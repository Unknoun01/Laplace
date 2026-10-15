"""La misma pregunta con la misma respuesta en ejecuciones distintas (D-200).

Lo que se exige, en los dos almacenes:

* que cuenten las llamadas que repiten la pregunta de otra ejecución (todas menos la
  primera ejecución), y de ésas las que dieron la misma respuesta que la primera;
* que las repeticiones dentro de una ejecución no cuenten (son de la repetición y los
  bucles), ni las tiradas de evaluación, ni las llamadas con error, ni un puñado;
* que la pregunta sea la misma salvo los números, pero la respuesta, exacta;
* que nunca ponga dinero en el total: «hasta X», sobre lo que queda del paso después de
  lo que ya reclaman las demás reglas (D-117);
* que sin muestra suficiente no haya porcentaje, y con ella vaya su intervalo;
* y que su ficha cuente lo mismo que su tarjeta.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.pricing import get_price_table
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import cifras, insights
from laplace_backend.config import Settings
from laplace_backend.evals import wilson
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(hours=2)
MODELO = "gpt-5.6-terra"
OTRO = "gpt-5.6-luna"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=3), days=1)
KIND = "cache_semantica"
COSTE = 0.01


@pytest.fixture(params=["sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "sqlite":
        local = SQLiteStore(tmp_path / "l.db")
        local.migrate()
        yield local
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d200_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    nube = ClickHouseStore(Settings(clickhouse_database=base))
    nube.migrate()
    try:
        yield nube
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _span(proyecto, traza, inicio, ms, **kw) -> Span:
    return Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        start_time=inicio, end_time=inicio + timedelta(milliseconds=ms),
        duration_ms=ms, **kw,
    )


def _ejecuciones(
    proyecto: str, respuestas, *, pregunta: str = "q-1", llamadas: int = 1,
    modelo: str = MODELO, paso: str = "clasificar", etiquetas=(), estado: str = "ok",
    salida: int = 300, coste: float = COSTE, desde: int = 0,
) -> list[Span]:
    """Una ejecución por respuesta, una detrás de otra en el tiempo: una raíz y
    `llamadas` al modelo con la misma pregunta (`loop_hash`) y esa respuesta."""
    spans = []
    for e, respuesta in enumerate(respuestas):
        traza = f"{proyecto}-{paso}-{modelo}-{desde + e}"
        base = AHORA + timedelta(seconds=10 * (desde + e))
        spans.append(
            _span(proyecto, traza, base, 9_000, name="agente", type="agent",
                  status="ok", tags=list(etiquetas))
        )
        for k in range(llamadas):
            inicio = base + timedelta(seconds=2 * k)
            llamada = _span(
                proyecto, traza, inicio, 1_000, name=f"chat {modelo}", type="llm",
                status=estado, step_key=f"k-{paso}", step_label=paso,
                step_site=f"agente > {paso}", dedup_hash=uuid.uuid4().hex,
                loop_hash=pregunta, loop_out_hash=respuesta,
            )
            llamada.llm = LLMAttributes(
                system="openai", request_model=modelo, response_model=modelo,
                usage=TokenUsage(input_tokens=1_500, output_tokens=salida),
                cost=Cost(total_usd=coste),
            )
            spans.append(llamada)
    return spans


def _cache(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def test_la_misma_respuesta_en_otra_ejecucion_es_una_cache_sin_dinero(store):
    store.insert_spans(_ejecuciones("misma", ["a"] * 12))
    (h,) = _cache(store, "misma")
    assert h.id == f"{KIND}:k-clasificar:{MODELO}"
    assert not h.costs_money
    assert h.window_waste_usd == 0
    assert h.monthly_saving_usd is None
    assert h.cost_unavailable, "tiene que decir por qué no entra en el total"
    assert cifras.dinero(11 * COSTE) in h.summary, "«hasta» lo que costaron las 11"
    (g,) = store.cacheable_groups("misma", VENTANA, min_mismas=1)
    assert (g.questions, g.repeated, g.same_answer) == (1, 11, 11)
    assert g.same_answer_cost_usd == pytest.approx(11 * COSTE)


def test_cuenta_las_que_dan_la_misma_respuesta_que_la_primera(store):
    """Ocho veces «a» y cuatro «b»: de las once repetidas, siete repiten la de la
    primera ejecución. El porcentaje, con su intervalo."""
    store.insert_spans(_ejecuciones("parte", ["a"] * 8 + ["b"] * 4))
    (g,) = store.cacheable_groups("parte", VENTANA, min_mismas=1)
    assert (g.repeated, g.same_answer) == (11, 7)
    assert g.same_answer_cost_usd == pytest.approx(7 * COSTE)
    (h,) = _cache(store, "parte")
    bajo, alto = wilson(7, 11)
    for cifra in (7 / 11, bajo, alto):
        assert cifras.porcentaje(cifra) in h.summary


def test_la_primera_ejecucion_marca_la_respuesta(store):
    """La primera dijo «b» y las demás «a»: ninguna repitió la primera respuesta, así
    que no hay prueba de que una caché la hubiera servido."""
    store.insert_spans(_ejecuciones("primera", ["b"] + ["a"] * 11))
    (g,) = store.cacheable_groups("primera", VENTANA, min_mismas=0)
    assert (g.repeated, g.same_answer) == (11, 0)
    assert _cache(store, "primera") == []


def test_las_repeticiones_dentro_de_una_ejecucion_no_cuentan(store):
    """Doce llamadas con la misma pregunta en una sola ejecución son de la repetición;
    tres por ejecución en seis ejecuciones son cinco repetidas, no diecisiete."""
    store.insert_spans(_ejecuciones("dentro", ["a"], llamadas=12))
    assert store.cacheable_groups("dentro", VENTANA, min_mismas=0) == []
    store.insert_spans(_ejecuciones("varias", ["a"] * 6, llamadas=3))
    (g,) = store.cacheable_groups("varias", VENTANA, min_mismas=1)
    assert (g.repeated, g.same_answer) == (5, 5)
    assert g.same_answer_cost_usd == pytest.approx(5 * COSTE)


def test_cada_pregunta_se_compara_con_su_primera(store):
    """Dos preguntas distintas del mismo paso, cada una con su respuesta: son dos
    preguntas, y cada repetida se compara con la primera de la suya."""
    store.insert_spans(
        _ejecuciones("dos", ["a"] * 4, pregunta="q-1")
        + _ejecuciones("dos", ["b"] * 4, pregunta="q-2", desde=100)
    )
    (g,) = store.cacheable_groups("dos", VENTANA, min_mismas=1)
    assert (g.questions, g.repeated, g.same_answer) == (2, 6, 6)


def test_otro_modelo_es_otro_grupo(store):
    store.insert_spans(
        _ejecuciones("modelos", ["a"] * 6) + _ejecuciones("modelos", ["a"] * 6, modelo=OTRO)
    )
    grupos = store.cacheable_groups("modelos", VENTANA, min_mismas=1)
    assert sorted((g.model, g.same_answer) for g in grupos) == sorted([(MODELO, 5), (OTRO, 5)])


def test_las_tiradas_de_evaluacion_no_cuentan(store):
    store.insert_spans(_ejecuciones("eval", ["a"] * 12, etiquetas=(EVAL_TAG,)))
    assert store.cacheable_groups("eval", VENTANA, min_mismas=0) == []


def test_las_llamadas_con_error_no_cuentan(store):
    store.insert_spans(_ejecuciones("error", ["a"] * 12, estado="error"))
    assert store.cacheable_groups("error", VENTANA, min_mismas=0) == []


def test_pocas_no_dicen_nada(store):
    store.insert_spans(_ejecuciones("pocas", ["a"] * 5))
    assert _cache(store, "pocas") == []


def test_sin_muestra_no_hay_porcentaje(store):
    """Seis ejecuciones: cinco repetidas con la misma respuesta. Llega para hablar, no
    para dar un porcentaje: se dice la cuenta."""
    store.insert_spans(_ejecuciones("muestra", ["a"] * 6))
    (h,) = _cache(store, "muestra")
    assert "%" not in h.summary
    ficha = insights.detail(store, "muestra", VENTANA, h.id)
    assert "%" not in ficha.what_happens
    assert "10" in ficha.what_happens, "tiene que decir cuántas hacen falta"


def test_no_reclama_lo_que_ya_reclama_el_modelo_caro(store):
    """Respuestas cortas en un modelo caro: el modelo caro reclama su parte del paso, y
    el «hasta» de la caché es sobre lo que queda, en proporción (D-117)."""
    precio = get_price_table().compute(MODELO, input_tokens=1_500, output_tokens=20)
    store.insert_spans(_ejecuciones("caro", ["a"] * 12, salida=20, coste=precio.total_usd))
    hallazgos = insights.detect(store, "caro", VENTANA)
    (caro,) = [f for f in hallazgos if f.kind == "modelo_caro"]
    (cache,) = [f for f in hallazgos if f.kind == KIND]
    paso = 12 * precio.total_usd
    queda = (paso - caro.window_waste_usd) / paso
    assert 0 < queda < 1
    hasta = 11 * precio.total_usd * queda
    assert cifras.dinero(hasta) in cache.summary
    assert cifras.dinero(11 * precio.total_usd) not in cache.summary
    ficha = insights.detail(store, "caro", VENTANA, cache.id)
    assert cifras.dinero_exacto(hasta) in ficha.savings_calculation
    assert cache.window_waste_usd == 0, "no descuenta a nadie: no pone dinero"


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    store.insert_spans(_ejecuciones("ficha", ["a"] * 12))
    (h,) = _cache(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha is not None
    assert ficha.window_waste_usd == h.window_waste_usd == 0
    assert ficha.summary == h.summary
    assert ficha.detection_query
    assert cifras.dinero_exacto(11 * COSTE) in ficha.savings_calculation
    codigo = " ".join(p.code or "" for p in ficha.fix_steps)
    assert "laplace replay" in codigo, "lo primero es validarlo"
    assert MODELO in codigo
