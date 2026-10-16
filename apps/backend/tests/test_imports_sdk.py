"""Entre los módulos del SDK no hay ciclos de imports, ni siquiera tardíos (D-202).

Un import dentro de una función sólo aplaza el ciclo: el día que alguien lo sube a la
cabecera, el paquete deja de importarse. Por eso el grafo cuenta todos los imports del
fichero, estén donde estén, y lo que implica importar un submódulo: que se ejecute
antes el `__init__` de cada paquete por el que pasa, salvo los que ya contienen al que
importa (ésos están a medio cargar y no se vuelven a ejecutar).

`from . import semconv` es un submódulo, no algo que se tome del `__init__`; pedir un
nombre al paquete (`from . import init`) sí depende del `__init__`.
"""

from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

import laplace

RAIZ = Path(laplace.__file__).parent


def _modulos() -> dict[str, tuple[Path, bool]]:
    modulos = {}
    for fichero in RAIZ.rglob("*.py"):
        partes = fichero.relative_to(RAIZ.parent).with_suffix("").parts
        es_paquete = partes[-1] == "__init__"
        modulos[".".join(partes[:-1] if es_paquete else partes)] = (fichero, es_paquete)
    return modulos


def _contiene(paquete: str, modulo: str) -> bool:
    return modulo == paquete or modulo.startswith(paquete + ".")


def grafo() -> dict[str, set[str]]:
    modulos = _modulos()
    aristas: dict[str, set[str]] = {nombre: set() for nombre in modulos}
    for nombre, (fichero, es_paquete) in modulos.items():
        paquete = nombre if es_paquete else nombre.rpartition(".")[0]
        destinos: set[str] = set()
        # El SDK no usa `if TYPE_CHECKING:`; si llegara, sus imports contarían igual.
        for nodo in ast.walk(ast.parse(fichero.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    partes = alias.name.split(".")
                    candidatos = [".".join(partes[:i]) for i in range(len(partes), 0, -1)]
                    destinos.update(next(([c] for c in candidatos if c in modulos), []))
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level:
                    base = paquete.rsplit(".", nodo.level - 1)[0] if nodo.level > 1 else paquete
                    base = f"{base}.{nodo.module}" if nodo.module else base
                else:
                    base = nodo.module or ""
                if base not in modulos:
                    continue
                for alias in nodo.names:
                    sub = f"{base}.{alias.name}"
                    destinos.add(sub if sub in modulos else base)
        for destino in destinos - {nombre}:
            aristas[nombre].add(destino)
            # Importar `a.b.c` ejecuta antes `a` y `a.b`, si no están ya cargándose.
            partes = destino.split(".")
            for i in range(1, len(partes)):
                padre = ".".join(partes[:i])
                if not _contiene(padre, nombre):
                    aristas[nombre].add(padre)
    return aristas


def test_el_grafo_ve_los_imports_tardios_y_los_submodulos():
    g = grafo()
    assert "laplace._tracer" in g["laplace.decorators"]
    # Dentro de una función, en `_common`: también cuenta.
    assert "laplace.prompts" in g["laplace.integrations._common"]
    # `from . import _common` es el submódulo, no el `__init__` de integraciones.
    assert "laplace.integrations" not in g["laplace.integrations._streaming"]
    # Pasar por `laplace.integrations._common` ejecuta el `__init__` de integraciones.
    assert "laplace.integrations" in g["laplace.manual"]
    # Pedir un nombre al paquete depende de su `__init__`.
    assert "laplace" in g["laplace.cli"]


def test_no_hay_ciclos_de_imports_en_el_sdk():
    try:
        TopologicalSorter(grafo()).prepare()
    except CycleError as e:
        # graphlib da el ciclo de importado a importador; se lee mejor al revés.
        ciclo = " -> ".join(reversed(e.args[1]))
        raise AssertionError(f"ciclo de imports en el SDK: {ciclo}") from None
