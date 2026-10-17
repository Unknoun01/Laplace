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

Y en ClickHouse, dos veces: en crudo y con las horas calculadas en los preagregados
(D-207), que tienen que dar lo mismo. Las conversaciones que cruzan horas, las copias en
otra hora y los bordes de la ventana tienen sus propias pruebas al final.
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


@pytest.fixture(params=["sqlite", "clickhouse", "clickhouse-calculado"])
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
    if request.param == "clickhouse-calculado":

        class Calculado(ClickHouseStore):
            """Calcula los preagregados de lo que escribe, como haría el bucle de fondo."""

            def insert_spans(self, spans):
                hechos = super().insert_spans(spans)
                while self.recalcular_preagregados(limite=500, quieta_s=0):
                    pass
                return hechos

        nube = Calculado(Settings(clickhouse_database=base))
    else:
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


def test_una_llamada_excluida_no_hace_que_el_filtro_previo_la_deje_fuera(store):
    """El filtro previo de ClickHouse pedía crecer 200 tokens por cada fila menos una,
    contando también las que la regla no cuenta (D-198). Una conversación que crece justo
    lo que se pide y trae un JSON roto rehecho tenía cinco filas y cuatro turnos: se
    quedaba fuera en ClickHouse y salía en SQLite."""
    spans = []
    for c in range(4):
        traza = f"justa-{c}"
        rota = _llamada("justa", traza, c * 100, 1_200)
        rota.output_json = "roto"
        spans.append(rota)
        spans += [
            _llamada("justa", traza, c * 100 + 1 + k, entrada)
            for k, entrada in enumerate([1_000, 1_200, 1_400, 1_600])
        ]
    store.insert_spans(spans)
    (h,) = _historiales(store, "justa")
    assert h.window_waste_tokens == 4 * (200 + 400 + 600)


def test_las_copias_se_cuentan_en_toda_la_traza(store):
    """Una llamada repetida tres veces en la traza es de la repetición, aunque dos de las
    copias las haga otro paso: `dedup_hash` no lleva el paso (D-198). ClickHouse las
    contaba sólo entre las conversaciones que pasaban su filtro previo, y si el otro
    paso no lo pasaba, el turno repetido se quedaba en el historial."""
    spans = []
    for c in range(4):
        traza = f"copias-{c}"
        turnos = [
            _llamada("copias", traza, c * 100 + k, entrada)
            for k, entrada in enumerate([1_000, 1_500, 2_000, 2_500])
        ]
        repetida = f"rep-{c}"
        turnos[1].dedup_hash = repetida
        for k in range(2):
            otra = _llamada("copias", traza, c * 100 + 10 + k, 1_500, paso="resumir")
            otra.dedup_hash = repetida
            spans.append(otra)
        spans += turnos
    store.insert_spans(spans)
    # Sin el turno repetido quedan tres turnos por conversación: no es un historial.
    assert _historiales(store, "copias") == []


def test_una_llamada_que_no_cuenta_al_final_no_esconde_el_historial(store):
    """El filtro previo de ClickHouse mira si la primera llamada es la menor y la última
    la mayor (D-198). Tiene que mirarlo sólo entre las que cuentan: una respuesta cortada
    al final, con la entrada recortada, no es un turno del historial."""
    spans = []
    for c in range(4):
        traza = f"final-{c}"
        spans += [
            _llamada("final", traza, c * 100 + k, entrada) for k, entrada in enumerate(CRECE)
        ]
        cortada = _llamada("final", traza, c * 100 + 50, 500)
        cortada.llm.finish_reasons = ["length"]
        spans.append(cortada)
    store.insert_spans(spans)
    (h,) = _historiales(store, "final")
    assert h.window_waste_tokens == 4 * 5_000


def test_un_lote_reenviado_no_convierte_dos_llamadas_en_tres_copias(store):
    """Las copias son spans distintos, no filas: un lote reenviado y aún sin fusionar
    duplica las filas en ClickHouse, y dos llamadas iguales parecían cuatro (D-198)."""
    if hasattr(store, "_client"):
        store._client.command("SYSTEM STOP MERGES spans")
    spans = []
    for c in range(4):
        traza = f"reenvio-{c}"
        turnos = [
            _llamada("reenvio", traza, c * 100 + k, entrada)
            for k, entrada in enumerate([1_000, 1_500, 1_500, 2_000, 2_500])
        ]
        turnos[1].dedup_hash = turnos[2].dedup_hash = f"dos-{c}"
        spans += turnos
    store.insert_spans(spans)
    store.insert_spans(spans)
    (h,) = _historiales(store, "reenvio")
    assert h.window_waste_tokens == 4 * (500 + 500 + 1_000 + 1_500)


