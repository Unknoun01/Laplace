"""Pruebas de la tabla de precios.

Todo el producto se vende con una cifra en dólares. Si esa cifra está mal, no hay
producto: estas pruebas existen para que un precio equivocado rompa la suite en lugar
de llegar a la pantalla de alguien.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from laplace_backend.config import Settings
from laplace_backend.pricing import PriceTable, get_price_table
from laplace_backend.storage.base import Window
from laplace_backend.storage.clickhouse import ClickHouseStore

PRICES_PATH = Path(__file__).parents[1] / "laplace_backend" / "pricing" / "model_prices.json"
#: A partir de aquí, la tabla se considera caducada y hay que reverificarla.
#:
#: Treinta días, no un trimestre: los proveedores mueven precios mucho más rápido que
#: eso. En julio de 2026 OpenAI recortó un modelo un 20 % y otro un 80 % en el mismo
#: día. Una tabla de tres meses miente con toda naturalidad y nadie se entera hasta que
#: un usuario compara nuestra cifra con su factura.
MAX_AGE_DAYS = 30


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(PRICES_PATH.read_text(encoding="utf-8"))


def test_la_tabla_declara_version_y_fuentes(raw):
    assert raw.get("version"), "la tabla necesita versión para poder auditar un cálculo"
    assert raw.get("sources"), "sin fuentes, los precios son una afirmación sin respaldo"
    for key, source in raw["sources"].items():
        assert source["url"].startswith("https://"), key
        date.fromisoformat(source["verified_at"])  # revienta si el formato es otro


def test_cada_modelo_declara_de_donde_sale_su_precio(raw):
    fuentes = set(raw["sources"])
    for name, entry in raw["models"].items():
        assert entry.get("source") in fuentes, f"{name} no dice de qué fuente sale"
        assert entry.get("input") is not None and entry.get("output") is not None, name


def test_las_alternativas_existen_y_son_mas_baratas(raw):
    """Proponer un modelo que no está en la tabla, o que cuesta más, sería un ridículo."""
    tabla = get_price_table()
    for name, entry in raw["models"].items():
        alternativa = entry.get("alternative")
        if not alternativa:
            continue
        barato = tabla.lookup(alternativa)
        assert barato is not None, f"{name} propone {alternativa}, que no está en la tabla"
        caro = tabla.lookup(name)
        assert barato.output < caro.output, f"{alternativa} no es más barato que {name}"


def test_la_tabla_no_esta_caducada(raw):
    """Los proveedores cambian precios. Una tabla vieja miente con toda naturalidad."""
    viejas = get_price_table().stale_sources(MAX_AGE_DAYS)
    assert not viejas, (
        "estas fuentes llevan más de "
        f"{MAX_AGE_DAYS} días sin reverificar: {viejas}. Hay que volver a "
        f"{[raw['sources'][k]['url'] for k in viejas]} y subir 'version'."
    )


def test_ninguna_tarifa_promocional_sigue_aplicandose_vencida(raw):
    """Una promoción caducada es un precio inventado con fecha de caducidad pasada."""
    vencidas = get_price_table().expired_rates()
    assert not vencidas, (
        f"estas tarifas eran promocionales y ya vencieron: {vencidas}. Mientras no se "
        f"revisen, todo coste que las use sale marcado como no fiable."
    )


def test_ningun_sufijo_de_palabra_cambia_de_modelo_a_espaldas_del_precio():
    """La auditoría de identificadores, convertida en test permanente.

    Los proveedores llaman `-pro`, `-mini`, `-nano` o `-cyber` a modelos **distintos**,
    con precios que se van a seis veces, y `-latest` o `-preview` al **mismo** modelo.
    Mientras el sufijo de palabra que aceptamos sea sólo el segundo grupo, un modelo
    nuevo que no esté en la tabla saldrá como «coste desconocido» en vez de heredar en
    silencio una tarifa que no le toca.

    Los snapshots con fecha son otra cosa y sí pueden costar distinto: OpenAI mantiene
    `gpt-4o-2024-05-13` al precio viejo. Por eso están fuera de esta comprobación: si
    la entrada exacta existe, gana; si no, heredar del modelo base es lo correcto.
    """
    from laplace_backend.pricing import _SNAPSHOT_SUFFIX

    tabla = get_price_table()
    modelos = tabla.models
    for largo, precio_largo in modelos.items():
        for corto, precio_corto in modelos.items():
            if corto == largo or not largo.startswith(corto):
                continue
            sufijo = largo[len(corto) :]
            if not _SNAPSHOT_SUFFIX.match(sufijo) or re.match(r"^-[\dv]", sufijo):
                continue
            assert (precio_largo.input, precio_largo.output) == (
                precio_corto.input,
                precio_corto.output,
            ), (
                f"«{largo}» heredaría de «{corto}» por un sufijo de palabra pero cuesta "
                f"distinto: o no es el mismo modelo, o la tabla está mal"
            )


# ---------------------------------------------------------------------------------
# Resolución de identificadores
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identificador", "esperado"),
    [
        ("gpt-4o", "gpt-4o"),
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),  # snapshot con fecha
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("openai/gpt-4o", "gpt-4o"),  # prefijo de gateway
        ("anthropic.claude-sonnet-5", "claude-sonnet-5"),
        ("bedrock/anthropic.claude-sonnet-5-v1:0", "claude-sonnet-5"),
        ("gpt-5.1", "gpt-5.1"),  # el punto es del modelo, no un separador
    ],
)
def test_identificadores_reales_se_resuelven(identificador, esperado):
    price = get_price_table().lookup(identificador)
    assert price is not None, identificador
    assert price.model == esperado


@pytest.mark.parametrize("identificador", ["gpt-5.7", "claude-opus-9", "modelo-inventado-7b", ""])
def test_lo_que_no_conocemos_no_se_resuelve_por_las_bravas(identificador):
    assert get_price_table().lookup(identificador) is None


def test_una_version_nueva_no_hereda_la_tarifa_de_la_anterior():
    """`claude-opus-4-5` cuesta un tercio que `claude-opus-4`.

    Con la resolución por prefijo ingenua, un usuario de Opus 4.5 habría visto el triple
    de coste sin que nada lo avisara. Es el error más caro que puede cometer esta tabla.
    """
    tabla = get_price_table()
    assert tabla.lookup("claude-opus-4-5").model == "claude-opus-4-5"
    assert tabla.lookup("claude-opus-4-5").input == 5.0
    assert tabla.lookup("claude-opus-4").input == 15.0


def test_un_modelo_sin_tarifa_devuelve_desconocido_no_cero():
    resultado = get_price_table().compute(
        "modelo-inventado-7b", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert resultado.unknown is True
    assert resultado.rate == ""


def test_el_calculo_anota_la_tarifa_que_uso():
    resultado = get_price_table().compute("gpt-4o", input_tokens=1_000_000, output_tokens=0)
    assert resultado.unknown is False
    assert resultado.total_usd == pytest.approx(2.50)
    assert resultado.rate == f"gpt-4o @ {get_price_table().version}"


def test_los_tokens_cacheados_se_cobran_a_su_tarifa():
    tabla = get_price_table()
    caro = tabla.compute("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    barato = tabla.compute(
        "claude-sonnet-5", input_tokens=1_000_000, output_tokens=0, cached_input_tokens=1_000_000
    )
    assert caro.total_usd == pytest.approx(2.0)
    assert barato.total_usd == pytest.approx(0.20)


# ---------------------------------------------------------------------------------
# Metros de facturación
# ---------------------------------------------------------------------------------


def test_la_cache_abarata_la_misma_llamada():
    """La misma entrada, con y sin caché, no puede costar lo mismo.

    Es la prueba que le faltaba al motor: un agente repite su prompt de sistema en cada
    paso, así que el caché salta siempre. Cobrando esa entrada al 100 % estábamos
    inflando la factura de todos nuestros usuarios.
    """
    tabla = get_price_table()
    sin_cache = tabla.compute("claude-sonnet-5", input_tokens=100_000, output_tokens=0)
    con_cache = tabla.compute(
        "claude-sonnet-5",
        input_tokens=100_000,
        output_tokens=0,
        cached_input_tokens=90_000,
    )
    # 100k a 2 $/1M.
    assert sin_cache.total_usd == pytest.approx(0.20)
    # 10k a 2 $/1M + 90k a 0,20 $/1M.
    assert con_cache.total_usd == pytest.approx(0.02 + 0.018)
    assert con_cache.cache_read_usd == pytest.approx(0.018)
    assert con_cache.cache_saving_usd == pytest.approx(90_000 * (2.0 - 0.2) / 1e6)
    assert con_cache.total_usd < sin_cache.total_usd


def test_escribir_en_cache_cuesta_mas_que_la_entrada_normal():
    """La escritura no es gratis: 1,25x en caché corta y 2x en la de una hora."""
    tabla = get_price_table()
    normal = tabla.compute("claude-opus-5", input_tokens=100_000, output_tokens=0)
    corta = tabla.compute(
        "claude-opus-5", input_tokens=100_000, output_tokens=0, cache_write_tokens=100_000
    )
    larga = tabla.compute(
        "claude-opus-5", input_tokens=100_000, output_tokens=0, cache_write_1h_tokens=100_000
    )
    assert normal.total_usd == pytest.approx(0.5)
    assert corta.total_usd == pytest.approx(0.625)
    assert larga.total_usd == pytest.approx(1.0)
    assert corta.cache_write_usd == pytest.approx(0.625)


def test_los_tramos_de_cache_salen_del_total_de_entrada():
    """Los tokens de caché van DENTRO de `input_tokens`, no se suman aparte.

    Si se sumaran, el mismo agente costaría distinto según el proveedor: OpenAI ya los
    incluye en `prompt_tokens` y Anthropic los devuelve por separado.
    """
    tabla = get_price_table()
    resultado = tabla.compute(
        "claude-sonnet-5",
        input_tokens=100_000,
        output_tokens=0,
        cached_input_tokens=60_000,
        cache_write_tokens=20_000,
    )
    # 20k normales a 2 $ + 60k leídos a 0,20 $ + 20k escritos a 2,50 $.
    esperado = (20_000 * 2.0 + 60_000 * 0.2 + 20_000 * 2.5) / 1e6
    assert resultado.total_usd == pytest.approx(esperado)


def test_el_api_de_lotes_aplica_su_descuento():
    tabla = get_price_table()
    normal = tabla.compute("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    lote = tabla.compute(
        "claude-sonnet-5", input_tokens=1_000_000, output_tokens=0, tier="batch"
    )
    assert lote.total_usd == pytest.approx(normal.total_usd * 0.5)
    assert lote.assumed is False


def test_el_modo_rapido_usa_la_tarifa_publicada_del_modelo():
    tabla = get_price_table()
    rapido = tabla.compute("claude-opus-5", input_tokens=1_000_000, output_tokens=0, tier="fast")
    assert rapido.total_usd == pytest.approx(10.0)
    assert rapido.assumed is False


def test_un_nivel_de_servicio_sin_tarifa_no_se_cobra_como_si_fuera_el_normal():
    """`flex` y compañía existen y no tenemos su precio. Se dice, no se disimula."""
    tabla = get_price_table()
    resultado = tabla.compute("gpt-5.6-terra", input_tokens=1_000, output_tokens=0, tier="flex")
    assert resultado.assumed is True
    assert "modo rápido" in resultado.note or "lotes" in resultado.note or resultado.note


def test_la_residencia_de_datos_recarga_solo_donde_esta_publicada():
    tabla = get_price_table()
    con_recargo = tabla.compute(
        "claude-sonnet-5", input_tokens=1_000_000, output_tokens=0, region="regional"
    )
    assert con_recargo.total_usd == pytest.approx(2.2)
    assert con_recargo.assumed is False

    # Opus 4.5 no admite `inference_geo`: pedirlo no puede inventarle un recargo.
    sin_soporte = tabla.compute(
        "claude-opus-4-5", input_tokens=1_000_000, output_tokens=0, region="regional"
    )
    assert sin_soporte.total_usd == pytest.approx(5.0)
    assert sin_soporte.assumed is True


def test_el_contexto_largo_se_avisa_pero_no_se_cobra_por_nuestra_cuenta():
    """El proveedor publica el tramo pero no dónde empieza. Inventarlo sería mentir.

    Y además mentiría en nuestro favor: cobrar el tramo caro engordaría el ahorro que
    anunciamos. Se cobra el estándar y se avisa de que el coste puede quedarse corto.
    """
    tabla = get_price_table()
    corto = tabla.compute("gpt-5.6-terra", input_tokens=1_000, output_tokens=0)
    largo = tabla.compute("gpt-5.6-terra", input_tokens=300_000, output_tokens=0)
    assert corto.assumed is False
    assert largo.assumed is True
    assert "contexto largo" in largo.note
    # La tarifa aplicada sigue siendo la estándar, no la del tramo largo.
    assert largo.total_usd == pytest.approx(300_000 * 2.0 / 1e6)


def test_una_tarifa_promocional_vencida_marca_el_coste_como_no_fiable(tmp_path):
    caducada = tmp_path / "caducada.json"
    caducada.write_text(
        json.dumps(
            {
                "version": "prueba",
                "sources": {"x": {"url": "https://x", "verified_at": "2026-09-01"}},
                "models": {
                    "modelo-promo": {
                        "input": 1.0,
                        "output": 1.0,
                        "expires": "2026-01-01",
                        "source": "x",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    tabla = PriceTable.load(caducada)
    resultado = tabla.compute("modelo-promo", input_tokens=1_000_000, output_tokens=0)
    assert resultado.assumed is True
    assert "promocional" in resultado.note
    assert tabla.expired_rates() == ["modelo-promo"]


def test_una_tabla_ilegible_no_tumba_el_proceso(tmp_path):
    rota = tmp_path / "rota.json"
    rota.write_text("{ esto no es json", encoding="utf-8")
    tabla = PriceTable.load(rota)
    assert tabla.models == {}
    # Y todo pasa a ser desconocido, que es lo correcto: sin tabla no sabemos nada.
    assert tabla.compute("gpt-4o", input_tokens=100, output_tokens=100).unknown is True


# ---------------------------------------------------------------------------------
# Contra los datos que hay de verdad
# ---------------------------------------------------------------------------------


def test_ningun_modelo_de_las_trazas_se_queda_sin_tarifa():
    """Si aparece un modelo sin precio, sus costes son desconocidos y los totales cojean.

    Este test es el que avisa de que hay que añadir una entrada a la tabla, en lugar de
    descubrirlo porque un usuario ve un total que no cuadra con su factura.
    """
    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")

    ahora = datetime.now(timezone.utc)
    huerfanos: dict[str, int] = {}
    for project in store.list_projects():
        resumen = store.summarize_window(
            project.project_id,
            Window(since=ahora - timedelta(days=90), until=ahora, days=90),
        )
        for model in resumen.models_without_price:
            huerfanos[model] = huerfanos.get(model, 0) + resumen.unknown_cost_spans

    assert not huerfanos, (
        "estos modelos aparecen en las trazas y no están en model_prices.json, así que "
        f"su coste es desconocido: {sorted(huerfanos)}"
    )

def test_la_escritura_de_cache_lleva_su_recargo_en_los_dos_proveedores():
    """Las cifras verificadas: OpenAI cobra la escritura a 1,25x la entrada desde la
    familia GPT-5.6; Anthropic, 1,25x la de cinco minutos y 2x la de una hora.

    Se comprueba sobre la tabla entera y no sobre un modelo suelto porque el fallo que
    esto evita no es «este modelo está mal», es «alguien añadió un modelo nuevo y se
    dejó el recargo», que sólo se ve mirándolos todos.
    """
    tabla = get_price_table()
    for nombre, precio in tabla.models.items():
        if precio.cache_write is None:
            continue  # el proveedor no cobra aparte por escribir: va a tarifa de entrada
        assert precio.cache_write == pytest.approx(precio.input * 1.25), nombre
        if precio.cache_write_1h is not None:
            assert precio.cache_write_1h == pytest.approx(precio.input * 2.0), nombre

    # Y que la familia actual de cada proveedor lo tiene puesto de verdad, no que la
    # comprobación de arriba pase por no haber ninguno.
    assert tabla.models["gpt-5.6-luna"].cache_write is not None
    assert tabla.models["claude-haiku-4-5"].cache_write_1h is not None


def test_openai_tambien_cobra_la_escritura_de_cache():
    """La contraparte de OpenAI del test de arriba, que sólo miraba Anthropic.

    Cobra 1,25x la entrada desde la familia GPT-5.6. El motor ya sabía aplicarlo; lo que
    faltaba —y por lo que nuestro coste de OpenAI salía por debajo del real— era que el
    SDK leyera `prompt_tokens_details.cache_write_tokens` (D-101).
    """
    tabla = get_price_table()
    con_escritura = tabla.compute(
        "gpt-5.6-luna", input_tokens=10_000, output_tokens=0, cache_write_tokens=8_000
    )
    como_entrada = tabla.compute("gpt-5.6-luna", input_tokens=10_000, output_tokens=0)
    assert con_escritura.cache_write_usd == pytest.approx(8_000 * 0.25 / 1_000_000)
    assert con_escritura.input_usd > como_entrada.input_usd
