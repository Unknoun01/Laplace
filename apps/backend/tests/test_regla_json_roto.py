"""Reintentos por JSON mal formado (D-194): una salida que no se puede leer, y rehecha.

Lo que se exige:

* que la ingesta marque cada salida de modelo como JSON que se lee, JSON roto o texto
  que no intenta ser JSON, con los bloques de código y las formas de mensaje de los
  proveedores;
* en los dos almacenes, el dinero exacto del caso de libro: la llamada rota se tira
  entera cuando el mismo paso vuelve a llamar después en la misma ejecución;
* que no se cobre la rota que nadie rehízo, ni el texto que no intentaba ser JSON;
* que una salida cortada por el tope (que casi siempre deja el JSON roto) sea de la
  regla de la salida truncada y no de ésta, y que entre las dos no reclamen más que lo
  que costó;
* que no cuente lo que cuenta la repetición, ni lo vuelva a reclamar el modelo caro;
* que las tiradas de evaluación no cuenten;
* y que su ficha cuente lo mismo que su tarjeta.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace import semconv
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.ingest.otlp import salida_json
from laplace_backend.pricing import get_price_table
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)
MODELO = "gpt-5.6-terra"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=2), days=1)
KIND = "json_roto"

ROTO = '{"pedido": 41, "lineas": [{"sku": "A-1", '
BIEN = '{"pedido": 41, "lineas": []}'


# -- la marca de la ingesta --------------------------------------------------------


@pytest.mark.parametrize(
    ("mensajes", "esperado"),
    [
        ([{"role": "assistant", "content": BIEN}], "ok"),
        ([{"role": "assistant", "content": ROTO}], "roto"),
        ([{"role": "assistant", "content": "  [1, 2, 3]\n"}], "ok"),
        ([{"role": "assistant", "content": "El pedido 41 está listo."}], ""),
        ([{"role": "assistant", "content": f"```json\n{BIEN}\n```"}], "ok"),
        ([{"role": "assistant", "content": f"```json\n{ROTO}\n```"}], "roto"),
        # Anthropic: bloques de contenido.
        ([{"role": "assistant", "content": [{"type": "text", "text": ROTO}]}], "roto"),
        # Convenciones de OTel: `parts`.
        ([{"role": "assistant", "parts": [{"type": "text", "content": BIEN}]}], "ok"),
        ([], ""),
        ([{"role": "assistant", "content": None}], ""),
    ],
)
def test_la_ingesta_sabe_si_la_salida_es_json_que_se_lee(mensajes, esperado):
    assert salida_json(mensajes) == esperado


def test_la_marca_llega_al_span_por_la_ingesta():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from laplace_backend.ingest.otlp import parse_spans

    exportador = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(exportador))
    with proveedor.get_tracer("prueba").start_as_current_span("chat") as span:
        span.set_attribute(semconv.LAPLACE_SPAN_TYPE, "llm")
        span.set_attribute(semconv.GEN_AI_REQUEST_MODEL, MODELO)
        span.set_attribute(
            semconv.GEN_AI_OUTPUT_MESSAGES,
            json.dumps([{"role": "assistant", "content": ROTO}]),
        )
    (traducido,) = parse_spans(encode_spans(exportador.get_finished_spans()))
    assert traducido.output_json == "roto"


# -- la regla ----------------------------------------------------------------------


@pytest.fixture(params=["sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "sqlite":
        local = SQLiteStore(tmp_path / "j.db")
        local.migrate()
        yield local
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d194_{uuid.uuid4().hex[:8]}"
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
    proyecto: str, traza: str, i: int, *, texto: str = BIEN, motivo: str = "stop",
    paso: str = "extraer", entrada: int = 1_000, salida: int = 800,
    dedup: str | None = None, tags=None,
) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    mensajes = [{"role": "assistant", "content": texto}]
    span = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name=f"chat {MODELO}", type="llm", status="ok", start_time=inicio,
        end_time=inicio + timedelta(milliseconds=700), duration_ms=700.0,
        step_key=f"k-{paso}", step_label=paso, step_site=f"agente > {paso}",
        dedup_hash=dedup or uuid.uuid4().hex, tags=list(tags or []),
        output_json=salida_json(mensajes),
    )
    span.llm = LLMAttributes(
        system="openai", request_model=MODELO, response_model=MODELO,
        usage=TokenUsage(input_tokens=entrada, output_tokens=salida),
        cost=Cost(total_usd=_coste(entrada, salida)),
        output_messages=mensajes,
        finish_reasons=[motivo],
    )
    return span


def _rota_y_rehecha(proyecto: str, n: int, **kw) -> list[Span]:
    spans = []
    for t in range(n):
        traza = f"{proyecto}-{t}"
        spans.append(_llamada(proyecto, traza, t * 10, texto=ROTO, **kw))
        spans.append(_llamada(proyecto, traza, t * 10 + 1, **kw))
    return spans


def _rotos(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def test_el_caso_de_libro_cuesta_lo_que_costaron_las_rotas(store):
    store.insert_spans(_rota_y_rehecha("libro", 6))
    (h,) = _rotos(store, "libro")
    assert h.window_waste_usd == pytest.approx(6 * _coste(1_000, 800))
    assert h.window_waste_tokens == 6 * 1_800
    assert h.step_key == "k-extraer"
    assert h.id == f"{KIND}:k-extraer:{MODELO}"
    assert h.sample_trace_id.startswith("libro-")


def test_un_json_roto_que_nadie_rehizo_no_se_cobra(store):
    store.insert_spans(
        [_llamada("solo", f"solo-{t}", t * 10, texto=ROTO) for t in range(8)]
    )
    assert _rotos(store, "solo") == []


def test_el_texto_que_no_intenta_ser_json_no_cuenta(store):
    spans = []
    for t in range(8):
        traza = f"prosa-{t}"
        spans.append(_llamada("prosa", traza, t * 10, texto="Déjame pensarlo."))
        spans.append(_llamada("prosa", traza, t * 10 + 1))
    store.insert_spans(spans)
    assert _rotos(store, "prosa") == []


def test_por_debajo_del_minimo_no_dice_nada(store):
    store.insert_spans(_rota_y_rehecha("pocas", insights.MIN_JSON_ROTO_REHECHAS - 1))
    assert _rotos(store, "pocas") == []


def test_una_salida_cortada_es_de_la_truncada_y_no_de_esta(store):
    """El JSON se rompe porque el modelo se quedó sin espacio: el arreglo es el tope, y
    el dinero lo reclama la otra regla, una sola vez."""
    store.insert_spans(_rota_y_rehecha("corte", 6, motivo="length"))
    hallazgos = insights.detect(store, "corte", VENTANA)
    tipos = [f.kind for f in hallazgos]
    assert "salida_truncada" in tipos
    assert KIND not in tipos


def test_entre_las_dos_no_reclaman_mas_de_lo_que_costo(store):
    spans = []
    for t in range(6):
        traza = f"mixto-{t}"
        # Una cortada (de la truncada), una rota sin cortar (de ésta) y la buena.
        spans.append(_llamada("mixto", traza, t * 10, texto=ROTO, motivo="length"))
        spans.append(_llamada("mixto", traza, t * 10 + 1, texto=ROTO))
        spans.append(_llamada("mixto", traza, t * 10 + 2))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, "mixto", VENTANA)
    por_tipo = {f.kind: f.window_waste_usd for f in hallazgos}
    una = 6 * _coste(1_000, 800)
    assert por_tipo["salida_truncada"] == pytest.approx(una)
    assert por_tipo[KIND] == pytest.approx(una)
    gastado = 18 * _coste(1_000, 800)
    assert sum(f.window_waste_usd for f in hallazgos) <= gastado + 1e-12


def test_no_cuenta_lo_que_ya_cuenta_la_repeticion(store):
    spans = []
    for t in range(6):
        traza = f"rep-{t}"
        mismo = f"igual-{t}"
        spans.append(_llamada("rep", traza, t * 10, texto=ROTO, dedup=mismo))
        spans.append(_llamada("rep", traza, t * 10 + 1, texto=ROTO, dedup=mismo))
        spans.append(_llamada("rep", traza, t * 10 + 2, dedup=mismo))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, "rep", VENTANA)
    assert any(f.kind == "repeticion" for f in hallazgos)
    assert [f for f in hallazgos if f.kind == KIND] == []


def test_las_tiradas_de_evaluacion_no_cuentan(store):
    store.insert_spans(_rota_y_rehecha("eval", 6, tags=[EVAL_TAG]))
    assert _rotos(store, "eval") == []


def test_el_modelo_caro_no_reclama_las_llamadas_que_esta_regla_tira(store):
    spans = []
    for t in range(8):
        traza = f"corto-{t}"
        spans.append(_llamada("corto", traza, t * 10, texto=ROTO, entrada=400, salida=40))
        spans.append(_llamada("corto", traza, t * 10 + 1, entrada=400, salida=40))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, "corto", VENTANA)
    tipos = {f.kind for f in hallazgos}
    assert {KIND, "modelo_caro"} <= tipos, tipos
    reclamado = sum(f.window_waste_usd for f in hallazgos if f.step_key == "k-extraer")
    assert reclamado <= 16 * _coste(400, 40) + 1e-12
    caro = next(f for f in hallazgos if f.kind == "modelo_caro")
    assert next(i.value for i in caro.tech if i.label == "llamadas") == "8"


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    spans = _rota_y_rehecha("ficha", 6)
    spans += [_llamada("ficha", f"ficha-sola-{t}", 500 + t, texto=ROTO) for t in range(3)]
    store.insert_spans(spans)
    (h,) = _rotos(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha is not None
    assert ficha.title == h.title
    assert ficha.window_waste_usd == pytest.approx(h.window_waste_usd)
    assert ficha.fix_steps and ficha.why and ficha.what_happens
    assert "SELECT" in ficha.detection_query
    assert "3" in ficha.what_happens, "las rotas sin rehacer se cuentan, sin cobrarlas"
    salidas = [s.llm.output_messages[0]["content"] for s in ficha.evidence if s.llm]
    assert ROTO in salidas and BIEN in salidas, "la evidencia enseña la rota y la buena"