# ---------------------------------------------------------------------------------
# Lo que cruza horas (D-207): con las horas calculadas, la conversación sale de juntar
# las filas de cada hora, y lo que la hora no ve entero se calcula en crudo.
# ---------------------------------------------------------------------------------

BASE = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
#: De 9:30 a 14:00: la hora de las 9 la corta la ventana; de 10 a 13, enteras.
FIJA = Window(since=BASE - timedelta(minutes=30), until=BASE + timedelta(hours=4), days=1)


def _turno(proyecto, traza, cuando, entrada, *, sesion=None, paso="conversar", copia=None):
    span = _llamada(proyecto, traza, 0, entrada, sesion=sesion, paso=paso)
    span.start_time, span.end_time = cuando, cuando + timedelta(milliseconds=600)
    if copia:
        span.dedup_hash = copia
    return span


def _cruces(proyecto: str) -> list[Span]:
    spans = []
    turnos = [timedelta(seconds=40 * k) for k in range(5)]
    # Sesiones de 10:58 a 11:00:40, un turno por ejecución: tres turnos en la hora de
    # las 10 y dos en la de las 11. La cuarta baja justo en el borde de las dos horas.
    for c in range(4):
        entradas = [1_000, 1_500, 2_600, 2_500, 3_000] if c == 3 else CRECE
        for k, entrada in enumerate(entradas):
            cuando = BASE + timedelta(minutes=58) + turnos[k]
            spans.append(_turno(proyecto, f"s{c}-{k}", cuando, entrada, sesion=f"ses-{c}"))
    # Ejecuciones sin sesión que cruzan de las 11 a las 12, con dos turnos en cada hora:
    # ninguna hora tiene sola una conversación de cuatro turnos.
    for c in range(3):
        for k, entrada in enumerate([1_000, 1_500, 2_000, 2_500]):
            cuando = BASE + timedelta(hours=1, minutes=59) + turnos[k]
            spans.append(_turno(proyecto, f"r{c}", cuando, entrada, paso="resumir"))
    # A las 13, cuatro ejecuciones de cuatro turnos. En la última, el segundo turno se
    # repite dos veces más a las 12:50 desde otro paso: tres copias en la ventana, y fuera
    # del historial, aunque en su hora sólo haya una.
    for c in range(4):
        for k, entrada in enumerate([1_000, 1_500, 2_000, 2_500]):
            cuando = BASE + timedelta(hours=3, minutes=10) + turnos[k]
            copia = "rep-x3" if c == 3 and k == 1 else None
            spans.append(_turno(proyecto, f"x{c}", cuando, entrada, paso="extraer", copia=copia))
    for k in range(2):
        cuando = BASE + timedelta(hours=2, minutes=50, seconds=k)
        spans.append(_turno(proyecto, "x3", cuando, 1_500, paso="otro", copia="rep-x3"))
    # Sesiones a las 12:20, un turno por ejecución. En la cuarta, el tercer turno se
    # repite dos veces más a las 13:30 desde otro paso: fuera del historial, aunque en su
    # hora cuente. Su ejecución cruza la hora y no trae ninguna otra llamada de la sesión,
    # así que en crudo la fila ya no tiene ninguna traza que cruce: lo que decide de dónde
    # sale es la tabla, o la sesión se perdería.
    for c in range(4):
        entradas = CRECE if c == 3 else [1_000, 1_500, 2_000, 2_500]
        for k, entrada in enumerate(entradas):
            cuando = BASE + timedelta(hours=2, minutes=20) + turnos[k]
            copia = "rep-v3" if c == 3 and k == 2 else None
            spans.append(_turno(proyecto, f"v{c}-{k}", cuando, entrada, sesion=f"rev-{c}",
                                paso="revisar", copia=copia))
    for k in range(2):
        cuando = BASE + timedelta(hours=3, minutes=30, seconds=k)
        spans.append(_turno(proyecto, "v3-2", cuando, 2_000, paso="otro", copia="rep-v3"))
    # A las 9:35, tres ejecuciones de cuatro turnos cuyo segundo turno se repite dos veces
    # a las 9:20, antes de que empiece la ventana: en ella hay una copia sola y cuenta.
    for c in range(3):
        for k, entrada in enumerate([1_000, 1_500, 2_000, 2_500]):
            cuando = BASE - timedelta(minutes=25) + turnos[k]
            copia = f"rep-b{c}" if k == 1 else None
            spans.append(_turno(proyecto, f"b{c}", cuando, entrada, paso="borde", copia=copia))
        for k in range(2):
            cuando = BASE - timedelta(minutes=40, seconds=-k)
            spans.append(_turno(proyecto, f"b{c}", cuando, 1_500, paso="otro", copia=f"rep-b{c}"))
    return spans


