"""Lo parecido pero no idéntico, con la misma respuesta (D-219).

Lo que se exige, en los dos almacenes:

* que cuente las preguntas distintas cuya huella (`sim_hash`) está a 4 bits o menos de la
  cabeza del grupo, en otra ejecución, y de ésas las que dieron exactamente la salida de
  la cabeza;
* que no encadene (A≈B y B≈C no hacen a C parecida a A), que 5 bits no sea parecido, y
  que sin huella (0) no se compare nada;
* que la misma pregunta repetida (`loop_hash` igual) no cuente: es de la caché exacta,
  así que las dos reglas no cuentan nunca la misma llamada;
* que nunca ponga dinero en el total, que sin muestra no haya porcentaje y que su ficha
  cuente lo mismo que su tarjeta;
* y que calle, sin afirmar nada, un paso con demasiadas preguntas distintas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import cifras, insights
from laplace_backend.config import Settings
from laplace_backend.evals import wilson
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(hours=2)
MODELO = "gpt-5.6-terra"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=3), days=1)
KIND = "cache_parecida"
COSTE = 0.01
CABEZA = 0x0F0F_0F0F_0F0F_0F0F


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
    base = f"prueba_d219_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    nube = ClickHouseStore(Settings(clickhouse_database=base))
    nube.migrate()
    try:
        yield nube
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _a(bits: int) -> int:
    """La cabeza con sus `bits` primeros bits cambiados."""
    return CABEZA ^ ((1 << bits) - 1)


def _llamada(
    proyecto, n, *, sim, pregunta=None, salida="a", paso="clasificar", estado="ok",
    etiquetas=(), traza=None, coste=COSTE,
) -> list[Span]:
    traza = traza or f"{proyecto}-{paso}-{n}"
    inicio = AHORA + timedelta(seconds=10 * n)
    raiz = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto, name="agente",
        type="agent", status="ok", start_time=inicio, end_time=inicio + timedelta(seconds=9),
        duration_ms=9_000, tags=list(etiquetas),
    )
    ll = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name=f"chat {MODELO}", type="llm", status=estado, start_time=inicio,
        end_time=inicio + timedelta(seconds=1), duration_ms=1_000, step_key=f"k-{paso}",
        step_label=paso, step_site=f"agente > {paso}", dedup_hash=uuid.uuid4().hex,
        loop_hash=pregunta or f"q-{n}", loop_out_hash=salida, sim_hash=sim,
    )
    ll.llm = LLMAttributes(
        system="openai", request_model=MODELO, response_model=MODELO,
        usage=TokenUsage(input_tokens=1_500, output_tokens=300), cost=Cost(total_usd=coste),
    )
    return [raiz, ll]


def _paso(proyecto, salidas, *, distancia=2, **kw) -> list[Span]:
    """Una cabeza (n=0, la primera) y una pregunta casi igual por cada salida."""
    spans = _llamada(proyecto, 0, sim=CABEZA, salida=salidas[0], **kw)
    for n, s in enumerate(salidas[1:], 1):
        spans += _llamada(proyecto, n, sim=_a(distancia), salida=s, **kw)
    return spans


def _cache(store, proyecto):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def _grupos(store, proyecto, **kw):
    return store.similar_groups(proyecto, VENTANA, min_mismas=kw.pop("min_mismas", 1), **kw)


def test_preguntas_casi_iguales_con_la_misma_respuesta_son_una_cache_sin_dinero(store):
    store.insert_spans(_paso("par", ["a"] * 13))
    (h,) = _cache(store, "par")
    assert h.id == f"{KIND}:k-clasificar:{MODELO}"
    assert not h.costs_money
    assert h.window_waste_usd == 0
    assert h.monthly_saving_usd is None
    assert h.cost_unavailable
    assert cifras.dinero(12 * COSTE) in h.summary
    (g,) = _grupos(store, "par")
    assert (g.questions, g.repeated, g.same_answer) == (1, 12, 12)
    assert g.same_answer_cost_usd == pytest.approx(12 * COSTE)


def test_cuatro_bits_son_parecidas_y_cinco_no(store):
    store.insert_spans(_paso("cuatro", ["a"] * 6, distancia=4))
    (g,) = _grupos(store, "cuatro")
    assert g.repeated == 5
    store.insert_spans(_paso("cinco", ["a"] * 2, distancia=5))
    assert _grupos(store, "cinco", min_mismas=0) == []


def test_no_encadena(store):
    """B está a 4 bits de A y C a 4 de B, pero C a 8 de A: C no es de A."""
    spans = _llamada("cadena", 0, sim=CABEZA)
    spans += _llamada("cadena", 1, sim=CABEZA ^ 0b1111)
    spans += _llamada("cadena", 2, sim=CABEZA ^ 0b1111 ^ 0b1111_0000)
    store.insert_spans(spans)
    (g,) = _grupos(store, "cadena")
    assert g.repeated == 1, "sólo B; C es otra cabeza"


def test_sin_huella_no_se_compara(store):
    store.insert_spans(sum((_llamada("sinhuella", n, sim=0) for n in range(10)), []))
    assert _grupos(store, "sinhuella") == []


def test_la_misma_pregunta_repetida_es_de_la_cache_exacta(store):
    """Doce ejecuciones con la misma pregunta exacta: la caché exacta (D-200) las
    reclama. Aquí sólo hay una pregunta, así que no hay nada que comparar."""
    spans = []
    for n in range(12):
        spans += _llamada("exacta", n, sim=CABEZA, pregunta="q-misma")
    store.insert_spans(spans)
    assert _grupos(store, "exacta") == []


def test_las_dos_cachés_cuentan_llamadas_distintas(store):
    """Doce repeticiones exactas de la pregunta A (11 para la caché exacta) y doce
    preguntas casi iguales a A: las parecidas son la primera llamada de cada otra
    pregunta, y ésa nunca la cuenta la exacta."""
    spans = []
    for n in range(12):
        spans += _llamada("dos", n, sim=CABEZA, pregunta="q-A")
    for n in range(12, 24):
        spans += _llamada("dos", n, sim=_a(2))
    store.insert_spans(spans)
    (g,) = _grupos(store, "dos")
    assert (g.questions, g.repeated, g.same_answer) == (1, 12, 12)
    (e,) = store.cacheable_groups("dos", VENTANA, min_mismas=1)
    assert e.same_answer == 11
    assert g.same_answer_cost_usd + e.same_answer_cost_usd == pytest.approx(23 * COSTE)


def test_los_miembros_de_la_ejecucion_de_la_cabeza_no_cuentan(store):
    spans = _llamada("misma", 0, sim=CABEZA, traza="t-0")
    for n in range(1, 8):
        spans += _llamada("misma", n, sim=_a(1), traza="t-0")
    store.insert_spans(spans)
    assert _grupos(store, "misma") == []


def test_cuenta_las_que_dan_la_misma_salida_que_la_cabeza(store):
    store.insert_spans(_paso("parte", ["a"] * 8 + ["b"] * 4))
    (g,) = _grupos(store, "parte")
    assert (g.repeated, g.same_answer) == (11, 7)
    assert g.same_answer_cost_usd == pytest.approx(7 * COSTE)
    (h,) = _cache(store, "parte")
    bajo, alto = wilson(7, 11)
    for cifra in (7 / 11, bajo, alto):
        assert cifras.porcentaje(cifra) in h.summary


def test_la_cabeza_marca_la_respuesta(store):
    store.insert_spans(_paso("cabeza", ["b"] + ["a"] * 11))
    (g,) = _grupos(store, "cabeza", min_mismas=0)
    assert (g.repeated, g.same_answer) == (11, 0)
    assert _cache(store, "cabeza") == []


def test_sin_muestra_no_hay_porcentaje(store):
    store.insert_spans(_paso("poca", ["a"] * 8))  # 7 parecidas: 5 o más, menos de 10
    (h,) = _cache(store, "poca")
    assert "%" not in h.summary
    ficha = insights.detail(store, "poca", VENTANA, h.id)
    assert "%" not in ficha.what_happens
    assert "10" in ficha.what_happens


def test_evaluaciones_errores_y_pocas_no_cuentan(store):
    store.insert_spans(_paso("eval", ["a"] * 12, etiquetas=[EVAL_TAG]))
    store.insert_spans(_paso("error", ["a"] * 12, estado="error"))
    store.insert_spans(_paso("pocas", ["a"] * 4))
    assert _cache(store, "eval") == [] and _grupos(store, "eval") == []
    assert _grupos(store, "error") == []
    assert _cache(store, "pocas") == []


def test_un_paso_con_demasiadas_preguntas_calla(store):
    store.insert_spans(_paso("mucho", ["a"] * 12))
    assert _grupos(store, "mucho", max_preguntas=5) == []
    assert _cache(store, "mucho") != []


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    store.insert_spans(_paso("ficha", ["a"] * 13))
    (h,) = _cache(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha.id == h.id and ficha.window_waste_usd == 0
    assert cifras.dinero_exacto(12 * COSTE) in ficha.savings_calculation
    assert "replay" in ficha.fix_steps[0].code
    assert ficha.detection_query.strip()
