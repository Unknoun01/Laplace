"""Pruebas del motor de detección.

Lo que importa aquí no es que las reglas disparen, es que **las cifras sean honestas**:
que no se invente dinero donde no lo hay, que no se cuente dos veces el mismo ahorro, y
que no se prometa un ahorro mayor que el gasto.

Van contra ClickHouse porque las reglas son consultas; se saltan solas si no hay ninguno
escuchando.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage, ToolAttributes

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.storage.base import Window
from laplace_backend.storage.clickhouse import ClickHouseStore

BASE = datetime.now(timezone.utc) - timedelta(hours=1)


@pytest.fixture(scope="module")
def store() -> ClickHouseStore:
    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; se omiten las pruebas del motor")
    store.migrate()
    return store


@pytest.fixture(scope="module")
def window() -> Window:
    ahora = datetime.now(timezone.utc)
    return Window(since=ahora - timedelta(days=7), until=ahora + timedelta(minutes=5), days=7)


def _span(project, trace, span_id, name, type_, offset_s, **kwargs) -> Span:
    start = BASE + timedelta(seconds=offset_s)
    return Span(
        span_id=span_id,
        trace_id=trace,
        parent_span_id=kwargs.pop("parent", None),
        project_id=project,
        name=name,
        type=type_,
        status="ok",
        start_time=start,
        end_time=start + timedelta(milliseconds=kwargs.pop("ms", 200)),
        duration_ms=kwargs.pop("duration_ms", 200.0),
        dedup_hash=kwargs.pop("dedup_hash", ""),
        **kwargs,
    )


def _llm(model: str, tok_in: int, tok_out: int, cost: float) -> LLMAttributes:
    return LLMAttributes(
        system="openai",
        request_model=model,
        response_model=model,
        usage=TokenUsage(input_tokens=tok_in, output_tokens=tok_out),
        cost=Cost(input_usd=cost / 2, output_usd=cost / 2, total_usd=cost, estimated=False),
    )


# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_con_bucle_de_tool(store: ClickHouseStore):
    """Cinco ejecuciones que repiten una herramienta. Las tools no gastan tokens."""
    project = f"test-tool-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(5):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, f"{t}0" * 8, "agente", "agent", t * 10))
        for i in range(4):
            s = _span(
                project,
                trace,
                f"{t}{i + 1}" * 8,
                "buscar",
                "tool",
                t * 10 + i,
                parent=f"{t}0" * 8,
                dedup_hash="hash-buscar",
                duration_ms=150.0,
            )
            s.tool = ToolAttributes(name="buscar", arguments={"q": "vuelos"})
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_un_bucle_de_herramientas_no_inventa_dinero(store, window, proyecto_con_bucle_de_tool):
    findings = insights.detect(store, proyecto_con_bucle_de_tool, window)
    bucle = next(f for f in findings if f.kind == "repeticion")

    # Repetir una herramienta no gasta tokens: el ahorro en dinero tiene que ser cero.
    assert bucle.monthly_saving_usd == 0
    assert bucle.costs_money is False
    # Y lo que sí se pierde, tiempo, se cuenta: 3 repeticiones de más x 5 trazas x 150 ms.
    assert bucle.window_waste_ms == pytest.approx(3 * 5 * 150, rel=0.01)
    assert "gasta tokens de más" in bucle.summary


def test_el_heroe_no_promete_ahorrar_mas_de_lo_que_se_gasta(
    store, window, proyecto_con_bucle_de_tool
):
    resumen = insights.overview(store, proyecto_con_bucle_de_tool, window)
    assert resumen.monthly_avoidable_usd <= resumen.monthly_cost_usd
    assert resumen.monthly_necessary_usd >= 0


# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_con_reintentos(store: ClickHouseStore):
    """Un paso caro que además se reintenta: las dos reglas se pisan si no se descuenta.

    Diez ejecuciones. En cada una, `clasificar` llama a gpt-4o tres veces con el mismo
    prompt (200 tokens de entrada, 10 de salida, $0,001 cada llamada).
    """
    project = f"test-caro-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(10):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, f"a{t:03d}".ljust(16, "0"), "agente", "agent", t))
        for i in range(3):
            s = _span(
                project,
                trace,
                f"b{t:03d}{i}".ljust(16, "0"),
                "clasificar",
                "llm",
                t,
                parent=f"a{t:03d}".ljust(16, "0"),
                dedup_hash="hash-clasificar",
            )
            s.llm = _llm("gpt-4o", 200, 10, 0.001)
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_las_reglas_no_cuentan_dos_veces_el_mismo_ahorro(store, window, proyecto_con_reintentos):
    """Regresión: la regla del modelo caro contaba también las llamadas repetidas.

    30 llamadas a $0,001 = $0,03 gastados. La repetición se lleva 20 de esas llamadas
    ($0,02). A la regla del modelo caro sólo le pueden quedar las 10 restantes, así que
    su ahorro no puede acercarse al coste total.
    """
    findings = insights.detect(store, proyecto_con_reintentos, window)
    resumen = insights.overview(store, proyecto_con_reintentos, window)

    repeticion = next(f for f in findings if f.kind == "repeticion")
    modelo = next(f for f in findings if f.kind == "modelo_caro")

    # La repetición se lleva el coste íntegro de las copias sobrantes.
    assert repeticion.window_waste_usd == pytest.approx(0.02, rel=0.02)
    assert repeticion.costs_money is True

    # El modelo caro trabaja sólo sobre la llamada legítima de cada ejecución: como
    # mucho puede ahorrar lo que cuestan esas diez, nunca lo que cuestan las treinta.
    assert modelo.window_waste_usd < 0.01

    # Y la suma sigue sin pasarse del gasto real.
    total = sum(f.monthly_saving_usd for f in findings)
    assert total == pytest.approx(resumen.monthly_avoidable_usd, rel=0.001)
    assert resumen.monthly_avoidable_usd <= resumen.monthly_cost_usd


def test_la_ficha_dice_lo_mismo_que_la_tarjeta(store, window, proyecto_con_reintentos):
    """Si el detalle recalculase sin descontar el solape, diría otra cifra que el inicio."""
    findings = insights.detect(store, proyecto_con_reintentos, window)
    for finding in findings:
        detalle = insights.detail(store, proyecto_con_reintentos, window, finding.id)
        assert detalle is not None, finding.id
        assert detalle.monthly_saving_usd == pytest.approx(finding.monthly_saving_usd)
        assert detalle.title == finding.title
        # La consulta que se enseña es la que se ejecuta, no una copia.
        assert detalle.detection_query.startswith("SELECT")


def test_un_hallazgo_que_ya_no_se_da_devuelve_none(store, window, proyecto_con_reintentos):
    assert insights.detail(store, proyecto_con_reintentos, window, "repeticion:no-existe") is None
    assert insights.detail(store, proyecto_con_reintentos, window, "invento:lo-que-sea") is None


def test_un_proyecto_sin_datos_no_da_hallazgos(store, window):
    assert insights.detect(store, f"vacio-{uuid.uuid4().hex[:8]}", window) == []