#: (conversaciones, llamadas, trazas, tokens de historial, turnos de la más larga)
CRUCES = {
    "k-conversar": (3, 15, 15, 3 * 5_000, 5),
    "k-resumir": (3, 12, 3, 3 * 3_000, 4),
    "k-extraer": (3, 12, 3, 3 * 3_000, 4),
    "k-borde": (3, 12, 3, 3 * 3_000, 4),
    # La cuarta, sin el turno repetido: 1.000, 1.500, 2.500 y 3.000.
    "k-revisar": (4, 16, 16, 3 * 3_000 + 4_000, 4),
}


def _grupos(store, proyecto: str, ventana: Window = FIJA):
    return store.history_groups(
        proyecto, ventana,
        min_turnos=insights.historial.MIN_TURNOS_HISTORIAL,
        min_crece=insights.historial.MIN_CRECIMIENTO_POR_TURNO,
        min_conversaciones=insights.historial.MIN_CONVERSACIONES_HISTORIAL,
        min_repeats=insights.MIN_REPEATS,
        min_vueltas=insights.MIN_VUELTAS_BUCLE,
    )


def _de_los_preagregados(store, proyecto: str, ventana: Window = FIJA) -> bool:
    """Si la lectura de ClickHouse sale de los preagregados y no entera en crudo."""
    from laplace_backend.storage import preagregados

    umbrales = dict(preagregados.UMBRALES_HISTORIAL)
    return preagregados.historial(
        store._client, proyecto, ventana.since, ventana.until, umbrales
    ) is not None


def test_lo_que_cruza_horas_sale_igual_en_los_tres_caminos(store):
    store.insert_spans(_cruces("cruces"))
    grupos = _grupos(store, "cruces")
    vistos = {
        g.step_key: (g.conversations, g.calls, g.traces, g.history_tokens, g.max_turns)
        for g in grupos
    }
    assert vistos == CRUCES
    conversar = next(g for g in grupos if g.step_key == "k-conversar")
    assert conversar.growth_per_turn == 500
    assert (conversar.sample_first_input, conversar.sample_last_input) == (1_000, 3_000)
    assert conversar.sample_conversation in {"s:ses-0", "s:ses-1", "s:ses-2"}
    assert conversar.last_seen == BASE + timedelta(minutes=58, seconds=160)
    if type(store).__name__ == "Calculado":
        assert _de_los_preagregados(store, "cruces")


def test_una_hora_sucia_en_medio_se_lee_en_crudo_y_se_junta(store):
    """Llega tarde un turno de las 11 de una sesión: esa hora está sucia, y la sesión sale
    de juntar su fila de las 10, de la tabla, con su hora de las 11, en crudo."""
    store.insert_spans(_cruces("tarde"))
    tarde = _turno("tarde", "s0-5", BASE + timedelta(minutes=61), 3_500, sesion="ses-0")
    if hasattr(store, "_client"):
        from laplace_backend.storage.clickhouse import ClickHouseStore

        # Sin recalcular: como un lote que llega entre dos vueltas del bucle de fondo.
        ClickHouseStore.insert_spans(store, [tarde])
    else:
        store.insert_spans([tarde])
    grupos = _grupos(store, "tarde")
    vistos = {g.step_key: (g.conversations, g.calls, g.history_tokens) for g in grupos}
    assert vistos["k-conversar"] == (3, 16, 3 * 5_000 + 2_500)
    if type(store).__name__ == "Calculado":
        assert _de_los_preagregados(store, "tarde")


