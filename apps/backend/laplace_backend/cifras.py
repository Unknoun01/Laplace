"""El único sitio que sabe escribir un número para que lo lea una persona.

Existe por lo mismo que `pasos.py`: sin un sitio que sepa hacerlo, cada pantalla se lo
inventa. Había **tres** formateadores de dinero —uno en `insights`, otro en `alerts` y
otro en la web— y cada uno había elegido su convención, así que en la cabecera de una
traza convivían «120.255 / 108», «$0.007181» y «12,8 pasos por ejecución»: tres lecturas
del mismo carácter en la misma pantalla (D-120).

**La convención es una por idioma** (D-147), y la del idioma de la petición
(`idioma.actual()`) vale para todas las cifras de la respuesta: escribir los importes en
inglés al lado de los tokens en español no es un detalle de estilo, es pedirle al lector
que adivine qué significa cada punto. En español, punto para los millares, coma para los
decimales y «14,64 US$»; en inglés, «$14.64»; en francés, espacio fino para los millares
y «14,64 $US».

La precisión no es uniforme a propósito, y es la regla que este producto necesita y otros
no: **el coste de un paso es minúsculo y el de un mes no**. Redondear a dos decimales
convierte el coste de una llamada en `$0,00`, que se lee como «no cuesta nada» y es justo
la afirmación que D-073 y D-107 prohíben. Por eso los importes pequeños llevan más
decimales y los grandes ninguno.

`apps/web/lib/format.ts` tiene su copia porque corre en otro runtime (D-069), y
`test_cifras.py` la ejecuta con Node y comprueba, valor a valor y en los cinco idiomas,
que las dos escriben lo mismo: el resumen de un hallazgo lo redacta el backend y el
importe de su tarjeta lo pinta la web, y se leen uno al lado del otro.

El redondeo es el mismo en los dos: el valor binario exacto, y en empate hacia arriba.
Es lo que hace `toFixed` en JavaScript y `Decimal(x).quantize(..., ROUND_HALF_UP)` aquí.
Con el `round()` de Python —que en empate va al par— «1.234,50 US$» salía «1.234» en una
frase y «1.235» en la tarjeta de al lado.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from . import idioma

#: Espacio que no parte línea, entre la cifra y su unidad: «14,64 / US$» partido en dos
#: renglones no se lee como un importe.
NBSP = " "
#: El espacio fino que el francés usa para los millares.
NNBSP = " "


@dataclass(frozen=True)
class Convencion:
    millar: str
    decimal: str
    #: Cómo va un importe: `{n}` es la cifra y `{s}` el símbolo.
    dinero: str
    #: Símbolo de cada moneda en este idioma. La que no esté, por su código.
    simbolos: dict[str, str]
    #: Cómo va un porcentaje.
    porcentaje: str


CONVENCIONES: dict[str, Convencion] = {
    "es": Convencion(
        ".", ",", "{n}" + NBSP + "{s}", {"USD": "US$", "EUR": "€"}, "{n}" + NBSP + "%"
    ),
    "en": Convencion(",", ".", "{s}{n}", {"USD": "$", "EUR": "€"}, "{n}%"),
    "pt": Convencion(".", ",", "{s}" + NBSP + "{n}", {"USD": "US$", "EUR": "€"}, "{n}%"),
    "fr": Convencion(
        NNBSP, ",", "{n}" + NBSP + "{s}", {"USD": "$US", "EUR": "€"}, "{n}" + NBSP + "%"
    ),
    "zh": Convencion(",", ".", "{s}{n}", {"USD": "US$", "EUR": "€"}, "{n}%"),
}

#: Lo que separa los decimales en español. Queda por compatibilidad: lo que dependa del
#: idioma tiene que leer `convencion()`.
SEPARADOR_DECIMAL = CONVENCIONES["es"].decimal
SEPARADOR_MILLAR = CONVENCIONES["es"].millar


def convencion() -> Convencion:
    return CONVENCIONES[idioma.actual()]


def _fijo(valor: float, decimales: int) -> str:
    """|valor| con `decimales` decimales, en cifras inglesas y sin agrupar.

    Redondea el valor binario exacto y, en empate, hacia arriba: lo mismo que `toFixed`,
    para que la web y el backend no se separen nunca en el último dígito.
    """
    cuanto = Decimal(1).scaleb(-decimales)
    return str(Decimal(abs(valor)).quantize(cuanto, rounding=ROUND_HALF_UP))


def _local(fijo: str, negativo: bool, quitar_ceros: bool) -> str:
    """Un número de `_fijo` escrito con los separadores del idioma."""
    c = convencion()
    entero, _, fraccion = fijo.partition(".")
    if quitar_ceros:
        fraccion = fraccion.rstrip("0")
    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    texto = c.millar.join(grupos) + (c.decimal + fraccion if fraccion else "")
    return ("-" if negativo and texto.strip("0.,") else "") + texto


def _numero(valor: float, decimales: int, quitar_ceros: bool) -> str:
    return _local(_fijo(valor, decimales), valor < 0, quitar_ceros)


def miles(n: float) -> str:
    """12345 -> «12.345» (en español)."""
    return _numero(n, 0, False)


def decimal(value: float, decimales: int = 1) -> str:
    """Un número con decimales, sin los ceros que sobran al final."""
    return _numero(value, decimales, True)


def porcentaje(ratio: float, decimales: int = 0) -> str:
    """0,934 -> «93 %» en español, «93%» en inglés."""
    return convencion().porcentaje.format(n=decimal(ratio * 100, decimales))


def _con_simbolo(cifra: str, currency: str) -> str:
    c = convencion()
    negativo = cifra.startswith("-")
    simbolo = c.simbolos.get(currency, currency)
    texto = c.dinero.format(n=cifra.lstrip("-"), s=simbolo)
    return ("-" if negativo else "") + texto


def dinero(value: float, currency: str = "USD") -> str:
    """Un importe, con la precisión que su magnitud necesita.

    Espejo exacto de `money()` en `apps/web/lib/format.ts`. Los tramos (en español):

    | Magnitud | Cómo se escribe | Por qué |
    |----------|-----------------|---------|
    | 0        | `0 US$`         | cero es cero, sin decorar |
    | ≥ 100    | `1.234 US$`     | los céntimos de una factura mensual son ruido |
    | ≥ 1      | `5,00 US$`      | dos decimales, como cualquier precio |
    | ≥ 0,1    | `0,47 US$`      | dos, como un precio                     |
    | ≥ 0,01   | `0,069 US$`     | tres: dos cifras que se leen, sin ruido |
    | < 0,01   | `0,000037 US$`  | seis: el coste de un paso vive aquí |
    """
    magnitud = abs(value)
    if magnitud == 0:
        cifra = "0"
    elif magnitud >= 100:
        cifra = _numero(value, 0, False)
    elif magnitud >= 0.1:
        cifra = _numero(value, 2, False)
    elif magnitud >= 0.01:
        cifra = _numero(value, 3, True)
    else:
        cifra = _numero(value, 6, True)
    return _con_simbolo(cifra, currency)


def dinero_exacto(value: float, currency: str = "USD") -> str:
    """El mismo importe con todos sus decimales, para el texto que enseña la cuenta.

    `dinero()` escribe la cifra que se lee; ésta escribe la que se comprueba. La ficha
    de un hallazgo cuenta de dónde sale el ahorro —«15 vueltas de más en 3 trazas, que
    suman …»— y ahí redondear a cuatro decimales rompe la suma: el lector que eche las
    cuentas a mano no le cuadra, y este producto se apoya en que pueda hacerlo.
    """
    return _con_simbolo(decimal(value, 6), currency)


__all__ = [
    "CONVENCIONES",
    "SEPARADOR_DECIMAL",
    "SEPARADOR_MILLAR",
    "convencion",
    "decimal",
    "dinero",
    "dinero_exacto",
    "miles",
    "porcentaje",
]
