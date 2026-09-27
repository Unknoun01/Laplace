"""El único sitio que sabe escribir un número, y la prueba de que no hay un segundo.

Este producto se lee. Una cifra mal escrita no es un detalle de estilo: en la cabecera
de una traza convivían «120.255 / 108» —tokens, punto de millar—, «$0.007181» —dinero,
punto decimal— y «12,8 pasos por ejecución» —coma decimal—. Tres lecturas del mismo
carácter en la misma pantalla, en un producto cuyo argumento entero es que una cifra se
enseña con lo que haga falta para leerla bien.

No era un descuido en un sitio: había **tres** formateadores de dinero —uno en
`insights`, otro en `alerts` y otro en la web— y cada uno había elegido su convención.
Es la misma enfermedad que `pasos.py` curó para los nombres de paso: sin un sitio que
sepa hacerlo, cada pantalla se lo inventa.

La convención es una por idioma (D-147); aquí se prueba la española —punto para los
millares, coma para los decimales, «14,64 US$»— y en `test_idioma.py` las otras cuatro y
el espejo con la web. Lo que este fichero comprueba no es que esté bonito, es que no
vuelva a haber dos.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

from laplace_backend import alerts, cifras, insights, panel, prompts

NBSP = " "


def usd(cifra: str) -> str:
    """Un importe en dólares, escrito en español: «14,64 US$»."""
    return f"{cifra}{NBSP}US$"

#: La raíz del repositorio, para no depender de desde dónde se lance pytest.
_RAIZ = Path(__file__).resolve().parents[3]


def test_los_millares_van_con_punto():
    assert cifras.miles(12_345) == "12.345"
    assert cifras.miles(999) == "999"
    assert cifras.miles(1_000_000) == "1.000.000"


def test_los_decimales_van_con_coma():
    assert cifras.decimal(7.5) == "7,5"
    assert cifras.decimal(7.0) == "7", "el «,0» sobra"
    assert cifras.porcentaje(0.934) == f"93{NBSP}%"


def test_el_dinero_lleva_las_dos_cosas_a_la_vez():
    """Un importe grande necesita millares y uno pequeño necesita decimales, y los dos
    separadores tienen que poder convivir en la misma cifra sin ambigüedad."""
    assert cifras.dinero(1234.4) == usd("1.234")
    assert cifras.dinero(5.0) == usd("5,00")
    assert cifras.dinero(0.0042) == usd("0,0042")
    # Entre el céntimo y el dólar, dos cifras significativas: la tarjeta decía
    # «0,0692 US$ al mes», cuatro decimales que nadie lee.
    assert cifras.dinero(0.0692) == usd("0,069")
    assert cifras.dinero(0.4687) == usd("0,47")
    assert cifras.dinero(0.5) == usd("0,50"), "como la web: los céntimos de un precio se escriben"
    assert cifras.dinero(0.0000371) == usd("0,000037")
    assert cifras.dinero(0) == usd("0")


def test_por_debajo_del_centimo_no_se_redondea_a_cero():
    """La regla que hace falta en este producto y no en otros: el coste de un paso es
    minúsculo y el de un mes no. `$0,00` se lee como «no cuesta nada», que es justo la
    afirmación que D-073 y D-107 prohíben."""
    escrito = cifras.dinero(0.0000371)
    assert escrito != usd("0,00")
    cifra = escrito.removesuffix(NBSP + "US$")
    assert cifra.rstrip("0") == cifra, "y sin ceros de relleno a la derecha"


def test_una_cifra_de_dinero_nunca_lleva_punto_decimal():
    """El guardia. Un punto en un importe sólo puede ser separador de millar.

    Se recorre un abanico de magnitudes en vez de un par de casos, porque cada tramo de
    `dinero()` tiene su propia precisión y el fallo original vivía en uno solo de ellos.
    """
    for valor in (0.0000009, 0.000037, 0.0042, 0.5, 5.0, 99.99, 1234.5, 987654.0):
        escrito = cifras.dinero(valor)
        entero = escrito.removesuffix(NBSP + "US$").split(",")[0]
        assert re.fullmatch(r"\d{1,3}(\.\d{3})*", entero), (
            f"{valor} se escribe {escrito!r}: la parte entera no es un millar español"
        )


#: Los módulos que escriben frases con cifras dentro para que las lea una persona.
#: `insights` es un paquete (D-130): se miran todos sus módulos, no sólo `__init__`, que
#: no tiene código y dejaría al guardia sin mirar ninguna regla.
MODULOS_CON_TEXTO = (
    *(
        importlib.import_module(f"{insights.__name__}.{m.name}")
        for m in pkgutil.iter_modules(insights.__path__)
    ),
    alerts,
    panel,
    prompts,
)


def test_nadie_mas_formatea_dinero_por_su_cuenta():
    """Que la convención sea una no se sostiene si cada módulo se escribe la suya.

    Había tres. Este guardia lee el código de los módulos que redactan texto y prohíbe
    el patrón que las creó: un literal con el símbolo de moneda pegado a un formato de
    coma flotante. Si hace falta escribir dinero, se pide a `cifras.dinero`.
    """
    sospechosos: list[tuple[str, str]] = []
    patron = re.compile(r"\$\{[^}]*:[^}]*[fg]\}|\$\{[^}]*:,")
    for modulo in MODULOS_CON_TEXTO:
        fuente = inspect.getsource(modulo)
        for numero, linea in enumerate(fuente.splitlines(), 1):
            if patron.search(linea):
                sospechosos.append((f"{modulo.__name__}:{numero}", linea.strip()))
    assert sospechosos == [], (
        "estos sitios formatean dinero a mano en vez de pedírselo a `cifras.dinero`, "
        f"que es como acabamos con tres convenciones: {sospechosos}"
    )


def test_la_web_no_redondea_por_su_cuenta():
    """El espejo valor a valor está en `test_idioma.py`, que ejecuta `format.ts` con Node.
    Lo que queda aquí es el guardia del patrón que creó el problema: `toFixed` con
    decimales escribe siempre el punto inglés, sea cual sea el idioma de la página. Sólo
    `format.ts` puede usarlo, dentro de `fijo()`, que después pone los separadores.

    `toFixed(0)` no emite separador y se deja: prohibirlo sería ruido, y un guardia que
    salta donde no hay nada acaba silenciado.
    """
    culpables = [
        (fichero.as_posix(), numero, linea.strip())
        for fichero in (_RAIZ / "apps/web").rglob("*.ts*")
        if ".next" not in fichero.as_posix()
        and "node_modules" not in fichero.as_posix()
        and fichero.name != "format.ts"
        for numero, linea in enumerate(fichero.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\.toFixed\(\s*[1-9]", linea)
    ]
    assert culpables == [], (
        "`toFixed` con decimales escribe siempre el punto decimal inglés, sea cual sea "
        f"el idioma de la página: es el patrón que creó el problema. {culpables}"
    )
    formato = (_RAIZ / "apps/web/lib/format.ts").read_text(encoding="utf-8")
    assert formato.count(".toFixed(") == 1, "en format.ts, sólo dentro de `fijo()`"


def test_identificador_quita_el_llamante_y_la_pista():
    """El nombre de un paso en código no lleva la decoración del título."""
    from laplace_backend.pasos import identificador

    assert identificador("agente_de_equipaje → consultar_manual") == "consultar_manual"
    assert identificador("responder — “Responde usando el manual…”") == "responder"
    assert identificador("responder — «Responde usando el manual…»") == "responder"
    assert identificador("buscar_vuelos") == "buscar_vuelos"
    assert identificador("") == "paso"