def test_los_umbrales_de_los_preagregados_son_los_de_la_regla():
    from laplace_backend.storage import preagregados

    assert preagregados.UMBRALES_HISTORIAL == {
        "min_repeats": insights.MIN_REPEATS,
        "min_vueltas": insights.MIN_VUELTAS_BUCLE,
        "min_turnos": insights.historial.MIN_TURNOS_HISTORIAL,
        "min_crece": insights.historial.MIN_CRECIMIENTO_POR_TURNO,
    }


def test_una_instalacion_de_antes_recalcula_sus_horas(store):
    """Una instalación con horas ya calculadas y sin `pre_historial`: `migrate` crea la
    tabla vacía, y esas horas se leerían limpias y sin historial. Tienen que quedar sucias
    hasta recalcularlas, y mientras tanto leerse en crudo (D-207)."""
    if type(store).__name__ != "Calculado":
        pytest.skip("sólo con preagregados calculados")
    from laplace_backend.storage import preagregados

    store.insert_spans(_cruces("antes"))
    store._client.command("DROP TABLE pre_historial SYNC")
    store.migrate()
    assert preagregados.pendientes(store._client, quieta_s=0), "las horas, sucias otra vez"
    vistos = {g.step_key: (g.conversations, g.calls, g.traces, g.history_tokens, g.max_turns)
              for g in _grupos(store, "antes")}
    assert vistos == CRUCES
    while store.recalcular_preagregados(limite=500, quieta_s=0):
        pass
    assert _de_los_preagregados(store, "antes")
    vistos = {g.step_key: (g.conversations, g.calls, g.traces, g.history_tokens, g.max_turns)
              for g in _grupos(store, "antes")}
    assert vistos == CRUCES


def test_un_lote_reenviado_de_lo_que_cruza_no_cuenta_dos_veces(store):
    """Lo crudo pide sin `FINAL` las trazas que cruzan (D-207): un lote reenviado y aún
    sin fusionar duplica sus filas, y cada turno contaría dos veces."""
    if hasattr(store, "_client"):
        store._client.command("SYSTEM STOP MERGES spans")
    spans = _cruces("reenviado")
    store.insert_spans(spans)
    store.insert_spans(spans)
    grupos = _grupos(store, "reenviado")
    vistos = {
        g.step_key: (g.conversations, g.calls, g.traces, g.history_tokens, g.max_turns)
        for g in grupos
    }
    assert vistos == CRUCES
    if type(store).__name__ == "Calculado":
        assert _de_los_preagregados(store, "reenviado")


def test_una_conversacion_con_turnos_en_crudo_no_la_deja_fuera_el_filtro(store):
    """El filtro previo de la tabla (D-222) mira sólo las filas que se leen: una
    conversación con dos turnos en la tabla no llega a cuatro, pero con los de lo crudo
    sí. Tienen que salir las que tienen turnos en el tramo del borde y las que tienen una
    fila que no se lee porque una de sus trazas cruza."""
    spans = []
    for c in range(3):
        # Dos turnos a las 9:50, que la ventana corta, y dos a las 10, de la tabla.
        for k, minuto in enumerate([-10, -9, 5, 6]):
            cuando = BASE + timedelta(minutes=minuto)
            spans.append(_turno("partida", f"pb{c}-{k}", cuando, CRECE[k], sesion=f"pb-{c}",
                                paso="borde"))
        # Dos turnos a las 10:30, de la tabla, y dos a las 11. La última ejecución sigue
        # a las 12 con otro paso: cruza, y la fila de las 11 no se lee.
        for k, minuto in enumerate([30, 31, 80, 119]):
            cuando = BASE + timedelta(minutes=minuto)
            spans.append(_turno("partida", f"pc{c}-{k}", cuando, CRECE[k], sesion=f"pc-{c}",
                                paso="cruce"))
        spans.append(_turno("partida", f"pc{c}-3", BASE + timedelta(minutes=121), 900,
                            sesion=f"pc-{c}", paso="otro"))
    store.insert_spans(spans)
    vistos = {
        g.step_key: (g.conversations, g.calls, g.traces, g.history_tokens, g.max_turns)
        for g in _grupos(store, "partida")
    }
    assert vistos == {
        "k-borde": (3, 12, 12, 3 * 3_000, 4),
        "k-cruce": (3, 12, 12, 3 * 3_000, 4),
    }
    if type(store).__name__ == "Calculado":
        assert _de_los_preagregados(store, "partida")
