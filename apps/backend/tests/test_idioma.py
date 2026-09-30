"""Cinco idiomas: cuál se habla en cada petición y cómo se escribe una cifra (D-147).

El idioma viaja en una variable de contexto que el middleware rellena con `?lang=` o con
`Accept-Language`. Lo que se ejecuta fuera de una petición —el renovador del
Diagnóstico— tiene que fijarlo él: si lo heredara del contexto por defecto, un
Diagnóstico pedido en inglés se renovaría en español.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient
from nodo import ejecutar

from laplace_backend import cifras, idioma

NBSP = " "
NNBSP = " "


# ---------------------------------------------------------------------------------
# Qué idioma
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cabecera", "esperado"),
    [
        (None, "es"),
        ("", "es"),
        ("en-US,en;q=0.9", "en"),
        ("pt-BR", "pt"),
        ("de-DE,fr;q=0.8,en;q=0.5", "fr"),  # el alemán no se habla: el siguiente
        ("en;q=0.2,zh-CN;q=0.9", "zh"),  # manda el peso, no el orden
        ("fr;q=0,en", "en"),  # q=0 es «no quiero»
        ("de-DE", "es"),
        ("*", "es"),
    ],
)
def test_negociar(cabecera, esperado):
    assert idioma.negociar(cabecera) == esperado


def test_usar_restaura_el_anterior():
    with idioma.usar("zh"):
        with idioma.usar("en"):
            assert idioma.actual() == "en"
        assert idioma.actual() == "zh"
    assert idioma.actual() == "es"


# ---------------------------------------------------------------------------------
# Cómo se escribe una cifra
# ---------------------------------------------------------------------------------

ESPERADO = {
    #       1234,5 US$                 0,0692 US$              0,934         1.234.567
    "es": (f"1.235{NBSP}US$", f"0,069{NBSP}US$", f"93{NBSP}%", "1.234.567"),
    "en": ("$1,235", "$0.069", "93%", "1,234,567"),
    "pt": (f"US${NBSP}1.235", f"US${NBSP}0,069", "93%", "1.234.567"),
    "fr": (f"1{NNBSP}235{NBSP}$US", f"0,069{NBSP}$US", f"93{NBSP}%", f"1{NNBSP}234{NNBSP}567"),
    "zh": ("US$1,235", "US$0.069", "93%", "1,234,567"),
}


@pytest.mark.parametrize("lengua", idioma.IDIOMAS)
def test_cada_idioma_escribe_sus_cifras(lengua):
    with idioma.usar(lengua):
        obtenido = (
            cifras.dinero(1234.5),
            cifras.dinero(0.0692),
            cifras.porcentaje(0.934),
            cifras.miles(1_234_567),
        )
    assert obtenido == ESPERADO[lengua]


def test_el_importe_y_su_simbolo_no_se_separan_de_renglon():
    """«14,64 / US$» partido en dos líneas no se lee como un importe."""
    for lengua in ("es", "pt", "fr"):
        with idioma.usar(lengua):
            assert " " not in cifras.dinero(14.64), lengua


def test_el_redondeo_va_hacia_arriba_en_el_empate():
    """Con `round()` —al par en el empate— un 1.234,50 salía «1.234» en una frase del
    backend y «1.235» en la tarjeta de la web, uno al lado del otro."""
    assert cifras.dinero(1234.5) == f"1.235{NBSP}US$"
    assert cifras.dinero(0.125) == f"0,13{NBSP}US$"
    # 1,005 no es un empate: en binario vale 1,00499999…, y así lo redondea `toFixed`.
    assert cifras.dinero(1.005) == f"1,00{NBSP}US$"


def test_el_signo_va_delante_de_todo():
    with idioma.usar("en"):
        assert cifras.dinero(-5) == "-$5.00"
    assert cifras.dinero(-5) == f"-5,00{NBSP}US$"
    # Lo que se redondea a cero no lleva signo: «-0 US$» no es una cifra.
    assert cifras.dinero(-0.0000001) == f"0{NBSP}US$"


#: Los casos del espejo con la web. Cubren cada tramo de `dinero`, el empate, los
#: negativos y los decimales que se quitan. `0.0045` y `0.0000035` son los que separan
#: redondear el valor binario exacto (0,004 y 0,000003, como `toFixed`) de redondear
#: `x · 10^d` (0,005 y 0,000004), que es lo que haría un `Math.round` a mano.
VALORES = [
    0, 0.0000009, 0.0000035, 0.0000371, 0.0042, 0.0045, 0.0692, 0.125, 0.4687, 0.5,
    1.005, 5, 99.99, 100, 1234.5, 987654.321, -5, -0.004, 2.5, 7.0, 0.934,
]


def _python(lengua: str) -> dict[str, list[str]]:
    with idioma.usar(lengua):
        return {
            "dinero": [cifras.dinero(v) for v in VALORES],
            "dinero_exacto": [cifras.dinero_exacto(v) for v in VALORES],
            "miles": [cifras.miles(v) for v in VALORES],
            "decimal": [cifras.decimal(v) for v in VALORES],
            "decimal3": [cifras.decimal(v, 3) for v in VALORES],
            "porcentaje": [cifras.porcentaje(v) for v in VALORES],
        }


def test_la_web_escribe_exactamente_lo_mismo_en_los_cinco_idiomas():
    """El espejo, de verdad: `format.ts` ejecutado con Node contra `cifras.py`.

    Antes esta prueba sólo podía comprobar la convención —que la web no usara `toFixed`
    a secas— porque no había forma de ejecutar la función de allí desde aquí. Una réplica
    en Python de la función de TypeScript no habría probado nada.
    """
    web = ejecutar(
        f"""
