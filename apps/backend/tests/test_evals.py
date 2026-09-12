"""Evaluaciones: anotación, conjuntos de casos y A vs B.

Tres cosas se comprueban aquí, y las tres son sobre no mentir:

1. **Que el veredicto de persona y el de máquina no se toquen.** Ni en la base —una
   anotación humana no puede llevar coste de juez— ni en el cálculo —no se promedian—
   ni en la API —la ruta de anotar no puede crear un veredicto de modelo—.
2. **Que ningún porcentaje salga sin su guarda.** Con cuatro casos se enseñan cuatro
   casos, no un 94 %. Y en A vs B no se declara ganador sobre una diferencia que cabe
   dentro del margen, que es la cuarta cara del mismo error que ya mordió tres veces.
3. **Que el modo local pueda evaluar.** Anotar es el gesto más básico de la pestaña y
   antes en local no había dónde guardarlo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from laplace.schema import (
    Annotation,
    Cost,
    Dataset,
    DatasetItem,
    EvalRun,
    EvalRunItem,
    JudgeRun,
    LLMAttributes,
    Span,
    TokenUsage,
)

from laplace_backend import evals
from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceCost
from laplace_backend.storage.metadata import (
    MetadataUnavailable,
    NullMetadataStore,
    SQLiteMetadataStore,
)
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------
# Constructores
# ---------------------------------------------------------------------------------


def _run(variant: str, n: int, *, crashed: int = 0, prefijo: str = "") -> EvalRun:
    items = [
        EvalRunItem(case_id=f"c{i}", trace_id=f"{prefijo}t{i}", failed=i < crashed)
        for i in range(n)
    ]
    return EvalRun(
        id=f"run_{variant}",
        project_id="p",
        dataset_id="ds_1",
        variant=variant,
        created_at=AHORA,
        items=items,
    )


def _anns(prefijo: str, n: int, *, human_pass: int = 0, judge_pass: int = 0,
          human: int = 0, judge: int = 0) -> dict[str, list[Annotation]]:
    """Anotaciones para `n` trazas: las `human` primeras con veredicto de persona
    (de las cuales `human_pass` aciertan) y las `judge` primeras con el del juez."""
    salida: dict[str, list[Annotation]] = {}
    for i in range(n):
        trace = f"{prefijo}t{i}"
        lista: list[Annotation] = []
        if i < human:
            lista.append(
                Annotation(
                    id=f"h{i}",
                    trace_id=trace,
                    source="human",
                    verdict="pass" if i < human_pass else "fail",
                    author="enol",
                    created_at=AHORA,
                )
            )
        if i < judge:
            lista.append(
                Annotation(
                    id=f"j{i}",
                    trace_id=trace,
                    source="llm_judge",
                    verdict="pass" if i < judge_pass else "fail",
                    author="modelo-juez",
                    created_at=AHORA,
                    judge=JudgeRun(
                        model="modelo-juez",
                        input_tokens=500,
                        output_tokens=20,
                        cost_usd=0.0004,
                        prompt_version="v1",
                    ),
                )
            )
        if lista:
            salida[trace] = lista
    return salida


def _costs(prefijo: str, n: int, por_traza: float) -> dict[str, TraceCost]:
    return {
        f"{prefijo}t{i}": TraceCost(
            trace_id=f"{prefijo}t{i}",
            cost_usd=por_traza,
            input_tokens=1000,
            output_tokens=100,
            duration_ms=800.0,
            spans=3,
        )
        for i in range(n)
    }


# ---------------------------------------------------------------------------------
# La guarda del porcentaje (D-087)
# ---------------------------------------------------------------------------------


def test_con_pocos_casos_no_se_ensena_un_porcentaje():
    """El mismo patrón del 468 $/mes y del 193.100 %, por cuarta vez.

    Con cuatro casos anotados un «75 %» se recuerda igual de bien que uno de verdad, y
    no informa de nada. Se enseñan los cuatro casos y se dice que no bastan.
    """
    rate = evals.rate_for("human", passed=3, failed=1, unjudged=0)
    assert rate.value is None
    assert rate.low is None and rate.high is None
    assert "4 casos" in rate.unavailable
    # Los números en bruto siguen ahí: no se puede calcular, pero sí contar.
    assert (rate.passed, rate.failed, rate.judged) == (3, 1, 4)


def test_con_casos_suficientes_el_porcentaje_va_con_su_margen():
    rate = evals.rate_for("human", passed=18, failed=2, unjudged=0)
    assert rate.value == pytest.approx(0.9)
    assert rate.unavailable == ""
    assert 0 < rate.low < rate.value < rate.high < 1


def test_el_margen_nunca_se_sale_del_rango():
    """Con el intervalo normal, diez de diez dan «entre el 100 % y el 100 %» y nueve de
    diez, «entre el 71 % y el 109 %». Wilson no se sale nunca."""
    for passed, judged in ((10, 10), (0, 10), (9, 10), (1, 40)):
        low, high = evals.wilson(passed, judged)
        assert 0.0 <= low <= high <= 1.0, (passed, judged)
    # Y no colapsa a un punto cuando aciertan todos: diez de diez no es certeza.
    low, high = evals.wilson(10, 10)
    assert low < 1.0


def test_sin_ningun_veredicto_se_dice_que_no_lo_hay():
    rate = evals.rate_for("llm_judge", passed=0, failed=0, unjudged=12)
    assert rate.value is None
    assert "ningún caso tiene veredicto" in rate.unavailable


# ---------------------------------------------------------------------------------
# Persona y máquina, nunca mezcladas (D-083)
# ---------------------------------------------------------------------------------


def test_el_acierto_se_calcula_por_separado_para_cada_fuente():
    """Si se promediaran, un juez optimista taparía lo que dicen las personas."""
    run = _run("A", 20)
    anotaciones = _anns("", 20, human=20, human_pass=10, judge=20, judge_pass=20)
    lado = evals.side_for(run, anotaciones, _costs("", 20, 0.001))

    humano = next(r for r in lado.rates if r.source == "human")
    maquina = next(r for r in lado.rates if r.source == "llm_judge")
    assert humano.value == pytest.approx(0.5)
    assert maquina.value == pytest.approx(1.0)
    assert len(lado.rates) == 2, "una cifra por fuente, nunca una fundida"


def test_el_coste_del_juez_va_aparte_del_coste_del_agente():
    """Mezclarlos haría que la versión que alguien miró con más cuidado pareciese más
    cara de ejecutar, que es justo al revés de lo que pasó."""
    run = _run("A", 20)
    anotaciones = _anns("", 20, judge=20, judge_pass=15)
    lado = evals.side_for(run, anotaciones, _costs("", 20, 0.002))

    assert lado.cost_usd == pytest.approx(0.04)
    assert lado.judge_cost_usd == pytest.approx(20 * 0.0004)
    assert lado.judge_cost_usd not in (0, lado.cost_usd)


def test_una_anotacion_humana_no_puede_llevar_coste_de_juez(tmp_path):
    """La separación es estructural: la fila humana escribe NULL en las columnas del
    juez, así que ni un bug ni una migración a medias pueden mezclarlas."""
    store = SQLiteMetadataStore(tmp_path / "m.db")
    store.migrate()
    # Se intenta colar un bloque de juez en una anotación humana.
    store.save_annotation(
        "p",
        Annotation(
            id="a1",
            trace_id="t1",
            source="human",
            verdict="pass",
            author="enol",
            created_at=AHORA,
            judge=JudgeRun(model="colado", cost_usd=99.0),
        ),
    )
    guardada = store.list_annotations("t1")[0]
    assert guardada.source == "human"
    assert guardada.judge is None, "el coste de juez no puede sobrevivir en una fila humana"


def test_volver_a_juzgar_sustituye_en_vez_de_acumular(tmp_path):
    """Sin esto, correr el juez tres veces contaría tres veces en el acierto."""
    store = SQLiteMetadataStore(tmp_path / "m.db")
    store.migrate()
    for veredicto in ("pass", "fail", "pass"):
        store.save_annotation(
            "p",
            Annotation(
                id=f"a-{veredicto}-{uuid.uuid4().hex[:4]}",
                trace_id="t1",
                source="llm_judge",
                verdict=veredicto,
                author="modelo-juez",
                created_at=AHORA,
                judge=JudgeRun(model="modelo-juez", cost_usd=0.001),
            ),
        )
    anotaciones = store.list_annotations("t1")
    assert len(anotaciones) == 1
    assert anotaciones[0].verdict == "pass"


def test_una_persona_y_el_juez_conviven_en_la_misma_traza(tmp_path):
    store = SQLiteMetadataStore(tmp_path / "m.db")
    store.migrate()
    store.save_annotation(
        "p",
        Annotation(id="a1", trace_id="t1", source="human", verdict="pass",
                   author="enol", created_at=AHORA),
    )
    store.save_annotation(
        "p",
        Annotation(id="a2", trace_id="t1", source="llm_judge", verdict="fail",
                   author="m", created_at=AHORA, judge=JudgeRun(model="m", cost_usd=0.002)),
    )
    fuentes = {a.source: a.verdict for a in store.list_annotations("t1")}
    assert fuentes == {"human": "pass", "llm_judge": "fail"}


# ---------------------------------------------------------------------------------
# Qué cuenta como fallo
# ---------------------------------------------------------------------------------


def test_un_caso_que_revienta_cuenta_como_fallo():
    """Si contase como «sin juzgar», una versión que revienta la mitad de las veces
    saldría con el mismo acierto que una que funciona."""
    run = _run("A", 20, crashed=10)
    anotaciones = _anns("", 20, human=20, human_pass=20)
    lado = evals.side_for(run, anotaciones, _costs("", 20, 0.001))

    humano = next(r for r in lado.rates if r.source == "human")
    assert lado.crashed == 10
    assert humano.failed == 10
    assert humano.value == pytest.approx(0.5)


def test_un_caso_sin_anotar_no_cuenta_como_fallo():
    """Contarlo convertiría «no lo he mirado» en «está mal», y el acierto bajaría al
    añadir casos al conjunto."""
    run = _run("A", 30)
    anotaciones = _anns("", 30, human=12, human_pass=12)
    lado = evals.side_for(run, anotaciones, _costs("", 30, 0.001))

    humano = next(r for r in lado.rates if r.source == "human")
    assert (humano.passed, humano.failed, humano.unjudged) == (12, 0, 18)
    assert humano.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------------
# A vs B: lo valioso de la pestaña
# ---------------------------------------------------------------------------------


def _comparacion(*, n=30, a_pass=24, b_pass=25, coste_a=0.004, coste_b=0.001):
    """Dos tiradas del mismo conjunto, anotadas por personas."""
    run_a, run_b = _run("A", n, prefijo="a"), _run("B", n, prefijo="b")
    anotaciones = {
        **_anns("a", n, human=n, human_pass=a_pass),
        **_anns("b", n, human=n, human_pass=b_pass),
    }
    costes = {**_costs("a", n, coste_a), **_costs("b", n, coste_b)}
    return evals.compare(run_a, run_b, anotaciones, costes, project_id="p", dataset_name="ds")


def test_una_diferencia_dentro_del_margen_es_un_empate_y_se_dice():
    """El error que hace desplegar una regresión creyendo que es una mejora.

    24 de 30 frente a 25 de 30 son cuatro puntos de diferencia, y con treinta casos eso
    cabe de sobra dentro del azar. La respuesta honesta es «no se distinguen».
    """
    comp = _comparacion(a_pass=24, b_pass=25)
    humano = next(c for c in comp.by_source if c.source == "human")
    assert humano.verdict == "empate"
    assert "no se distinguen" in humano.headline
    assert "cabe dentro del azar" in humano.detail


def test_una_diferencia_grande_si_se_declara():
    comp = _comparacion(n=60, a_pass=20, b_pass=58)
    humano = next(c for c in comp.by_source if c.source == "human")
    assert humano.verdict == "mejor"
    assert "no se solapan" in humano.detail


def test_el_titular_junta_acierto_y_coste():
    """Es la frase por la que existe la pestaña: se lee antes de desplegar."""
    comp = _comparacion(a_pass=24, b_pass=25, coste_a=0.004, coste_b=0.001)
    assert "acierta igual" in comp.headline
    assert "menos por caso" in comp.headline
    # Y el detalle explica por qué el coste no lleva margen y el acierto sí.
    assert "no es una muestra" in comp.detail


def test_empeorar_manda_sobre_ser_mas_barato():
    """Cuando se está decidiendo si desplegar, el lado por el que hay que equivocarse
    es el de avisar de que acierta menos, aunque ahorre."""
    comp = _comparacion(n=60, a_pass=58, b_pass=20, coste_a=0.004, coste_b=0.0001)
    assert "acierta menos" in comp.headline
    assert comp.headline.index("acierta menos") < comp.headline.index("cuesta")


def test_sin_casos_suficientes_no_se_compara_el_acierto_pero_si_el_coste():
    """Con cuatro casos no se sabe si B acierta igual, pero se sabe lo que costó."""
    comp = _comparacion(n=4, a_pass=3, b_pass=4, coste_a=0.01, coste_b=0.002)
    humano = next(c for c in comp.by_source if c.source == "human")
    assert humano.verdict == "sin-base"
    assert humano.a.value is None
    assert "3 de 4" in humano.detail
    assert "todavía no se puede decir" in comp.headline.lower()
    assert comp.a.cost_per_case_usd == pytest.approx(0.01)
    assert comp.b.cost_per_case_usd == pytest.approx(0.002)


def test_cuando_las_personas_y_el_juez_no_coinciden_se_dice():
    """No es un fallo: es el dato más interesante que puede dar esta pantalla."""
    n = 60
    run_a, run_b = _run("A", n, prefijo="a"), _run("B", n, prefijo="b")
    anotaciones = {}
    # Las personas dicen que B empeora; el juez, que mejora.
    anotaciones.update(_anns("a", n, human=n, human_pass=58, judge=n, judge_pass=20))
    anotaciones.update(_anns("b", n, human=n, human_pass=20, judge=n, judge_pass=58))
    costes = {**_costs("a", n, 0.001), **_costs("b", n, 0.001)}
    comp = evals.compare(run_a, run_b, anotaciones, costes, project_id="p")

    assert comp.sources_disagree is True
    veredictos = {c.source: c.verdict for c in comp.by_source}
    assert veredictos == {"human": "peor", "llm_judge": "mejor"}
    # Y manda el que avisa de que empeora.
    assert "acierta menos" in comp.headline


def test_un_empate_no_contradice_a_nadie():
    """«No lo sé» no es «lo contrario»: un empate no puede marcar contradicción."""
    n = 60
    run_a, run_b = _run("A", n, prefijo="a"), _run("B", n, prefijo="b")
    anotaciones = {}
    anotaciones.update(_anns("a", n, human=n, human_pass=30, judge=n, judge_pass=20))
    anotaciones.update(_anns("b", n, human=n, human_pass=31, judge=n, judge_pass=58))
    costes = {**_costs("a", n, 0.001), **_costs("b", n, 0.001)}
    comp = evals.compare(run_a, run_b, anotaciones, costes, project_id="p")

    veredictos = {c.source: c.verdict for c in comp.by_source}
    assert veredictos["human"] == "empate"
    assert veredictos["llm_judge"] == "mejor"
    assert comp.sources_disagree is False


def test_el_coste_es_un_suelo_cuando_hay_pasos_sin_tarifa():
    run = _run("A", 20)
    costes = _costs("", 20, 0.001)
    costes["t3"].assumed_rate_spans = 2
    lado = evals.side_for(run, {}, costes)
    assert lado.cost_is_floor is True


# ---------------------------------------------------------------------------------
# El modo local puede evaluar (D-084)
# ---------------------------------------------------------------------------------


def _traza(project: str, trace_id: str, coste: float) -> list[Span]:
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        project_id=project,
        name="responder",
        type="agent",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(seconds=1),
        duration_ms=1000.0,
        input={"pregunta": "¿cuánto equipaje?"},
        output={"respuesta": "una maleta"},
    )
    llm = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=raiz.span_id,
        project_id=project,
        name="chat",
        type="llm",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=500),
        duration_ms=500.0,
    )
    llm.llm = LLMAttributes(
        system="openai",
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=800, output_tokens=80),
        cost=Cost(input_usd=coste * 0.8, output_usd=coste * 0.2, total_usd=coste),
    )
    return [raiz, llm]


@pytest.fixture
def local(tmp_path, monkeypatch):
    """La aplicación tal cual la arranca `laplace ui`, con trazas dentro."""
    import importlib

    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans: list[Span] = []
    for i in range(12):
        spans += _traza("local", f"tr{i:02d}", 0.001)
    store.insert_spans(spans)

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_en_local_se_puede_anotar(local):
    """Antes en local no había dónde guardar una anotación, así que la pestaña entera
    habría sido una versión recortada del producto (D-084)."""
    respuesta = local.post(
        "/api/annotations",
        json={
            "project_id": "local",
            "trace_id": "tr00",
            "verdict": "fail",
            "comment": "se inventó la tarifa",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["source"] == "human"
    assert cuerpo["judge"] is None

    # Y la anotación vuelve pegada a la traza, que es donde se lee.
    traza = local.get("/api/traces/tr00", params={"project_id": "local"}).json()
    assert [a["verdict"] for a in traza["annotations"]] == ["fail"]


def test_la_ruta_de_anotar_no_puede_crear_un_veredicto_de_maquina(local):
    """Ni un cliente equivocado ni un `curl` a mano pueden colar un veredicto de modelo
    disfrazado de persona: el campo no existe en la entrada."""
    respuesta = local.post(
        "/api/annotations",
        json={
            "project_id": "local",
            "trace_id": "tr01",
            "verdict": "pass",
            "source": "llm_judge",
            "judge": {"model": "colado", "cost_usd": 99},
        },
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["source"] == "human"
    assert respuesta.json()["judge"] is None


def test_un_conjunto_se_crea_desde_un_filtro_del_explorador(local):
    respuesta = local.post(
        "/api/datasets",
        json={
            "project_id": "local",
            "name": "regresiones",
            "filter": {"sort": "recent"},
            "limit": 10,
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    conjunto = respuesta.json()
    assert conjunto["item_count"] == 10
    assert conjunto["source_filter"] == {"sort": "recent"}

    detalle = local.get(f"/api/datasets/{conjunto['id']}").json()
    casos = detalle["items"]
    assert len(casos) == 10
    # Tráfico real: cada caso apunta a la traza de la que salió, y trae su entrada.
    assert all(c["trace_id"] for c in casos)
    assert casos[0]["input"] == {"pregunta": "¿cuánto equipaje?"}
    assert casos[0]["expected"] == {"respuesta": "una maleta"}


def test_un_filtro_que_no_selecciona_nada_no_crea_un_conjunto_vacio(local):
    respuesta = local.post(
        "/api/datasets",
        json={"project_id": "local", "name": "vacio", "filter": {"search": "zzzz"}},
    )
    assert respuesta.status_code == 400
    assert "vacío" in respuesta.json()["detail"]


def test_el_ciclo_entero_en_local(local):
    """Conjunto → dos tiradas → comparación, por la API y contra SQLite."""
    conjunto = local.post(
        "/api/datasets",
        json={"project_id": "local", "name": "ciclo", "filter": {}, "limit": 12},
    ).json()
    casos = local.get(f"/api/datasets/{conjunto['id']}").json()["items"]

    tiradas = {}
    for variante in ("A", "B"):
        tiradas[variante] = local.post(
            "/api/runs",
            json={
                "project_id": "local",
                "dataset_id": conjunto["id"],
                "variant": variante,
                "items": [
                    {"case_id": c["id"], "trace_id": c["trace_id"]} for c in casos
                ],
            },
        ).json()

    comparacion = local.get(
        "/api/experiments/compare",
        params={"project_id": "local", "a": tiradas["A"]["id"], "b": tiradas["B"]["id"]},
    )
    assert comparacion.status_code == 200, comparacion.text
    cuerpo = comparacion.json()
    assert cuerpo["a"]["variant"] == "A" and cuerpo["b"]["variant"] == "B"
    assert cuerpo["a"]["cost_usd"] > 0, "el coste sale de las trazas de verdad"
    # Sin anotar nada, ninguna fuente tiene base: se dice, no se inventa un 100 %.
    assert all(c["verdict"] == "sin-base" for c in cuerpo["by_source"])


def test_no_se_comparan_tiradas_de_conjuntos_distintos(local):
    """Comparar el acierto sobre casos diferentes no dice nada, y es un error fácil de
    cometer con dos desplegables."""
    ids = []
    for nombre in ("uno", "otro"):
        conjunto = local.post(
            "/api/datasets",
            json={"project_id": "local", "name": nombre, "filter": {}, "limit": 5},
        ).json()
        ids.append(
            local.post(
                "/api/runs",
                json={
                    "project_id": "local",
                    "dataset_id": conjunto["id"],
                    "variant": nombre,
                    "items": [],
                },
            ).json()["id"]
        )
    respuesta = local.get(
        "/api/experiments/compare", params={"project_id": "local", "a": ids[0], "b": ids[1]}
    )
    assert respuesta.status_code == 400
    assert "conjuntos distintos" in respuesta.json()["detail"]


def test_el_juez_esta_apagado_por_defecto_y_lo_dice(local):
    estado = local.get("/api/judge").json()
    assert estado["enabled"] is False
    assert "LAPLACE_EVALS_JUDGE_ENABLED" in estado["detail"]
    # Y pedirle que juzgue devuelve el mismo motivo, no un 500.
    respuesta = local.post("/api/judge", json={"project_id": "local", "trace_ids": ["tr00"]})
    assert respuesta.status_code == 503


def test_sin_base_de_metadatos_se_dice_en_vez_de_fingir_que_se_guarda():
    """Un 200 sobre un almacén nulo haría que alguien anotase veinte trazas y las
    perdiese todas sin enterarse."""
    nulo = NullMetadataStore()
    with pytest.raises(MetadataUnavailable):
        nulo.save_annotation(
            "p", Annotation(id="a", trace_id="t", source="human", created_at=AHORA)
        )
    # Leer sigue funcionando: lo que se pierde es escribir.
    assert nulo.list_annotations("t") == []


# ---------------------------------------------------------------------------------
# El coste de unas trazas concretas, por los dos caminos
# ---------------------------------------------------------------------------------


def test_el_coste_de_unas_trazas_sueltas(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    spans: list[Span] = []
    for i in range(5):
        spans += _traza("p", f"x{i}", 0.002)
    store.insert_spans(spans)

    costes = store.costs_for_traces("p", ["x0", "x2", "no-existe"])
    assert set(costes) == {"x0", "x2"}
    assert costes["x0"].cost_usd == pytest.approx(0.002)
    assert costes["x0"].spans == 2
    assert costes["x0"].input_tokens == 800


def test_los_dos_almacenes_cuestan_lo_mismo(tmp_path):
    """Paridad, con la consulta nueva incluida: no puede haber una comparación A vs B
    que diga una cosa en local y otra en la nube."""
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()

    project = f"eval-paridad-{uuid.uuid4().hex[:8]}"
    local_store = SQLiteStore(tmp_path / "l.db")
    local_store.migrate()
    spans: list[Span] = []
    ids = []
    for i in range(6):
        trace = f"{project}-{i}"
        ids.append(trace)
        spans += _traza(project, trace, 0.003)
    local_store.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        aqui = local_store.costs_for_traces(project, ids)
        alli = nube.costs_for_traces(project, ids)
        assert set(aqui) == set(alli)
        for trace_id in ids:
            a, b = aqui[trace_id], alli[trace_id]
            assert a.cost_usd == pytest.approx(b.cost_usd, rel=1e-9)
            assert a.input_tokens == b.input_tokens
            assert a.spans == b.spans
            assert a.duration_ms == pytest.approx(b.duration_ms)
    finally:
        nube.delete_project(project)


def test_los_dos_almacenes_de_metadatos_dicen_lo_mismo(tmp_path):
    """La misma promesa que con las trazas, ahora con lo mutable: una anotación no puede
    leerse distinto según dónde esté guardada."""
    from laplace_backend.storage.postgres import PostgresMetadataStore

    nube = PostgresMetadataStore(Settings())
    if not nube.health():
        pytest.skip("no hay Postgres escuchando; no se puede comparar")
    nube.migrate()

    local_store = SQLiteMetadataStore(tmp_path / "m.db")
    local_store.migrate()

    project = f"meta-paridad-{uuid.uuid4().hex[:8]}"
    trace = uuid.uuid4().hex
    humana = Annotation(
        id=f"an_{uuid.uuid4().hex[:8]}", trace_id=trace, source="human",
        verdict="pass", comment="bien", author="enol", created_at=AHORA,
    )
    maquina = Annotation(
        id=f"an_{uuid.uuid4().hex[:8]}", trace_id=trace, source="llm_judge",
        verdict="fail", comment="se lo inventó", author="modelo-juez", created_at=AHORA,
        judge=JudgeRun(model="modelo-juez", input_tokens=400, output_tokens=30,
                       cost_usd=0.0007, prompt_version="v1"),
    )
    conjunto = Dataset(
        id=f"ds_{uuid.uuid4().hex[:8]}", project_id=project, name="paridad",
        created_at=AHORA, source_filter={"sort": "recent"},
    )
    # Identificadores únicos por ejecución: Postgres es persistente entre tiradas de
    # los tests y unos ids fijos chocarían con los de la vez anterior.
    casos = [
        DatasetItem(id=f"case_{uuid.uuid4().hex[:10]}", dataset_id=conjunto.id,
                    trace_id=trace, input={"q": i}, expected={"a": i}, created_at=AHORA)
        for i in range(3)
    ]
    tirada = EvalRun(
        id=f"run_{uuid.uuid4().hex[:8]}", project_id=project, dataset_id=conjunto.id,
        variant="A", created_at=AHORA,
        items=[EvalRunItem(case_id=c.id, trace_id=trace) for c in casos],
    )

    def sembrar(store):
        for anotacion in (humana, maquina):
            store.save_annotation(project, anotacion)
        store.create_dataset(conjunto, casos)
        store.create_run(tirada)

    sembrar(local_store)
    sembrar(nube)

    def resumir(store):
        anotaciones = sorted(
            (a.source, a.verdict, a.comment, a.judge.model if a.judge else None,
             round(a.judge.cost_usd, 9) if a.judge else None)
            for a in store.list_annotations(trace)
        )
        ds = store.get_dataset(conjunto.id)
        corrida = store.get_run(tirada.id)
        return (
            anotaciones,
            (ds.name, ds.item_count, ds.source_filter),
            (corrida.variant, sorted(i.case_id for i in corrida.items)),
        )

    try:
        assert resumir(local_store) == resumir(nube)
    finally:
        # Postgres es persistente: lo que siembra un test se lo lleva el mismo test.
        nube.delete_dataset(conjunto.id)
        for anotacion in (humana, maquina):
            nube.delete_annotation(anotacion.id)
