"""El gráfico del Diagnóstico: gasto por día, franja evitable y coste por paso (D-152).

Lo que se exige, en los dos almacenes:

* los tramos suman **exactamente** el gasto y lo evitable del héroe: el gráfico no
  puede contar una cifra distinta de la que está encima;
* lo evitable de un hallazgo cae en los días en que gastó su paso, y en ningún otro;
* los pasos, del que más gasta al que menos, con su parte evitable, y el resto sumado.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.insights import grafico
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)
VENTANA = Window(since=AHORA - timedelta(days=5), until=AHORA, days=5)


@pytest.fixture(params=["sqlite", "clickhouse"])
def almacen(request, tmp_path):
    proyecto = f"grafico-{uuid.uuid4().hex[:8]}"
    if request.param == "sqlite":
        store = SQLiteStore(tmp_path / "laplace.db")
        store.migrate()
        yield store, proyecto
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    yield store, proyecto
    store.delete_project(proyecto)


def _llamada(proyecto, traza, paso, cuando, coste, dedup) -> Span:
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=traza,
        project_id=proyecto,
        name=paso,
        type="llm",
        status="ok",
        start_time=cuando,
        end_time=cuando + timedelta(milliseconds=300),
        duration_ms=300.0,
        dedup_hash=dedup,
        step_key=paso,
        step_label=paso,
    )
    # Salida larga: que la única regla que salte sea la repetición.
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=200, output_tokens=400),
        cost=Cost(input_usd=coste, total_usd=coste),
    )
    return span


def _repetida(proyecto, paso, cuando, coste, veces=3) -> list[Span]:
    traza = uuid.uuid4().hex
    return [
        _llamada(proyecto, traza, paso, cuando + timedelta(seconds=i), coste, f"h-{traza}")
        for i in range(veces)
    ]


def _sembrar(store, proyecto) -> None:
    spans = [
        # «buscar» se repite tres veces en una ejecución hace 4,5 días y en otra de hoy.
        *_repetida(proyecto, "buscar", AHORA - timedelta(days=4, hours=12), 0.01),
        *_repetida(proyecto, "buscar", AHORA - timedelta(hours=2), 0.01),
        # Hace 2,5 días sólo trabajó «responder», sin repetir nada.
        _llamada(proyecto, uuid.uuid4().hex, "responder", AHORA - timedelta(days=2, hours=12),
                 0.05, "unica"),
    ]
    store.insert_spans(spans)


def test_los_tramos_suman_lo_mismo_que_el_heroe(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    vista = insights.overview(store, proyecto, VENTANA)
    assert vista.window_avoidable_usd == pytest.approx(0.04)
    tramos = vista.chart.buckets
    assert sum(t.cost_usd for t in tramos) == pytest.approx(vista.window_cost_usd)
    assert sum(t.avoidable_usd for t in tramos) == pytest.approx(vista.window_avoidable_usd)
    assert vista.chart.unattributed_usd == 0


def test_lo_evitable_cae_donde_gasto_su_paso(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    tramos = insights.overview(store, proyecto, VENTANA).chart.buckets
    assert len(tramos) == 6, "cinco días y el pico de hoy: tramos de un día"
    evitable = [round(t.avoidable_usd, 6) for t in tramos]
    gasto = [round(t.cost_usd, 6) for t in tramos]
    # Días 0 y 4-5 (según la hora): «buscar». El día 2, sólo «responder»: nada evitable.
    assert evitable[0] == pytest.approx(0.02)
    assert gasto[2] == pytest.approx(0.05) and evitable[2] == 0
    assert sum(evitable[3:]) == pytest.approx(0.02)


def test_los_pasos_del_que_mas_gasta_al_que_menos(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    pasos = insights.overview(store, proyecto, VENTANA).chart.steps
    assert [(p.name, round(p.cost_usd, 6), round(p.avoidable_usd, 6)) for p in pasos] == [
        ("buscar", 0.06, 0.04),
        ("responder", 0.05, 0.0),
    ]


def test_el_resto_de_pasos_va_sumado(almacen, monkeypatch):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    monkeypatch.setattr(grafico, "MAX_PASOS", 1)
    vista = insights.overview(store, proyecto, VENTANA).chart
    assert [p.name for p in vista.steps] == ["buscar"]
    assert vista.other_steps_usd == pytest.approx(0.05)


def test_una_ventana_corta_va_por_horas(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    dia = Window(since=AHORA - timedelta(days=1), until=AHORA, days=1)
    vista = insights.overview(store, proyecto, dia)
    assert vista.chart.bucket_minutes == 60
    assert len(vista.chart.buckets) == 25


def test_sin_gasto_no_hay_grafico(almacen):
    store, proyecto = almacen
    assert insights.overview(store, proyecto, VENTANA).chart is None


def test_un_tramo_nunca_tiene_mas_evitable_que_gasto():
    """El total del tramo incluye todo el gasto y el de cada paso sólo lo que miran las
    reglas; si un reparto le pusiera a un tramo más evitable que gasto, manda lo medido."""
    from laplace_backend.insights.modelos import Finding
    from laplace_backend.storage.base import StepCostSeries

    class Falso:
        def step_cost_series(self, project_id, window, ancho):
            return StepCostSeries(
                total=[1.0, 0.1], pasos={"a": [0.5, 0.5]}, nombres={"a": ("a", "", "")}
            )

    corta = Window(since=AHORA - timedelta(hours=1), until=AHORA, days=1)
    hallazgo = Finding(id="x", kind="repeticion", title="", summary="", step_key="a",
                       window_waste_usd=1.0)
    tramos = grafico.construir(Falso(), "p", corta, [hallazgo]).buckets
    assert [t.avoidable_usd for t in tramos] == [0.5, 0.1]


def test_dos_pasos_homonimos_no_salen_con_el_mismo_nombre(almacen):
    """En la demo salían tres filas «responder» con tres cifras: la misma función con
    instrucciones distintas. Se nombran como en el resto del producto (D-106)."""
    store, proyecto = almacen
    spans = []
    for clave, pista, coste in (("k1", "Eres el agente de ventas", 0.03),
                                ("k2", "Eres el agente de soporte", 0.02)):
        s = _llamada(proyecto, uuid.uuid4().hex, "responder", AHORA - timedelta(hours=3),
                     coste, clave)
        s.step_key = clave
        s.step_hint = pista
        spans.append(s)
    store.insert_spans(spans)
    nombres = [p.name for p in insights.overview(store, proyecto, VENTANA).chart.steps]
    assert len(set(nombres)) == 2, nombres
    assert all(n.startswith("responder") for n in nombres), nombres
