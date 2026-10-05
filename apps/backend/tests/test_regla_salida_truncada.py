"""La salida truncada (D-193): una respuesta cortada por el tope de salida y rehecha.

Lo que se exige, en los dos almacenes:

* el dinero exacto del caso de libro: la llamada cortada se tira entera cuando el mismo
  paso vuelve a llamar después en la misma ejecución, y la que la rehace no se cuenta;
* que reconozca el corte de los tres proveedores (`length`, `max_tokens`,
  `max_output_tokens`), sin distinguir mayúsculas;
* que una respuesta cortada que nadie rehízo no se cobre: no sabemos si sirvió;
* que no cuente lo que ya cuentan la repetición exacta y los bucles, ni que el modelo
  caro vuelva a reclamar las llamadas que esta regla da por tiradas;
* que las tiradas de evaluación no cuenten;
* y que su ficha cuente lo mismo que su tarjeta.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.pricing import get_price_table
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)
MODELO = "gpt-5.6-terra"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=2), days=1)
KIND = "salida_truncada"


@pytest.fixture(params=["sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "sqlite":
        local = SQLiteStore(tmp_path / "t.db")
        local.migrate()
        yield local
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d193_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    nube = ClickHouseStore(Settings(clickhouse_database=base))
    nube.migrate()
    try:
        yield nube
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _coste(entrada: int, salida: int) -> float:
    precio = get_price_table().lookup(MODELO)
    return (entrada * precio.input + salida * precio.output) / 1_000_000


def _llamada(
    proyecto: str, traza: str, i: int, *, motivo: str = "stop", paso: str = "redactar",
    entrada: int = 1_000, salida: int = 800, dedup: str | None = None, tags=None,
) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name=f"chat {MODELO}", type="llm", status="ok", start_time=inicio,
        end_time=inicio + timedelta(milliseconds=700), duration_ms=700.0,
        step_key=f"k-{paso}", step_label=paso, step_site=f"agente > {paso}",
        dedup_hash=dedup or uuid.uuid4().hex, tags=list(tags or []),
    )
    span.llm = LLMAttributes(
        system="openai", request_model=MODELO, response_model=MODELO,
        usage=TokenUsage(input_tokens=entrada, output_tokens=salida),
        cost=Cost(total_usd=_coste(entrada, salida)),
        finish_reasons=[motivo],
    )
    return span


def _cortada_y_rehecha(proyecto: str, n: int, motivo: str = "length", **kw) -> list[Span]:
    """Cada ejecución: una llamada cortada por el tope y la que la rehace."""
    spans = []
    for t in range(n):
        traza = f"{proyecto}-{t}"
        spans.append(_llamada(proyecto, traza, t * 10, motivo=motivo, **kw))
        spans.append(_llamada(proyecto, traza, t * 10 + 1, salida=1_200, **kw))
    return spans


def _truncadas(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def test_el_caso_de_libro_cuesta_lo_que_costaron_las_cortadas(store):
    store.insert_spans(_cortada_y_rehecha("libro", 6))
    hallazgos = _truncadas(store, "libro")
    assert len(hallazgos) == 1
    h = hallazgos[0]
    assert h.window_waste_usd == pytest.approx(6 * _coste(1_000, 800))
    assert h.window_waste_tokens == 6 * 1_800
    assert h.window_waste_ms == pytest.approx(6 * 700.0)
    assert h.step_key == "k-redactar"
    assert h.sample_trace_id.startswith("libro-")
    assert h.id == f"{KIND}:k-redactar:{MODELO}"


@pytest.mark.parametrize("motivo", ["max_tokens", "MAX_TOKENS", "max_output_tokens"])
def test_reconoce_el_corte_de_cada_proveedor(store, motivo):
    store.insert_spans(_cortada_y_rehecha("prov", 6, motivo=motivo))
    assert len(_truncadas(store, "prov")) == 1


def test_una_respuesta_cortada_que_nadie_rehizo_no_se_cobra(store):
    spans = []
    for t in range(8):
        spans.append(_llamada("sola", f"sola-{t}", t * 10, motivo="length"))
    store.insert_spans(spans)
    assert _truncadas(store, "sola") == []


def test_la_que_se_rehace_en_otra_ejecucion_no_cuenta(store):
    """Otra ejecución es otra pregunta: no es rehacer la misma respuesta."""
    spans = []
    for t in range(8):
        spans.append(_llamada("otra", f"otra-{t}", t * 10, motivo="length"))
        spans.append(_llamada("otra", f"otra-b-{t}", t * 10 + 1))
    store.insert_spans(spans)
    assert _truncadas(store, "otra") == []


def test_por_debajo_del_minimo_no_dice_nada(store):
    store.insert_spans(_cortada_y_rehecha("pocas", insights.MIN_TRUNCADAS_REHECHAS - 1))
    assert _truncadas(store, "pocas") == []


def test_no_cuenta_lo_que_ya_cuenta_la_repeticion(store):
    """Tres copias exactas en una ejecución las reclama la repetición; aquí, nada."""
    spans = []
    for t in range(6):
        traza = f"rep-{t}"
        mismo = f"igual-{t}"
        spans.append(_llamada("rep", traza, t * 10, motivo="length", dedup=mismo))
        spans.append(_llamada("rep", traza, t * 10 + 1, motivo="length", dedup=mismo))
        spans.append(_llamada("rep", traza, t * 10 + 2, dedup=mismo))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, "rep", VENTANA)
    assert any(f.kind == "repeticion" for f in hallazgos)
    assert [f for f in hallazgos if f.kind == KIND] == []


def test_las_tiradas_de_evaluacion_no_cuentan(store):
    store.insert_spans(_cortada_y_rehecha("eval", 6, tags=[EVAL_TAG]))
    assert _truncadas(store, "eval") == []


def test_el_modelo_caro_no_reclama_las_llamadas_que_esta_regla_tira(store):
    """Salidas cortas en un modelo caro: el modelo caro reclama la diferencia de tarifa
    de cada llamada, y esta regla, la llamada cortada entera. Sin descontarlas, las dos
    juntas reclaman más de lo que costó el paso."""
    spans = []
    for t in range(8):
        traza = f"corto-{t}"
        spans.append(_llamada("corto", traza, t * 10, motivo="length", entrada=400, salida=40))
        spans.append(_llamada("corto", traza, t * 10 + 1, entrada=400, salida=40))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, "corto", VENTANA)
    tipos = {f.kind for f in hallazgos}
    assert {KIND, "modelo_caro"} <= tipos, tipos
    gastado = 16 * _coste(400, 40)
    reclamado = sum(f.window_waste_usd for f in hallazgos if f.step_key == "k-redactar")
    assert reclamado <= gastado + 1e-12, (reclamado, gastado)
    caro = next(f for f in hallazgos if f.kind == "modelo_caro")
    llamadas = next(i.value for i in caro.tech if i.label == "llamadas")
    assert llamadas == "8", "el modelo caro tiene que mirar sólo las 8 que no se tiran"


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    spans = _cortada_y_rehecha("ficha", 6)
    # Y dos cortadas que se quedaron así: la ficha las cuenta sin cobrarlas.
    spans += [_llamada("ficha", f"ficha-sola-{t}", 500 + t, motivo="length") for t in range(2)]
    store.insert_spans(spans)
    (h,) = _truncadas(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha is not None
    assert ficha.title == h.title
    assert ficha.window_waste_usd == pytest.approx(h.window_waste_usd)
    assert ficha.fix_steps and ficha.why and ficha.what_happens
    assert "SELECT" in ficha.detection_query
    assert "2" in ficha.what_happens, "las cortadas sin rehacer se cuentan, sin cobrarlas"
    motivos = [s.llm.finish_reasons for s in ficha.evidence if s.llm]
    assert ["length"] in motivos, "la evidencia enseña la llamada cortada"
    assert len(ficha.evidence) >= 2, "y la que la rehizo"