const f = await web("lib/format.ts");
const i = await web("lib/idioma.ts");
const valores = {VALORES};
const res = {{}};
for (const l of i.IDIOMAS) {{
  i.fijarIdioma(l);
  res[l] = {{
    dinero: valores.map((v) => f.money(v)),
    dinero_exacto: valores.map((v) => f.moneyExact(v)),
    miles: valores.map((v) => f.miles(v)),
    decimal: valores.map((v) => f.decimal(v)),
    decimal3: valores.map((v) => f.decimal(v, 3)),
    porcentaje: valores.map((v) => f.porcentaje(v)),
  }};
}}
salida(res);
"""
    )
    for lengua in idioma.IDIOMAS:
        esperado = _python(lengua)
        for funcion, lista in esperado.items():
            assert web[lengua][funcion] == lista, (lengua, funcion)


def test_las_duraciones_en_palabras_son_las_del_catalogo():
    web = ejecutar(
        """
const f = await web("lib/format.ts");
const i = await web("lib/idioma.ts");
const res = {};
for (const l of ["es", "en", "fr", "zh"]) {
  i.fijarIdioma(l);
  res[l] = [f.spanLabel(1), f.spanLabel(2.5), f.windowLabel(1 / 24), f.windowLabel(0.0001)];
}
salida(res);
"""
    )
    assert web["es"] == ["1 día", "2,5 días", "la última hora", "menos de un minuto"]
    assert web["en"] == ["1 day", "2.5 days", "the last hour", "less than a minute"]
    assert web["fr"] == ["1 jour", "2,5 jours", "la dernière heure", "moins d’une minute"]
    # El chino no tiene plural: `Intl.PluralRules` siempre dice «other».
    assert web["zh"] == ["1 天", "2.5 天", "过去 1 小时", "不到一分钟"]


# ---------------------------------------------------------------------------------
# La petición
# ---------------------------------------------------------------------------------


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_OVERVIEW_CACHE_S", "60")
    from laplace_backend import api, config, main

    config.get_settings.cache_clear()
    api.CACHE_DIAGNOSTICO.olvidar()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


def test_la_respuesta_dice_en_que_idioma_viene(cliente):
    r = cliente.get("/api/projects", headers={"Accept-Language": "pt-BR,pt;q=0.9"})
    assert r.headers["content-language"] == "pt"
    assert "Accept-Language" in r.headers["vary"]
    # Un enlace de un correo abre en el idioma del correo, diga lo que diga el navegador.
    r = cliente.get("/api/projects?lang=zh", headers={"Accept-Language": "en"})
    assert r.headers["content-language"] == "zh"
    assert cliente.get("/api/projects").headers["content-language"] == "es"


def test_el_endpoint_ve_el_idioma_de_la_peticion(cliente, monkeypatch):
    from laplace_backend import api

    vistos: list[str] = []
    original = api.overview

    def espia(*args, **kwargs):
        vistos.append(idioma.actual())
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "overview", espia)
    cliente.get("/api/overview?project_id=p", headers={"Accept-Language": "fr"})
    assert vistos == ["fr"]


def test_el_diagnostico_de_un_idioma_no_se_sirve_en_otro(cliente, monkeypatch):
    from laplace_backend import api

    vistos: list[str] = []
    original = api.overview

    def espia(*args, **kwargs):
        vistos.append(idioma.actual())
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "overview", espia)
    for lengua in ("en", "es", "en"):
        cliente.get("/api/overview?project_id=p", headers={"Accept-Language": lengua})
    assert vistos == ["en", "es"], "el segundo en inglés sale de la caché, el español no"


def test_el_renovador_recalcula_en_el_idioma_en_que_se_pidio(cliente, monkeypatch):
    """El renovador corre fuera de toda petición: sin fijarlo, renovaría en español un
    Diagnóstico que alguien está leyendo en chino."""
    from laplace_backend import api, cache_diagnostico

    cliente.get("/api/overview?project_id=p", headers={"Accept-Language": "zh"})
    vistos: list[str] = []
    original = api.overview

    def espia(*args, **kwargs):
        vistos.append(idioma.actual())
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "overview", espia)
    api.CACHE_DIAGNOSTICO._envejecer(50)
    assert asyncio.run(cache_diagnostico.una_vuelta(api.CACHE_DIAGNOSTICO, 60)) == 1
    assert vistos == ["zh"]


def test_los_avisos_salen_en_el_idioma_de_quien_los_configura(cliente, monkeypatch):
    """Se mandan desde un proceso de fondo, sin petición de la que sacar el idioma: se
    guarda el de quien configuró las alertas (D-148)."""
    from laplace_backend import main

    r = cliente.put(
        "/api/alert-settings",
        json={"project_id": "p", "generic_webhook_url": "https://127.0.0.1:9/avisos"},
        headers={"Accept-Language": "fr-FR"},
    )
    assert r.status_code == 200, r.text
    runner = main.app.state.alerts
    assert runner.config_for("p").language == "fr"

    enviados: list[str] = []
    monkeypatch.setattr(runner, "_enviar", lambda ajustes, texto: enviados.append(texto) or True)
    # Fuera de la petición, sin idioma en el contexto: el aviso sale en francés igual.
    assert idioma.actual() == "es"
    assert runner.send_test("p") is True
    assert "message de test" in enviados[0]


def test_un_tramo_de_un_dia_no_ensena_la_hora():
    """El pie del gráfico de gasto por día enseñaba «08 sept, 02:00» aunque el tramo fuera
    el día entero: la hora no significa nada ahí."""
    web = ejecutar(
        """
const f = await web("lib/format.ts");
const i = await web("lib/idioma.ts");
i.fijarIdioma("es");
const iso = "2026-09-08T12:00:00Z";
salida([f.inicioDeTramo(iso, 1440), f.inicioDeTramo(iso, 60)]);
"""
    )
    dia, hora = web
    assert ":" not in dia and "08" in dia
    assert ":" in hora
