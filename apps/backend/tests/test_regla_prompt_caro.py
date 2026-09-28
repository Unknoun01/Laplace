"""Prompts como fuente de hallazgos: la versión nueva que encarece cada ejecución (D-157).

Lo que se exige, en los dos almacenes:

* la versión que corre ahora, más cara que la anterior, sale en el Diagnóstico con la
  diferencia medida sobre sus ejecuciones;
* nada si ya se volvió atrás, si la nueva es más barata, si falta tráfico en cualquiera
  de las dos, si alguna llamada no tiene tarifa o si el tráfico caro es de una tirada
  de evaluación;
* lo que otra regla ya reclama sobre esas llamadas se descuenta, y si no queda nada no
  hay hallazgo: el mismo dinero no se cuenta dos veces;
* la ficha dice la misma cifra que la tarjeta, y marcarlo como arreglado y volver atrás
  se verifica como cualquier otro hallazgo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import insights, prompts
from laplace_backend.config import Settings
from laplace_backend.insights import prompt_caro
from laplace_backend.seguimiento import comprobar
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)
VENTANA = Window(since=AHORA - timedelta(days=3), until=AHORA + timedelta(minutes=1), days=3)


@pytest.fixture(params=["sqlite", "clickhouse"])
def almacen(request, tmp_path):
    proyecto = f"prompt-caro-{uuid.uuid4().hex[:8]}"
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


def _ejecucion(
    proyecto: str,
    version: int,
    coste: float,
    cuando: datetime,
    *,
    copias: int = 1,
    sin_tarifa: bool = False,
    evaluacion: bool = False,
) -> list[Span]:
    """Una ejecución: la raíz y `copias` llamadas con la versión dada del prompt
    «resumen». Salida larga y entrada corta: que no salte el modelo caro ni el contexto
    fijo. Con `copias` > 1, las llamadas repiten entrada y salta la repetición."""
    traza = uuid.uuid4().hex
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=traza,
        project_id=proyecto,
        name="agente",
        type="agent",
        status="ok",
        start_time=cuando,
        end_time=cuando + timedelta(seconds=2),
        duration_ms=2000.0,
        tags=[EVAL_TAG] if evaluacion else [],
    )
    spans = [raiz]
    for i in range(copias):
        llamada = Span(
            span_id=uuid.uuid4().hex[:16],
            trace_id=traza,
            parent_span_id=raiz.span_id,
            project_id=proyecto,
            name="resumir",
            type="llm",
            status="ok",
            start_time=cuando + timedelta(milliseconds=10 * i),
            end_time=cuando + timedelta(milliseconds=10 * i + 300),
            duration_ms=300.0,
            step_key=f"paso-resumen-v{version}",
            step_label="resumir",
            step_site="agente > resumir",
            dedup_hash=f"d-{traza}" if copias > 1 else f"d-{traza}-{i}",
            prompt_name="resumen",
            prompt_version=version,
        )
        llamada.llm = LLMAttributes(
            request_model="gpt-5.6-luna",
            usage=TokenUsage(input_tokens=300, output_tokens=400),
            cost=Cost(
                input_usd=coste / 2, output_usd=coste / 2, total_usd=coste, unknown=sin_tarifa
            ),
        )
        spans.append(llamada)
    return spans


def _sembrar(
    store,
    proyecto,
    *,
    vieja=(7, 0.004, 10),
    nueva=(8, 0.007, 10),
    nueva_despues: bool = True,
    **kw,
) -> None:
    """Dos versiones, cada una con sus ejecuciones; la que va después es la que corre."""
    (va, ca, na), (vb, cb, nb) = vieja, nueva
    antes, luego = AHORA - timedelta(days=2), AHORA - timedelta(hours=6)
    t_a, t_b = (antes, luego) if nueva_despues else (luego, antes)
    spans = []
    for i in range(na):
        spans += _ejecucion(proyecto, va, ca, t_a + timedelta(minutes=i))
    for i in range(nb):
        spans += _ejecucion(proyecto, vb, cb, t_b + timedelta(minutes=i), **kw)
    store.insert_spans(spans)


def _de_prompts(hallazgos):
    return [h for h in hallazgos if h.kind == "prompt_caro"]


def test_la_version_nueva_mas_cara_sale_en_el_diagnostico(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    vista = insights.overview(store, proyecto, VENTANA)
    (hallazgo,) = _de_prompts(vista.findings)
    assert hallazgo.id == "prompt_caro:resumen:v8"
    # 10 ejecuciones de la v8, 0,003 $ más cada una.
    assert hallazgo.window_waste_usd == pytest.approx(0.03)
    assert "v8" in hallazgo.title and "v7" in hallazgo.title and "«resumen»" in hallazgo.title
    assert hallazgo.step_key == "paso-resumen-v8"
    assert hallazgo.sample_trace_id
    # Suma al evitable del héroe como cualquier otro.
    assert vista.window_avoidable_usd == pytest.approx(0.03)


@pytest.mark.parametrize(
    ("caso", "kw"),
    [
        ("ya se volvió a la v7", {"nueva_despues": False}),
        ("la v8 es más barata", {"nueva": (8, 0.003, 10)}),
        ("un 3 % no es material", {"nueva": (8, 0.00412, 10)}),
        ("pocas ejecuciones de la v8", {"nueva": (8, 0.007, 4)}),
        ("pocas ejecuciones de la v7", {"vieja": (7, 0.004, 4)}),
        ("la v8 sin tarifa", {"sin_tarifa": True}),
        ("la v8 sólo en tiradas de evaluación", {"evaluacion": True}),
    ],
)
def test_cuando_no_hay_nada_que_decir(almacen, caso, kw):
    store, proyecto = almacen
    _sembrar(store, proyecto, **kw)
    assert _de_prompts(insights.detect(store, proyecto, VENTANA)) == [], caso


def test_la_reserva_no_es_una_version_a_la_que_volver(almacen):
    """La versión 0 es el texto del código que el SDK usa si Laplace no responde."""
    store, proyecto = almacen
    _sembrar(store, proyecto, vieja=(0, 0.004, 10), nueva=(1, 0.007, 10))
    assert _de_prompts(insights.detect(store, proyecto, VENTANA)) == []


def test_lo_que_otra_regla_ya_reclama_abarata_la_diferencia(almacen):
    """La v8 repite su llamada tres veces: la repetición reclama las dos copias
    sobrantes de cada ejecución (dos tercios de lo que cuesta la v8). Arreglado eso, la
    diferencia con la v7 también baja a un tercio: los arreglos se componen."""
    store, proyecto = almacen
    # v8: tres llamadas iguales de 0,0025 → 0,0075 por ejecución; 0,075 en total.
    _sembrar(store, proyecto, nueva=(8, 0.0025, 10), copias=3)
    hallazgos = insights.detect(store, proyecto, VENTANA)
    repeticion = sum(h.window_waste_usd for h in hallazgos if h.kind == "repeticion")
    assert repeticion == pytest.approx(0.05)
    (hallazgo,) = _de_prompts(hallazgos)
    # Bruto 10 × (0,0075 − 0,004) = 0,035; queda 1 − 0,05 / 0,075 = un tercio.
    assert hallazgo.window_waste_usd == pytest.approx(0.035 / 3)
    # Lo evitable de esas llamadas nunca pasa de lo que costaron.
    assert repeticion + hallazgo.window_waste_usd <= 0.075


def test_si_otras_reglas_se_llevan_todo_no_queda_nada():
    """Borde: si lo reclamado cubre el coste entero de la versión, no hay hallazgo."""
    from laplace_backend.storage.base import PromptUsage, WindowSummary

    ayer = AHORA - timedelta(days=1)
    vieja = PromptUsage(name="resumen", version=7, traces=10, cost_usd=0.04, last_seen=ayer)
    nueva = PromptUsage(name="resumen", version=8, traces=10, cost_usd=0.07, last_seen=AHORA)
    resumen = WindowSummary()
    assert prompt_caro._prompt_finding(vieja, nueva, 0.07, resumen, 1.0, 1.0) is None
    assert prompt_caro._prompt_finding(vieja, nueva, 0.09, resumen, 1.0, 1.0) is None
    parcial = prompt_caro._prompt_finding(vieja, nueva, 0.035, resumen, 1.0, 1.0)
    assert parcial is not None and parcial.window_waste_usd == pytest.approx(0.015)


def test_la_ficha_dice_lo_mismo_que_la_tarjeta(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    (hallazgo,) = _de_prompts(insights.detect(store, proyecto, VENTANA))
    ficha = insights.detail(store, proyecto, VENTANA, hallazgo.id)
    assert ficha is not None
    assert ficha.title == hallazgo.title
    assert ficha.window_waste_usd == pytest.approx(hallazgo.window_waste_usd)
    assert ficha.fix_steps and "v7" in ficha.fix_steps[1].title
    assert "0,03" in ficha.savings_calculation.replace(".", ",")
    # Un id que no corresponde a nada no inventa una ficha.
    assert insights.detail(store, proyecto, VENTANA, "prompt_caro:resumen:v9") is None
    assert insights.detail(store, proyecto, VENTANA, "prompt_caro:resumen") is None


def test_volver_atras_se_verifica_como_cualquier_arreglo(almacen):
    """Se marca como arreglado, se vuelve a la v7 y la v8 deja de verse: el
    seguimiento lo da por arreglado y cuenta lo ahorrado."""
    store, proyecto = almacen
    marcado = AHORA - timedelta(hours=3)
    spans = []
    for i in range(10):
        spans += _ejecucion(proyecto, 7, 0.004, AHORA - timedelta(days=2, minutes=-i))
    for i in range(10):
        spans += _ejecucion(proyecto, 8, 0.007, marcado - timedelta(hours=2, minutes=i))
    # Después de marcarlo, sólo la v7.
    for i in range(8):
        spans += _ejecucion(proyecto, 7, 0.004, marcado + timedelta(minutes=10 + i))
    store.insert_spans(spans)

    antes = Window(since=marcado - timedelta(days=3), until=marcado, days=3)
    (hallazgo,) = _de_prompts(insights.detect(store, proyecto, antes))
    check = comprobar(store, proyecto, hallazgo, marcado, ahora=AHORA)
    assert check.verdict == "arreglado", check.headline
    assert check.saved and check.saved > 0


def test_las_dos_lecturas_dan_lo_mismo_en_los_dos_almacenes(tmp_path):
    """La lectura de las reglas (`rules=True`) añade las claves de paso y el ejemplo:
    los dos almacenes tienen que dar exactamente lo mismo."""
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando")
    nube.migrate()
    local = SQLiteStore(tmp_path / "laplace.db")
    local.migrate()
    proyecto = f"prompt-caro-par-{uuid.uuid4().hex[:8]}"
    spans = []
    for i in range(6):
        spans += _ejecucion(proyecto, 7, 0.004, AHORA - timedelta(days=1, minutes=i))
        spans += _ejecucion(proyecto, 8, 0.007, AHORA - timedelta(hours=2, minutes=i))
    spans += _ejecucion(proyecto, 8, 0.009, AHORA - timedelta(hours=1), evaluacion=True)
    local.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        for reglas in (False, True):
            a = local.prompt_usage(proyecto, VENTANA, rules=reglas)
            b = nube.prompt_usage(proyecto, VENTANA, rules=reglas)
            clave = lambda u: (u.name, u.version)  # noqa: E731
            a, b = sorted(a, key=clave), sorted(b, key=clave)
            assert [(u.version, u.traces, round(u.cost_usd, 9), u.step_keys,
                     u.sample_step_key, u.sample_trace_id) for u in a] == [
                (u.version, u.traces, round(u.cost_usd, 9), u.step_keys,
                 u.sample_step_key, u.sample_trace_id) for u in b]
        # Sin reglas, la tirada de evaluación cuenta (es gasto); con reglas, no.
        sin = {u.version: u.traces for u in local.prompt_usage(proyecto, VENTANA)}
        con = {u.version: u.traces for u in local.prompt_usage(proyecto, VENTANA, rules=True)}
        assert sin[8] == 7 and con[8] == 6
        assert local.prompt_usage(proyecto, VENTANA, rules=True)[0].step_keys
    finally:
        nube.delete_project(proyecto)


def test_el_minimo_es_el_de_la_pestana_de_prompts():
    """No se importa (el panel importa el motor): se exige que coincidan."""
    assert prompt_caro.MIN_TRACES == prompts.MIN_TRACES_FOR_COST


def test_prompts_sin_tiradas_da_lo_mismo_en_los_dos_almacenes(tmp_path):
    """D-160: la pestaña de Prompts lee sin tiradas de evaluación; las dos lecturas que
    lo hacen (veredictos por traza y prompts observados) coinciden en los dos almacenes."""
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando")
    nube.migrate()
    local = SQLiteStore(tmp_path / "laplace.db")
    local.migrate()
    proyecto = f"prompt-sin-eval-{uuid.uuid4().hex[:8]}"
    reales = _ejecucion(proyecto, 8, 0.007, AHORA - timedelta(hours=2))
    tirada = _ejecucion(proyecto, 8, 0.001, AHORA - timedelta(hours=1), evaluacion=True)
    for almacen in (local, nube):
        almacen.insert_spans(reales + tirada)
    ids = [reales[0].trace_id, tirada[0].trace_id]
    try:
        for almacen in (local, nube):
            con = almacen.prompt_versions_by_trace(proyecto, ids)
            sin = almacen.prompt_versions_by_trace(proyecto, ids, sin_evaluaciones=True)
            assert set(con) == set(ids)
            assert set(sin) == {reales[0].trace_id}
            todos = almacen.observed_prompts(proyecto, VENTANA)
            reglas = almacen.observed_prompts(proyecto, VENTANA, rules=True)
            assert sum(o.traces for o in todos) == 2
            assert sum(o.traces for o in reglas) == 1
    finally:
        nube.delete_project(proyecto)
