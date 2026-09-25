"""El único sitio que sabe escribir un número para que lo lea una persona.

Existe por lo mismo que `pasos.py`: sin un sitio que sepa hacerlo, cada pantalla se lo
inventa. Había **tres** formateadores de dinero —uno en `insights`, otro en `alerts` y
otro en la web— y cada uno había elegido su convención, así que en la cabecera de una
traza convivían «120.255 / 108», «$0.007181» y «12,8 pasos por ejecución»: tres lecturas
del mismo carácter en la misma pantalla (D-120).

**La convención es una: punto para los millares, coma para los decimales**, que es la
española, y el símbolo de moneda delante. La interfaz está en español y el `<html>` lo
declara; escribir los importes en inglés al lado de los tokens en español no es un
detalle de estilo, es pedirle al lector que adivine qué significa cada punto.

La precisión no es uniforme a propósito, y es la regla que este producto necesita y otros
no: **el coste de un paso es minúsculo y el de un mes no**. Redondear a dos decimales
convierte el coste de una llamada en `$0,00`, que se lee como «no cuesta nada» y es justo
la afirmación que D-073 y D-107 prohíben. Por eso los importes pequeños llevan más
decimales y los grandes ninguno.

`apps/web/lib/format.ts` tiene su copia porque corre en otro runtime (D-069), y
`test_cifras.py` comprueba que las dos escriben lo mismo: el resumen de un hallazgo lo
redacta el backend y el importe de su tarjeta lo pinta la web, y se leen uno al lado del
otro.
"""

from __future__ import annotations

#: Lo que separa los millares y lo que separa los decimales. Nombrados para que el día
#: que haya que localizar el producto se vea de dónde tira el hilo.
SEPARADOR_MILLAR = "."
SEPARADOR_DECIMAL = ","

SIMBOLOS = {"USD": "$", "EUR": "€"}


def _es(texto: str) -> str:
    """Pasa un número ya formateado del inglés al español.

    Se hace sobre el número solo y nunca sobre la frase entera: el atajo de formatear en
    inglés y después reemplazar en el texto completo también convertía en puntos las
    comas de las oraciones y las partía por la mitad.
    """
    return (
        texto.replace(",", "\x00")  # la coma inglesa es el millar
        .replace(".", SEPARADOR_DECIMAL)  # el punto inglés es el decimal
        .replace("\x00", SEPARADOR_MILLAR)
    )


def miles(n: float) -> str:
    """12345 -> «12.345»."""
    return _es(f"{n:,.0f}")


def decimal(value: float, decimales: int = 1) -> str:
    """Un número con decimales, coma española, sin los ceros que sobran al final."""
    texto = f"{value:,.{decimales}f}"
    if decimales:
        texto = texto.rstrip("0").rstrip(".")
    return _es(texto)


def porcentaje(ratio: float, decimales: int = 0) -> str:
    """0,934 -> «93 %». El espacio antes del símbolo es el uso español."""
    return f"{decimal(ratio * 100, decimales)} %"


def dinero(value: float, currency: str = "USD") -> str:
    """Un importe, con la precisión que su magnitud necesita.

    Espejo exacto de `money()` en `apps/web/lib/format.ts`. Los tramos:

    | Magnitud | Cómo se escribe | Por qué |
    |----------|-----------------|---------|
    | 0        | `$0`            | cero es cero, sin decorar |
    | ≥ 100    | `$1.234`        | los céntimos de una factura mensual son ruido |
    | ≥ 1      | `$5,00`         | dos decimales, como cualquier precio |
    | ≥ 0,1    | `$0,47`         | dos, como un precio                     |
    | ≥ 0,01   | `$0,069`        | tres: dos cifras que se leen, sin ruido |
    | < 0,01   | `$0,000037`     | seis: el coste de un paso vive aquí |
    """
    simbolo = SIMBOLOS.get(currency, f"{currency} ")
    magnitud = abs(value)
    if magnitud == 0:
        return f"{simbolo}0"
    if magnitud >= 100:
        return f"{simbolo}{miles(value)}"
    if magnitud >= 1:
        return f"{simbolo}{_es(f'{value:,.2f}')}"
    if magnitud >= 0.1:
        return f"{simbolo}{_es(f'{value:,.2f}')}"
    if magnitud >= 0.01:
        return f"{simbolo}{decimal(value, 3)}"
    return f"{simbolo}{decimal(value, 6)}"


def dinero_exacto(value: float, currency: str = "USD") -> str:
    """El mismo importe con todos sus decimales, para el texto que enseña la cuenta.

    `dinero()` escribe la cifra que se lee; ésta escribe la que se comprueba. La ficha
    de un hallazgo cuenta de dónde sale el ahorro —«15 vueltas de más en 3 trazas, que
    suman …»— y ahí redondear a cuatro decimales rompe la suma: el lector que eche las
    cuentas a mano no le cuadra, y este producto se apoya en que pueda hacerlo.
    """
    simbolo = SIMBOLOS.get(currency, f"{currency} ")
    return f"{simbolo}{decimal(value, 6)}"


__all__ = [
    "SEPARADOR_DECIMAL",
    "SEPARADOR_MILLAR",
    "decimal",
    "dinero",
    "dinero_exacto",
    "miles",
    "porcentaje",
]
