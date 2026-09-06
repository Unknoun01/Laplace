"""Pruebas del almacén contra un ClickHouse de verdad.

Las de `test_ingest.py` no tocan la base de datos y por eso no ven una clase entera de
fallos: el SQL. Un alias mal puesto o un filtro que no se aplica sólo se descubre
ejecutando la consulta. Éstas se saltan solas si no hay ClickHouse escuchando:

    docker compose up -d clickhouse
    pytest apps/backend/tests
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage, ToolAttributes

from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceFilter, decode_cursor
from laplace_backend.storage.clickhouse import ClickHouseStore

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _store() -> ClickHouseStore:
    settings = Settings()
    store = ClickHouseStore(settings)
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; se omiten las pruebas del almacén")
    store.migrate()
    return store


@pytest.fixture(scope="module")
def store() -> ClickHouseStore:
    return _store()


@pytest.fixture(scope="module")
def dataset(store: ClickHouseStore) -> dict[str, str]:
    """Dos trazas en un proyecto propio de esta ejecución: una correcta y una fallida."""
    project = f"test-{uuid.uuid4().hex[:8]}"
    ok_trace, error_trace = uuid.uuid4().hex, uuid.uuid4().hex

    spans = [
        *_trace(project, ok_trace, session="conv-1", failed=False),
        *_trace(project, error_trace, session="conv-2", failed=True),
    ]
    store.insert_spans(spans)
    return {"project": project, "ok": ok_trace, "error": error_trace}


def _span(project: str, trace: str, span_id: str, parent: str | None, name: str,
          type_: str, offset_ms: int, duration_ms: int, session: str,
          status: str = "ok") -> Span:
    start = BASE + timedelta(milliseconds=offset_ms)
    return Span(
        span_id=span_id,
        trace_id=trace,
        parent_span_id=parent,
        project_id=project,
        name=name,
        type=type_,
        status=status,
        status_message="el proveedor rechazó la reserva" if status == "error" else "",
        start_time=start,
        end_time=start + timedelta(milliseconds=duration_ms),
        duration_ms=float(duration_ms),
        session_id=session,
        user_id="u-7",
        dedup_hash=f"hash-{name}",
    )


def _trace(project: str, trace: str, session: str, failed: bool) -> list[Span]:
    root = _span(project, trace, "1" * 16, None, "agente", "agent", 0, 500, session,
                 status="error" if failed else "ok")

    llm = _span(project, trace, "2" * 16, root.span_id, "chat gpt-4o-mini", "llm",
                10, 200, session)
    llm.llm = LLMAttributes(
        system="openai",
        request_model="gpt-4o-mini",
        response_model="gpt-4o-mini",
        usage=TokenUsage(input_tokens=1000, output_tokens=500),
        cost=Cost(input_usd=0.00015, output_usd=0.0003, total_usd=0.00045),
        input_messages=[{"role": "user", "content": "hola"}],
        output_messages=[{"role": "assistant", "content": "qué tal"}],
        params={"temperature": 0.5},
        finish_reasons=["stop"],
    )

    tool = _span(project, trace, "3" * 16, root.span_id, "buscar_vuelos", "tool",
                 220, 100, session, status="error" if failed else "ok")
    tool.tool = ToolAttributes(name="buscar_vuelos", arguments={"origen": "MAD"},
                              output=["IB3421"])

    return [root, llm, tool]


# ---------------------------------------------------------------------------------


def test_la_lista_resume_la_traza_a_partir_de_sus_spans(store, dataset):
    page = store.list_traces(TraceFilter(project_id=dataset["project"]))

    assert len(page.traces) == 2
    resumen = next(t for t in page.traces if t.trace_id == dataset["ok"])
    assert resumen.root_name == "agente"
    assert resumen.status == "ok"
    assert resumen.span_count == 3
    assert resumen.llm_call_count == 1
    assert resumen.tool_call_count == 1
    assert resumen.usage.input_tokens == 1000
    assert resumen.usage.output_tokens == 500
    assert resumen.cost.total_usd == pytest.approx(0.00045)
    assert resumen.session_id == "conv-1"
    assert resumen.duration_ms == pytest.approx(500, abs=1)


def test_filtrar_por_proyecto_no_choca_con_los_alias_de_las_agregaciones(store, dataset):
    # Regresión: `any(project_id) AS project_id` hacía que ClickHouse resolviera el
    # WHERE contra el alias y devolviera ILLEGAL_AGGREGATION.
    page = store.list_traces(TraceFilter(project_id=dataset["project"]))
    assert {t.project_id for t in page.traces} == {dataset["project"]}

    assert store.list_traces(TraceFilter(project_id="proyecto-que-no-existe")).traces == []


def test_los_filtros_por_sesion_y_usuario_tampoco_chocan(store, dataset):
    page = store.list_traces(TraceFilter(project_id=dataset["project"], session_id="conv-1"))
    assert [t.trace_id for t in page.traces] == [dataset["ok"]]

    page = store.list_traces(TraceFilter(project_id=dataset["project"], user_id="u-7"))
    assert len(page.traces) == 2


def test_el_estado_es_una_propiedad_de_la_traza_entera(store, dataset):
    solo_error = store.list_traces(TraceFilter(project_id=dataset["project"], status="error"))
    assert [t.trace_id for t in solo_error.traces] == [dataset["error"]]

    solo_ok = store.list_traces(TraceFilter(project_id=dataset["project"], status="ok"))
    assert [t.trace_id for t in solo_ok.traces] == [dataset["ok"]]


def test_la_busqueda_encuentra_por_nombre_de_paso_y_por_id(store, dataset):
    por_nombre = store.list_traces(
        TraceFilter(project_id=dataset["project"], search="buscar_vuelos")
    )
    assert len(por_nombre.traces) == 2

    por_id = store.list_traces(
        TraceFilter(project_id=dataset["project"], search=dataset["ok"][:8])
    )
    assert [t.trace_id for t in por_id.traces] == [dataset["ok"]]

    assert store.list_traces(
        TraceFilter(project_id=dataset["project"], search="no-existe-este-paso")
    ).traces == []


def test_el_cursor_no_pierde_trazas_que_empiezan_en_el_mismo_instante(store, dataset):
    """Las dos trazas del dataset arrancan a la vez, que es el caso peligroso.

    Con un cursor sólo por fecha (`started < before`), la segunda desaparecía entre
    páginas. Dos agentes lanzados en paralelo hacen esto constantemente.
    """
    vistas: list[str] = []
    cursor: str | None = None

    for _ in range(3):  # 2 páginas de datos + 1 vacía
        before, before_id = decode_cursor(cursor)
        page = store.list_traces(
            TraceFilter(
                project_id=dataset["project"],
                limit=1,
                before=before,
                before_trace_id=before_id,
            )
        )
        vistas.extend(t.trace_id for t in page.traces)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sorted(vistas) == sorted([dataset["ok"], dataset["error"]])
    assert len(vistas) == len(set(vistas)), "ninguna traza puede salir dos veces"


def test_los_spans_vuelven_del_almacen_intactos(store, dataset):
    spans = store.get_trace_spans(dataset["ok"])
    assert len(spans) == 3

    llm = next(s for s in spans if s.type == "llm")
    assert llm.llm is not None
    assert llm.llm.request_model == "gpt-4o-mini"
    assert llm.llm.usage.input_tokens == 1000
    assert llm.llm.usage.output_tokens == 500
    assert llm.llm.cost.total_usd == pytest.approx(0.00045)
    # Los payloads se guardan en crudo y vuelven tal cual.
    assert llm.llm.input_messages == [{"role": "user", "content": "hola"}]
    assert llm.llm.params == {"temperature": 0.5}
    assert llm.llm.finish_reasons == ["stop"]

    tool = next(s for s in spans if s.type == "tool")
    assert tool.tool is not None
    assert tool.tool.arguments == {"origen": "MAD"}
    assert tool.tool.output == ["IB3421"]

    root = next(s for s in spans if s.parent_span_id is None)
    assert root.session_id == "conv-1"
    assert root.dedup_hash == "hash-agente"


def test_reenviar_el_mismo_span_no_duplica_el_coste(store, dataset):
    """El exportador OTLP reintenta: un span reescrito debe colapsar, no sumar."""
    antes = next(
        t for t in store.list_traces(TraceFilter(project_id=dataset["project"])).traces
        if t.trace_id == dataset["ok"]
    )

    store.insert_spans(_trace(dataset["project"], dataset["ok"], "conv-1", failed=False))

    despues = next(
        t for t in store.list_traces(TraceFilter(project_id=dataset["project"])).traces
        if t.trace_id == dataset["ok"]
    )
    assert despues.span_count == antes.span_count
    assert despues.cost.total_usd == pytest.approx(antes.cost.total_usd)


def test_el_proyecto_aparece_en_el_listado_con_su_volumen(store, dataset):
    stats = {p.project_id: p for p in store.list_projects()}
    assert dataset["project"] in stats
    assert stats[dataset["project"]].trace_count == 2
    assert stats[dataset["project"]].span_count == 6
