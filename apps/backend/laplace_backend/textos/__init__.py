"""Las frases que redacta el backend, en el idioma de la petición (D-147, D-148).

El motor no escribe frases hechas: pide una clave con sus valores —`t("repeticion.titulo",
paso=..., veces=...)`— y la frase sale del catálogo del idioma actual (`idioma.actual()`).
`es.json` es el de origen y define las claves; `test_textos.py` exige que los otros cuatro
tengan las mismas, con los mismos huecos. Son JSON y no módulos: se editan y se revisan
como lo que son, textos, y cualquier herramienta de traducción los lee.

Las cifras que van dentro se escriben **antes**, con `cifras`, que ya sabe el idioma: aquí
sólo se rellenan huecos. Los plurales son dos claves, `x_one` y `x_other`, y se eligen con
las reglas de `Intl.PluralRules` —las mismas que usa la web—, para que una frase del
backend y la etiqueta de la tarjeta de al lado no discrepen en el singular.
"""

from __future__ import annotations

import json
import math
import string
from pathlib import Path
from typing import Any

from .. import idioma

_AQUI = Path(__file__).parent

CATALOGOS: dict[str, dict[str, str]] = {
    lengua: json.loads((_AQUI / f"{lengua}.json").read_text(encoding="utf-8"))
    for lengua in idioma.IDIOMAS
}

_FORMATO = string.Formatter()


def huecos(texto: str) -> set[str]:
    """Los nombres de los huecos de un texto: `{paso}` y `{veces}` → {"paso", "veces"}."""
    return {nombre for _, nombre, _, _ in _FORMATO.parse(texto) if nombre}


def t(clave: str, **valores: Any) -> str:
    """La frase de `clave` en el idioma actual, con sus huecos rellenos.

    Una clave que no exista es un error de programación y se dice alto: devolver la
    clave o el español escondería el fallo justo en el idioma que nadie del equipo lee.
    """
    return CATALOGOS[idioma.actual()][clave].format(**valores)


def forma_plural(n: float, lengua: str | None = None) -> str:
    """`one` u `other`, como `Intl.PluralRules(...).select(n)` en la web.

    Sólo las dos formas que usan los catálogos; lo que `Intl` llamaría `many` (el millón
    en español o francés) cae en `other`, que es lo que hace también `tn()` en la web.
    """
    lengua = lengua or idioma.actual()
    entero = math.floor(abs(n))
    es_entero = n == int(n)
    if lengua == "zh":
        return "other"
    if lengua in ("fr", "pt"):
        # CLDR: la parte entera 0 o 1 va en singular («0,5 jour», «1,5 dia»).
        return "one" if entero in (0, 1) else "other"
    if lengua == "en":
        return "one" if es_entero and entero == 1 else "other"
    return "one" if n == 1 else "other"  # español


def tn(base: str, cantidad: float, /, **valores: Any) -> str:
    """Singular o plural según `cantidad`. `{n}` es la cantidad tal cual, salvo que se
    pase ya escrita (`n=cifras.decimal(...)`), que es lo normal si lleva decimales."""
    catalogo = CATALOGOS[idioma.actual()]
    clave = f"{base}_{forma_plural(cantidad)}"
    texto = catalogo.get(clave, catalogo[f"{base}_other"])
    return texto.format(**{"n": cantidad, **valores})


__all__ = ["CATALOGOS", "forma_plural", "huecos", "t", "tn"]
