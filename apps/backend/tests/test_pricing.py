"""Pruebas de la tabla de precios.

Todo el producto se vende con una cifra en dólares. Si esa cifra está mal, no hay
producto: estas pruebas existen para que un precio equivocado rompa la suite en lugar
de llegar a la pantalla de alguien.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from laplace_backend.config import Settings
from laplace_backend.pricing import PriceTable, get_price_table
from laplace_backend.storage.base import Window
from laplace_backend.storage.clickhouse import ClickHouseStore

PRICES_PATH = Path(__file__).parents[1] / "laplace_backend" / "pricing" / "model_prices.json"
#: A partir de aquí, la tabla se considera caducada y hay que reverificarla.
MAX_AGE_DAYS = 120


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
    for key, source in raw["sources"].items():
        antiguedad = (date.today() - date.fromisoformat(source["verified_at"])).days
        assert antiguedad <= MAX_AGE_DAYS, (
            f"la fuente '{key}' se verificó hace {antiguedad} días: toca reverificar "
            f"{source['url']} y subir 'version'"
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
