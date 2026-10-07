"""Trabajo que podría ir por la API de lotes (D-199).

Lo que se exige, en los dos almacenes:

* que con la etiqueta `laplace-puede-esperar` el ahorro sea el descuento publicado del
  proveedor (la mitad en OpenAI y Anthropic), exacto;
* que sólo cuenten las llamadas que no esperan la respuesta de otra: por lotes, cada
  salto de una cadena puede tardar hasta un día;
* que no entre lo que ya va por lotes, ni las tiradas de evaluación, ni un puñado de
  llamadas;
* que sin descuento publicado no haya dinero, y se diga;
* que sin la etiqueta sea una pregunta, sin dinero, y sólo cuando el resto del proyecto
  dice quién espera (usuario o sesión) y estas ejecuciones no: si nadie lo dice nunca,
  no hay con qué comparar y se calla;
* que no reclame lo que ya reclaman las demás reglas sobre el mismo paso;
* y que su ficha cuente lo mismo que su tarjeta.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.pricing import get_price_table
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG, PUEDE_ESPERAR_TAG

from laplace_backend import cifras, insights
from laplace_backend.config import Settings
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(hours=2)
MODELO = "gpt-5.6-terra"
SIN_LOTES = "aihubmix/gemini-3.1-pro-preview"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=3), days=1)
KIND = "lotes"
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
    base = f"prueba_d199_{uuid.uuid4().hex[:8]}"
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
        status="ok", start_time=inicio, end_time=inicio + timedelta(milliseconds=ms),
        duration_ms=ms, **kw,
    )


def _ejecuciones(
    proyecto: str, n: int, *, etiquetas=(PUEDE_ESPERAR_TAG,), llamadas: int = 1,
    a_la_vez: bool = False, modelo: str = MODELO, nivel: str = "standard",
    paso: str = "clasificar", usuario: str | None = None, sesion: str | None = None,
    salida: int = 300, coste: float | None = COSTE, desde: int = 0,
) -> list[Span]:
    """`n` ejecuciones: una raíz con las etiquetas y `llamadas` al modelo, una detrás de
    otra (cada una empieza cuando acaba la anterior) o todas a la vez."""
    spans = []
    for e in range(n):
        traza = f"{proyecto}-{paso}-{desde + e}"
        base = AHORA + timedelta(seconds=10 * (desde + e))
        spans.append(
            _span(
                proyecto, traza, base, 9_000, name="agente", type="agent",
                tags=list(etiquetas), user_id=usuario, session_id=sesion,
            )
        )
        for k in range(llamadas):
            inicio = base if a_la_vez else base + timedelta(seconds=2 * k)
            llamada = _span(
                proyecto, traza, inicio, 1_000, name=f"chat {modelo}", type="llm",
                step_key=f"k-{paso}", step_label=paso, step_site=f"agente > {paso}",
                dedup_hash=uuid.uuid4().hex, user_id=usuario, session_id=sesion,
            )
            llamada.llm = LLMAttributes(
                system="openai", request_model=modelo, response_model=modelo,
                usage=TokenUsage(input_tokens=1_500, output_tokens=salida),
                cost=Cost(total_usd=coste) if coste is not None else Cost(),
                billing_tier=nivel,
            )
            spans.append(llamada)
    return spans


def _lotes(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == KIND]


def test_lo_que_puede_esperar_cuesta_la_mitad_por_lotes(store):
    store.insert_spans(_ejecuciones("mitad", 25))
    (h,) = _lotes(store, "mitad")
    assert h.costs_money
    assert h.window_waste_usd == pytest.approx(25 * COSTE * 0.5)
    assert h.id == f"{KIND}:k-clasificar:{MODELO}"
    assert h.step_key == "k-clasificar"


def test_las_llamadas_encadenadas_no_cuentan(store):
    """La segunda llamada empieza cuando acaba la primera: espera su respuesta. Por
    lotes, cada salto puede tardar hasta un día, así que sólo cuenta la primera."""
    store.insert_spans(_ejecuciones("cadena", 25, llamadas=3))
    (h,) = _lotes(store, "cadena")
    assert h.window_waste_usd == pytest.approx(25 * COSTE * 0.5)
    ficha = insights.detail(store, "cadena", VENTANA, h.id)
    assert cifras.miles(50) in ficha.what_happens, "tiene que decir cuántas encadenan"


def test_las_que_van_a_la_vez_cuentan_todas(store):
    store.insert_spans(_ejecuciones("vez", 25, llamadas=2, a_la_vez=True))
    (h,) = _lotes(store, "vez")
    assert h.window_waste_usd == pytest.approx(50 * COSTE * 0.5)


def test_lo_que_ya_va_por_lotes_no_se_propone(store):
    store.insert_spans(_ejecuciones("yalotes", 25, nivel="batch"))
    assert _lotes(store, "yalotes") == []


def test_las_tiradas_de_evaluacion_no_cuentan(store):
    store.insert_spans(_ejecuciones("eval", 25, etiquetas=(PUEDE_ESPERAR_TAG, EVAL_TAG)))
    assert _lotes(store, "eval") == []


def test_pocas_llamadas_no_dicen_nada(store):
    store.insert_spans(_ejecuciones("pocas", 10))
    assert _lotes(store, "pocas") == []


def test_sin_descuento_publicado_no_hay_dinero(store):
    """El modelo tiene tarifa, pero su proveedor no publica descuento por lotes: no se
    supone la mitad. Se dice que no lo sabemos."""
    store.insert_spans(_ejecuciones("sindescuento", 25, modelo=SIN_LOTES))
    (h,) = _lotes(store, "sindescuento")
    assert not h.costs_money
    assert h.window_waste_usd == 0
    assert h.cost_unavailable


def test_sin_etiqueta_es_una_pregunta_si_el_resto_dice_quien_espera(store):
    """El resto del proyecto lleva usuario; estas ejecuciones no. Es un indicio de que
    nadie espera la respuesta, no una prueba: no hay dinero en el total, y el resumen
    dice cuánto sería si pudieran esperar."""
    store.insert_spans(
        _ejecuciones("indicio", 25, etiquetas=(), paso="atender", usuario="ana")
        + _ejecuciones("indicio", 25, etiquetas=(), desde=100)
    )
    (h,) = _lotes(store, "indicio")
    assert h.step_key == "k-clasificar", "las que llevan usuario no son candidatas"
    assert not h.costs_money
    assert h.window_waste_usd == 0
    assert cifras.dinero(25 * COSTE * 0.5) in h.summary
    ficha = insights.detail(store, "indicio", VENTANA, h.id)
    assert PUEDE_ESPERAR_TAG in ficha.fix_steps[0].code, "lo primero es marcarlo"


def test_la_sesion_tambien_dice_que_alguien_espera(store):
    store.insert_spans(
        _ejecuciones("sesion", 25, etiquetas=(), paso="atender", sesion="s-1")
        + _ejecuciones("sesion", 25, etiquetas=(), paso="clasificar", sesion="s-2", desde=100)
    )
    assert _lotes(store, "sesion") == []


def test_sin_etiqueta_y_sin_nadie_con_quien_comparar_se_calla(store):
    store.insert_spans(_ejecuciones("callado", 25, etiquetas=()))
    assert _lotes(store, "callado") == []
    # Y ni siquiera se abren: sin nadie con quien comparar no son candidatas, y en un
    # proyecto que no pone usuario nunca la consulta no recorre todas sus ejecuciones.
    assert store.deferrable_groups("callado", VENTANA, min_llamadas=1) == []


def test_no_reclama_lo_que_ya_reclama_el_modelo_caro(store):
    """Respuestas cortas en un modelo caro: el modelo caro reclama su parte del paso, y
    lo que queda es sobre lo que se aplica el descuento por lotes (D-117)."""
    precio = get_price_table().compute(MODELO, input_tokens=1_500, output_tokens=20)
    store.insert_spans(
        _ejecuciones("encadena", 25, salida=20, coste=precio.total_usd)
    )
    hallazgos = insights.detect(store, "encadena", VENTANA)
    (caro,) = [f for f in hallazgos if f.kind == "modelo_caro"]
    (lotes,) = [f for f in hallazgos if f.kind == KIND]
    paso = 25 * precio.total_usd
    assert lotes.window_waste_usd == pytest.approx(0.5 * (paso - caro.window_waste_usd))
    assert caro.window_waste_usd + lotes.window_waste_usd <= paso


def test_la_ficha_cuenta_lo_mismo_que_la_tarjeta(store):
    store.insert_spans(_ejecuciones("ficha", 25, llamadas=2))
    (h,) = _lotes(store, "ficha")
    ficha = insights.detail(store, "ficha", VENTANA, h.id)
    assert ficha is not None
    assert ficha.window_waste_usd == h.window_waste_usd
    assert ficha.detection_query
    codigo = " ".join(p.code or "" for p in ficha.fix_steps)
    assert "client.batches.create" in codigo, "ya está marcada: el arreglo es el lote"
    assert PUEDE_ESPERAR_TAG not in codigo
