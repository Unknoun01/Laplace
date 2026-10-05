"""Historial que crece sin límite (D-195): la entrada de un paso crece en cada turno.

Lo que se exige, en los dos almacenes:

* el caso de libro: cuánto crece por turno y cuántos tokens de historial se reenvían,
  exactos;
* que la conversación sea la sesión cuando la hay, aunque cada turno sea otra ejecución;
* que un historial que se recorta (la entrada baja alguna vez) no salga, ni uno corto, ni
  uno que apenas crece, ni uno que sólo pasa en una o dos conversaciones;
* que **nunca** ponga dinero, aunque el modelo tenga tarifa: no hay un tope que se pueda
  defender desde las trazas;
* que las tiradas de evaluación no cuenten;
* que no reclame tokens que ya reclama el contexto fijo;
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
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)
MODELO = "gpt-5.6-terra"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=2), days=1)
KIND = "historial"


@pytest.fixture(params=["sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "sqlite":
        local = SQLiteStore(tmp_path / "h.db")
        local.migrate()
        yield local
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d195_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    nube = ClickHouseStore(Settings(clickhouse_database=base))
    nube.migrate()
    try:
        yield nube
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _llamada(
    proyecto: str, traza: str, i: int, entrada: int, *, sesion: str | None = None,
    paso: str = "conversar", tags=None,
) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name=f"chat {MODELO}", type="llm", status="ok", start_time=inicio,
        end_time=inicio + timedelta(milliseconds=600), duration_ms=600.0,
        step_key=f"k-{paso}", step_label=paso, step_site=f"agente > {paso}",
        dedup_hash=uuid.uuid4().hex, session_id=sesion, tags=list(tags or []),
    )
    span.llm = LLMAttributes(
        system="openai", request_model=MODELO, response_model=MODELO,
        usage=TokenUsage(input_tokens=entrada, output_tokens=300),
        cost=Cost(total_usd=entrada * 1e-6),
        finish_reasons=["stop"],
    )
    return span


def _conversaciones(
    proyecto: str, n: int, entradas: list[int], *, por_sesion: bool = False, **kw
) -> list[Span]:
    spans = []
    for c in range(n):
        for turno, entrada in enumerate(entradas):
            if por_sesion:
                traza, sesion = f"{proyecto}-{c}-{turno}", f"sesion-{c}"
            else:
                traza, sesion = f"{proyecto}-{c}", None
            spans.append(
                _llamada(proyecto, traza, c * 100 + turno, entrada, sesion=sesion, **kw)
            )
    return spans


CRECE = [1_000, 1_500, 2_000, 2_500, 3_000]


def _historiales(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def test_el_caso_de_libro(store):
    store.insert_spans(_conversaciones("libro", 4, CRECE))
    (h,) = _historiales(store, "libro")
    # Por conversación: 0 + 500 + 1.000 + 1.500 + 2.000 tokens por encima del primer turno.
    assert h.window_waste_tokens == 4 * 5_000
    assert "500" in h.title
    assert h.step_key == "k-conversar"
    assert h.id == f"{KIND}:k-conversar"
    assert h.sample_trace_id.startswith("libro-")


def test_nunca_pone_dinero_aunque_haya_tarifa(store):
    store.insert_spans(_conversaciones("dinero", 4, CRECE))
    (h,) = _historiales(store, "dinero")
    assert h.window_waste_usd == 0
    assert h.monthly_saving_usd is None
    assert not h.costs_money
    assert h.cost_unavailable, "tiene que decir por qué no hay dinero"


def test_la_conversacion_es_la_sesion_cuando_la_hay(store):
    store.insert_spans(_conversaciones("sesion", 4, CRECE, por_sesion=True))
    (h,) = _historiales(store, "sesion")
    assert h.window_waste_tokens == 4 * 5_000


def test_un_historial_que_se_recorta_no_sale(store):
    store.insert_spans(_conversaciones("recorta", 4, [1_000, 1_500, 2_000, 1_200, 1_700]))
    assert _historiales(store, "recorta") == []


def test_pocos_turnos_no_es_un_historial(store):
    turnos = CRECE[: insights.MIN_TURNOS_HISTORIAL - 1]
    store.insert_spans(_conversaciones("corto", 4, turnos))
    assert _historiales(store, "corto") == []


def test_si_apenas_crece_no_dice_nada(store):
    poco = insights.MIN_CRECIMIENTO_POR_TURNO - 1
    store.insert_spans(_conversaciones("plano", 4, [1_000 + poco * k for k in range(5)]))
    assert _historiales(store, "plano") == []


def test_hacen_falta_varias_conversaciones(store):
    store.insert_spans(
        _conversaciones("pocas", insights.MIN_CONVERSACIONES_HISTORIAL - 1, CRECE)
    )
    assert _historiales(store, "pocas") == []


def test_las_tiradas_de_evaluacion_no_cuentan(store):
    store.insert_spans(_conversaciones("eval", 4, CRECE, tags=[EVAL_TAG]))
    assert _historiales(store, "eval") == []


def test_no_reclama_los_tokens_del_contexto_fijo(store):
    """El contexto fijo reclama el suelo común de la entrada en cada llamada; esto, lo que
    se suma por encima del primer turno. Juntos no pasan de lo que se mandó."""
    entradas = [3_000, 3_600, 4_200, 4_800, 5_400]
    store.insert_spans(_conversaciones("fijo", 4, entradas))
    hallazgos = insights.detect(store, "fijo", VENTANA)
    assert KIND in {f.kind for f in hallazgos}
    mandado = 4 * sum(entradas)
    reclamado = sum(f.window_waste_tokens for f in hallazgos if f.step_key == "k-conversar")
    assert reclamado <= mandado


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    store.insert_spans(_conversaciones("ficha", 4, CRECE))
    (h,) = _historiales(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha is not None
    assert ficha.title == h.title
    assert ficha.window_waste_tokens == h.window_waste_tokens
    assert ficha.window_waste_usd == 0
    assert ficha.fix_steps and ficha.why and ficha.what_happens
    assert "SELECT" in ficha.detection_query
    assert "replay" in ficha.savings_note, "dice cómo conseguir un tope que se pueda defender"
    entradas = [s.llm.usage.input_tokens for s in ficha.evidence if s.llm]
    assert entradas == CRECE, "la evidencia es una conversación entera, en orden"


def test_no_cuenta_las_llamadas_que_reclaman_la_truncada_y_la_del_json(store):
    """Una respuesta cortada y rehecha es un turno de más en la secuencia: si contara,
    el historial crecería menos por turno de lo que crece de verdad."""
    spans = []
    for c in range(4):
        traza = f"rehecha-{c}"
        cortada = _llamada("rehecha", traza, c * 100, 1_000)
        cortada.llm.finish_reasons = ["length"]
        rota = _llamada("rehecha", traza, c * 100 + 1, 1_000)
        rota.output_json = "roto"
        spans += [cortada, rota]
        spans += [
            _llamada("rehecha", traza, c * 100 + 2 + k, entrada)
            for k, entrada in enumerate(CRECE)
        ]
    store.insert_spans(spans)
    (h,) = _historiales(store, "rehecha")
    assert "500" in h.title, h.title
    assert h.window_waste_tokens == 4 * 5_000
