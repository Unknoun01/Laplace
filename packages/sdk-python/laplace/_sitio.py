"""Desde qué fichero y qué línea del código del usuario se llama al modelo.

`step_site` dice desde qué **paso** se llama (`atender_ticket > resumir_para_crm`), que
es lo que agrupa las llamadas. Para proponer un arreglo en el código hace falta además
el sitio exacto: el fichero y la línea de la llamada (D-185). Se anotan con los nombres
de las convenciones de OpenTelemetry (`code.file.path`, `code.line.number`,
`code.function.name`), y la ingesta los guarda tal cual entre los atributos del span.

La ruta nunca es absoluta: diría el usuario y la máquina de quien lo ejecuta, y no le
sirve a nadie. Es relativa a la raíz del repositorio git si se encuentra; si no (un
contenedor sin `.git`), al directorio de trabajo; y si el fichero cae fuera de los dos,
los últimos tramos de la ruta. Quien la lee (el bot de PR) la busca en el árbol del
repositorio por sufijo, así que las tres le sirven.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
import sysconfig
from pathlib import Path

#: Tramos que se dejan de una ruta que no está ni en el repositorio ni bajo el
#: directorio de trabajo: bastan para encontrarla por sufijo y no dicen de quién es.
TRAMOS_SUELTOS = 3

_PAQUETE = str(Path(__file__).resolve().parent)


@functools.lru_cache(maxsize=1)
def _ajenos() -> tuple[str, ...]:
    """Directorios que no son código del usuario: la biblioteca estándar y lo instalado."""
    rutas = {_PAQUETE}
    for clave in ("stdlib", "platstdlib", "purelib", "platlib"):
        ruta = sysconfig.get_paths().get(clave)
        if ruta:
            rutas.add(str(Path(ruta).resolve()))
    return tuple(sorted(rutas))


@functools.lru_cache(maxsize=1024)
def _es_ajeno(fichero: str) -> bool:
    if not fichero or fichero.startswith("<"):
        return True  # <frozen …>, <string>, <stdin>: no hay fichero que arreglar
    try:
        ruta = str(Path(fichero).resolve())
    except OSError:
        return True
    partes = Path(ruta).parts
    if "site-packages" in partes or "dist-packages" in partes:
        return True
    return any(ruta == base or ruta.startswith(base + os.sep) for base in _ajenos())


@functools.lru_cache(maxsize=256)
def _raiz_git(directorio: str) -> str | None:
    actual = Path(directorio)
    for candidato in (actual, *actual.parents):
        if (candidato / ".git").exists():
            return str(candidato)
    return None


@functools.lru_cache(maxsize=1024)
def ruta_relativa(fichero: str) -> str:
    """La ruta que se anota: del repositorio, del directorio de trabajo o un sufijo."""
    ruta = Path(fichero).resolve()
    bases = [_raiz_git(str(ruta.parent))]
    try:
        bases.append(str(Path.cwd().resolve()))
    except OSError:
        pass
    for base in bases:
        if base is None:
            continue
        try:
            return ruta.relative_to(base).as_posix()
        except ValueError:
            continue
    return "/".join(ruta.parts[-TRAMOS_SUELTOS:])


def de_funcion(fn: object) -> tuple[str, int, str] | None:
    """Dónde está definida una función del usuario: (ruta, primera línea, nombre).

    La primera línea es la de `co_firstlineno`, que según la versión de Python es la del
    `def` o la del primer decorador. Para el tope de vueltas del bot de PR (D-190).
    """
    codigo = getattr(inspect.unwrap(fn), "__code__", None)  # type: ignore[arg-type]
    if codigo is None or _es_ajeno(codigo.co_filename):
        return None
    return ruta_relativa(codigo.co_filename), codigo.co_firstlineno, codigo.co_name


def sitio_llamada() -> tuple[str, int, str] | None:
    """El primer marco de la pila que es código del usuario: (ruta, línea, función).

    Recorrer la pila cuesta microsegundos por llamada al modelo, que tarda segundos; lo
    que se cachea es lo caro, decidir si un fichero es ajeno y a qué raíz pertenece.
    """
    marco = sys._getframe(1)
    while marco is not None:
        fichero = marco.f_code.co_filename
        if not _es_ajeno(fichero):
            return ruta_relativa(fichero), marco.f_lineno, marco.f_code.co_name
        marco = marco.f_back
    return None
