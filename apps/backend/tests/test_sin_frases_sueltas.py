"""Ninguna frase para el usuario escrita a mano en el código que redacta (D-148).

El motor habla cinco idiomas porque pide sus frases al catálogo (`textos`). Una frase
escrita a mano en un f-string sale en español aunque se haya pedido en chino, y ninguna
prueba en español lo nota. Este guardia recorre el código de los módulos que redactan y
falla con cualquier literal que parezca una frase: lo que no sea un docstring, un
mensaje de log, un error interno o SQL.

Leer el código en vez de ejecutarlo es a propósito: cubre todos los caminos, también
los que ningún proyecto de prueba recorre (una regla sin tarifa, un paso partido).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re

from laplace_backend import (
    alerts,
    coverage,
    dinero,
    evals,
    insights,
    panel,
    presupuesto,
    prompts,
    seguimiento,
)

MODULOS = (
    *(
        importlib.import_module(f"{insights.__name__}.{m.name}")
        for m in pkgutil.iter_modules(insights.__path__)
    ),
    alerts,
    coverage,
    dinero,
    evals,
    panel,
    presupuesto,
    prompts,
    seguimiento,
)

#: Dos palabras seguidas en minúscula: lo que distingue una frase de un nombre de campo.
FRASE = re.compile(r"[a-záéíóúñ]{2,} [a-záéíóúñ]{2,}", re.I)
SQL = re.compile(r"\b(SELECT|FROM|WHERE|GROUP BY|INSERT|CREATE TABLE|PRAGMA)\b")
#: Un fragmento de código de un arreglo propuesto: se copia tal cual, no se lee.
CODIGO = re.compile(r"^(from |import |@|def |for )")

#: Literales que parecen frase y no lo son para el usuario. Cerrada: una nueva se añade
#: aquí y se ve en el diff.
PERMITIDOS = {
    # Frases que `dinero.afirma_no_tener_tarifa` busca en las pruebas en español.
    "no esta en la tabla de precios",
    "no tiene tarifa conocida",
    "sin tarifa conocida",
    "ninguna de las",
    # Diagnóstico para quien opera la instalación (`laplace alerts --dry-run`, el log del
    # ciclo de avisos), no texto de ninguna pantalla.
    "tiene que ser https hacia un host público",
    "no se puede resolver",
    "no resuelve a ninguna dirección",
    "resuelve a una dirección no pública (",
    "silenciado o sin canal: ni webhook ni correo",
    "el envío ha fallado por todos los canales",
}


def _ignorables(arbol: ast.AST) -> set[int]:
    """Los nodos de texto que no son para el usuario: docstrings, logs, errores."""
    fuera: set[int] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            cuerpo = nodo.body
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant):
                fuera.add(id(cuerpo[0].value))
        llamada = None
        if isinstance(nodo, ast.Call):
            llamada = nodo
        elif isinstance(nodo, ast.Raise) and isinstance(nodo.exc, ast.Call):
            llamada = nodo.exc
        if llamada is None:
            continue
        funcion = llamada.func
        nombre = funcion.attr if isinstance(funcion, ast.Attribute) else getattr(funcion, "id", "")
        dueno = getattr(getattr(funcion, "value", None), "id", "")
        es_log = dueno in ("logger", "logging")
        es_error = isinstance(nodo, ast.Raise) or nombre.endswith(("Error", "Exception", "Unavailable"))
        if es_log or es_error:
            for hijo in ast.walk(llamada):
                fuera.add(id(hijo))
    return fuera


def _frases(modulo) -> list[str]:
    fuente = inspect.getsource(modulo)
    arbol = ast.parse(fuente)
    fuera = _ignorables(arbol)
    malas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Constant) or not isinstance(nodo.value, str):
            continue
        texto = nodo.value
        if id(nodo) in fuera or texto.strip() in PERMITIDOS:
            continue
        if SQL.search(texto) or CODIGO.match(texto) or not FRASE.search(texto):
            continue
        malas.append(f"{modulo.__name__}:{nodo.lineno}: {texto[:70]!r}")
    return malas


def test_ninguna_frase_escrita_a_mano():
    malas = [m for modulo in MODULOS for m in _frases(modulo)]
    assert malas == [], (
        "estas frases no pasan por el catálogo y saldrían en español en cualquier "
        "idioma; muévelas a `textos/*.json` y pídelas con t(): " + "\n".join(malas)
    )
