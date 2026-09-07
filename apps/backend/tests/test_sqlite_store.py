"""El almacén local dice lo mismo que el de la nube.

Es la prueba que sostiene la promesa del modo local: `laplace ui` no es una versión
recortada de Laplace, es Laplace con las filas en otro sitio (D-015). Lo que se
comprueba aquí no es que SQLite funcione —eso es fácil— sino que los **mismos spans**
producen los **mismos hallazgos y las mismas cifras** por los dos caminos. En cuanto
uno de los dos derive, el panel de ahorro de alguien empezará a mentir según dónde
tenga guardadas sus trazas.

Las pruebas que necesitan ClickHouse se saltan solas si no hay ninguno escuchando; las
de SQLite corren siempre, porque SQLite viene con Python.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage, ToolAttributes

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.storage.base import SpanStore, TraceFilter, Window
from laplace_backend.storage.clickhouse import ClickHouseStore
from laplace_backend.storage.sqlite import SQLiteStore

BASE = datetime.now(timezone.utc) - timedelta(hours=2)


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    almacen = SQLiteStore(tmp_path / "laplace.db")
    almacen.migrate()
    return almacen


@pytest.fixture
def window() -> Window:
    ahora = datetime.now(timezone.utc)
    return Window(since=ahora - timedelta(days=7), until=ahora + timedelta(minutes=5), days=7)


def _span(project, trace, span_id, name, tipo, offset_s, **kwargs) -> Span:
    inicio = BASE + timedelta(seconds=offset_s)
    return Span(
        span_id=span_id,
        trace_id=trace,
        parent_span_id=kwargs.pop("parent", None),
        project_id=project,
        name=name,
        type=tipo,
        status=kwargs.pop("status", "ok"),
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=kwargs.pop("ms", 200)),
        duration_ms=kwargs.pop("duration_ms", 200.0),
        dedup_hash=kwargs.pop("dedup_hash", ""),
        **kwargs,
    )


def _agente(project: str) -> list[Span]:
    """Un agente con las tres patologías, sin ningún paso nombrado a mano."""
    spans: list[Span] = []
    for t in range(6):
        trace = uuid.uuid4().hex
        raiz = _span(project, trace, uuid.uuid4().hex[:16], "responder", "agent", t * 10)
        spans.append(raiz)

        corto = _span(
            project, trace, uuid.uuid4().hex[:16], "chat gpt-5.6-terra", "llm", t * 10 + 1,
            parent=raiz.span_id, dedup_hash=f"clas-{t}",
            step_key="paso-clasificar", step_label="responder", step_hint="Clasifica",
        )
        corto.llm = LLMAttributes(
            system="openai", request_model="gpt-5.6-terra", response_model="gpt-5.6-terra",
            usage=TokenUsage(input_tokens=45, output_tokens=4),
            cost=Cost(input_usd=0.00009, output_usd=0.000048, total_usd=0.000138),
        )
        spans.append(corto)

        for i in range(3):
            reintento = _span(
                project, trace, uuid.uuid4().hex[:16], "chat gpt-5.6-terra", "llm",
                t * 10 + 2 + i, parent=raiz.span_id, dedup_hash=f"json-{t}",
                step_key="paso-json", step_label="responder", step_hint="Devuelve JSON",
            )
            reintento.llm = LLMAttributes(
                system="openai", request_model="gpt-5.6-terra", response_model="gpt-5.6-terra",
                usage=TokenUsage(input_tokens=60, output_tokens=6),
                cost=Cost(input_usd=0.00012, output_usd=0.000072, total_usd=0.000192),
            )
            spans.append(reintento)

        for i in range(2):
            manual = _span(
                project, trace, uuid.uuid4().hex[:16], "chat gpt-5.6-terra", "llm",
                t * 10 + 6 + i, parent=raiz.span_id, dedup_hash=f"man-{t}-{i}",
                step_key="paso-manual", step_label="responder", step_hint="Usa el manual",
            )
            manual.llm = LLMAttributes(
                system="openai", request_model="gpt-5.6-terra", response_model="gpt-5.6-terra",
                usage=TokenUsage(input_tokens=20_000, output_tokens=18),
                cost=Cost(input_usd=0.04, output_usd=0.000216, total_usd=0.040216),
            )
            spans.append(manual)

        herramienta = _span(
            project, trace, uuid.uuid4().hex[:16], "buscar", "tool", t * 10 + 8,
            parent=raiz.span_id, dedup_hash=f"tool-{t}",
        )
        herramienta.tool = ToolAttributes(name="buscar", arguments={"q": "tarifa"})
        spans.append(herramienta)
    return spans


# ---------------------------------------------------------------------------------
# El almacén, por sí solo
# ---------------------------------------------------------------------------------


def test_cumple_el_protocolo(store):
    assert isinstance(store, SpanStore)


def test_reenviar_un_span_no_duplica_su_coste(store, window):
    """El exportador OTLP reintenta. Un span reenviado sustituye, no se suma."""
    project = "reenvio"
    spans = _agente(project)
    store.insert_spans(spans)
    primero = store.summarize_window(project, window)

    store.insert_spans(spans)  # el mismo lote, otra vez
    segundo = store.summarize_window(project, window)

    assert segundo.spans == primero.spans
    assert segundo.total_cost_usd == pytest.approx(primero.total_cost_usd)


def test_la_traza_se_lee_entera_y_en_orden(store):
    project = "lectura"
    spans = _agente(project)
    store.insert_spans(spans)
    trace_id = spans[0].trace_id

    leidos = store.get_trace_spans(trace_id)
    assert len(leidos) == len([s for s in spans if s.trace_id == trace_id])
    assert [s.start_time for s in leidos] == sorted(s.start_time for s in leidos)
    # Y el contenido sobrevive al viaje de ida y vuelta.
    llm = next(s for s in leidos if s.type == "llm")
    assert llm.llm.request_model == "gpt-5.6-terra"
    assert llm.llm.usage.input_tokens > 0
    herramienta = next(s for s in leidos if s.type == "tool")
    assert herramienta.tool.arguments == {"q": "tarifa"}


def test_la_lista_de_trazas_filtra_y_pagina(store):
    project = "listado"
    store.insert_spans(_agente(project))

    pagina = store.list_traces(TraceFilter(project_id=project, limit=4, sort="recent"))
    assert len(pagina.traces) == 4
    assert pagina.next_cursor, "una página llena tiene que ofrecer la siguiente"

    resto = store.list_traces(
        TraceFilter(project_id=project, limit=4, sort="recent",
                    before=pagina.traces[-1].start_time,
                    before_trace_id=pagina.traces[-1].trace_id)
    )
    vistos = {t.trace_id for t in pagina.traces} | {t.trace_id for t in resto.traces}
    assert len(vistos) == 6, "ninguna traza se pierde entre páginas"

    caras = store.list_traces(TraceFilter(project_id=project, limit=10, sort="cost"))
    costes = [t.cost.total_usd for t in caras.traces]
    assert costes == sorted(costes, reverse=True)

    con_modelo = store.list_traces(TraceFilter(project_id=project, model="gpt-5.6-terra"))
    assert len(con_modelo.traces) == 6
    sin_modelo = store.list_traces(TraceFilter(project_id=project, model="no-existe"))
    assert sin_modelo.traces == []


def test_la_busqueda_encuentra_por_nombre_de_paso(store):
    project = "busqueda"
    store.insert_spans(_agente(project))
    assert len(store.list_traces(TraceFilter(project_id=project, search="buscar")).traces) == 6
    assert store.list_traces(TraceFilter(project_id=project, search="zzz")).traces == []


def test_las_tres_reglas_funcionan_en_local(store, window):
    """Un estudiante tiene que ver su panel de ahorro sin nube.

    Es literalmente el requisito del encargo, y se comprueba con datos donde ningún
    paso lleva nombre propio, que es como está el código de cualquiera.
    """
    project = "local"
    store.insert_spans(_agente(project))
    resumen = insights.overview(store, project, window)

    tipos = {f.kind for f in resumen.findings}
    assert {"repeticion", "modelo_caro", "contexto_fijo"} <= tipos

    # Y la promesa no se pasa de lo que cuesta el agente, aquí tampoco.
    prometido = sum(f.window_waste_usd for f in resumen.findings)
    assert prometido <= resumen.window_cost_usd


def test_la_ficha_ensena_la_consulta_de_sqlite_no_la_de_clickhouse(store, window):
    """«Cómo lo hemos detectado» tiene que enseñar la consulta que se ha ejecutado.

    Enseñar la de ClickHouse en modo local sería una captura de pantalla de otro
    producto: se parece, pero no es la que ha dado ese número (D-067).
    """
    project = "consulta"
    store.insert_spans(_agente(project))
    hallazgo = next(
        f for f in insights.detect(store, project, window) if f.kind == "contexto_fijo"
    )
    detalle = insights.detail(store, project, window, hallazgo.id)
    assert "GROUP BY paso_clave" in detalle.detection_query
    assert "uniqExact" not in detalle.detection_query


# ---------------------------------------------------------------------------------
# Los dos almacenes, uno contra otro
# ---------------------------------------------------------------------------------


@pytest.fixture
def clickhouse():
    almacen = ClickHouseStore(Settings())
    if not almacen.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    almacen.migrate()
    return almacen


def test_los_dos_almacenes_dan_el_mismo_diagnostico(store, clickhouse, window):
    """La comprobación que sostiene «el mismo producto, otro sitio para las filas».

    Los mismos spans por los dos caminos tienen que dar los mismos hallazgos, con el
    mismo dinero. Si esto se rompe, el modo local ha dejado de ser Laplace.
    """
    project = f"paridad-{uuid.uuid4().hex[:8]}"
    spans = _agente(project)
    store.insert_spans(spans)
    clickhouse.insert_spans(spans)
    try:
        local = insights.overview(store, project, window)
        nube = insights.overview(clickhouse, project, window)

        assert local.spans == nube.spans
        assert local.traces == nube.traces
        assert local.window_cost_usd == pytest.approx(nube.window_cost_usd, rel=1e-9)
        assert local.input_tokens == nube.input_tokens

        def resumir(vista):
            return sorted(
                (f.kind, f.title, round(f.window_waste_usd, 9)) for f in vista.findings
            )

        # Sin esto la comparación pasaría con dos listas vacías, que es justo el
        # resultado que tendría un modo local roto.
        assert len(local.findings) >= 3
        assert resumir(local) == resumir(nube)
    finally:
        clickhouse.delete_project(project)


def test_los_dos_almacenes_leen_la_misma_traza(store, clickhouse):
    project = f"paridad-traza-{uuid.uuid4().hex[:8]}"
    spans = _agente(project)
    store.insert_spans(spans)
    clickhouse.insert_spans(spans)
    try:
        trace_id = spans[0].trace_id
        aqui = store.get_trace_spans(trace_id, project)
        alli = clickhouse.get_trace_spans(trace_id, project)
        assert [s.span_id for s in aqui] == [s.span_id for s in alli]
        assert [s.model_dump(mode="json") for s in aqui] == [
            s.model_dump(mode="json") for s in alli
        ]
    finally:
        clickhouse.delete_project(project)
