"""API HTTP: ingesta OTLP y lectura de trazas."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from laplace.schema import Trace, TraceListPage
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from starlette.concurrency import run_in_threadpool

from .ingest.otlp import decode_request, parse_spans
from .insights import FindingDetail, Overview, overview
from .insights import detail as finding_detail
from .pricing import get_price_table, reload_price_table
from .storage.base import TraceFilter, Window, decode_cursor
from .tree import build_tree, summarize

logger = logging.getLogger("laplace.api")

router = APIRouter()

_PROTOBUF = "application/x-protobuf"


def _store(request: Request) -> Any:
    return request.app.state.store


def _metadata(request: Request) -> Any:
    return request.app.state.metadata


# ---------------------------------------------------------------------------------
# Ingesta — ruta OTLP estándar
# ---------------------------------------------------------------------------------


@router.post("/v1/traces", include_in_schema=True)
async def ingest_traces(request: Request) -> Response:
    """Endpoint OTLP/HTTP.

    Es la ruta estándar, así que cualquier proceso instrumentado con OpenTelemetry
    puede exportar aquí apuntando `OTEL_EXPORTER_OTLP_ENDPOINT` a este backend,
    con o sin el SDK de Laplace.
    """
    body = await request.body()
    try:
        decoded = decode_request(
            body,
            content_type=request.headers.get("content-type", ""),
            content_encoding=request.headers.get("content-encoding", ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("petición OTLP ilegible: %s", exc)
        raise HTTPException(status_code=400, detail="petición OTLP ilegible") from exc

    spans = parse_spans(decoded)
    if spans:
        store = _store(request)
        try:
            await run_in_threadpool(store.insert_spans, spans)
        except Exception as exc:  # noqa: BLE001
            # 503 y no 500: el exportador reintenta, y el span es idempotente al escribir.
            logger.exception("fallo al escribir spans")
            raise HTTPException(status_code=503, detail="almacenamiento no disponible") from exc

        projects = {span.project_id for span in spans}
        for project_id in projects:
            try:
                await run_in_threadpool(_metadata(request).ensure_project, project_id)
            except Exception:  # noqa: BLE001 - registrar el proyecto no es crítico
                logger.debug("no se pudo registrar el proyecto %s", project_id, exc_info=True)

    logger.debug("ingeridos %d spans", len(spans))
    return Response(
        content=ExportTraceServiceResponse().SerializeToString(),
        media_type=_PROTOBUF,
    )


# ---------------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------------


@router.get("/api/traces", response_model=TraceListPage)
async def list_traces(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    #: Valor de `next_cursor` de la página anterior. Opaco: no lo construyas a mano.
    cursor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    status: str | None = Query(None, pattern="^(ok|error)$"),
    session_id: str | None = None,
    user_id: str | None = None,
    search: str | None = None,
    span_type: str | None = None,
    sort: str = Query("recent", pattern="^(recent|cost|duration)$"),
    #: Filtros que sólo ofrece el modo avanzado del explorador.
    model: str | None = None,
    min_cost_usd: float | None = Query(None, ge=0),
) -> TraceListPage:
    """Lista de trazas del proyecto y rango activos."""
    before, before_trace_id = decode_cursor(cursor)
    filters = TraceFilter(
        project_id=project_id,
        limit=limit,
        before=before,
        before_trace_id=before_trace_id,
        since=since,
        until=until,
        status=status,
        session_id=session_id,
        user_id=user_id,
        search=search,
        span_type=span_type,
        sort=sort,
        model=model,
        min_cost_usd=min_cost_usd,
    )
    store = _store(request)
    page = await run_in_threadpool(store.list_traces, filters)

    # Marca de bucle: una segunda consulta sobre las trazas ya seleccionadas.
    ids = [t.trace_id for t in page.traces]
    with_repeats = await run_in_threadpool(store.traces_with_repeats, project_id, ids)

    return TraceListPage(
        traces=page.traces,
        next_cursor=page.next_cursor,
        with_repeats=sorted(with_repeats),
    )


@router.get("/api/traces/{trace_id}", response_model=Trace)
async def get_trace(
    request: Request, trace_id: str, project_id: str | None = None
) -> Trace:
    """Árbol completo de una traza, con coste por nodo y por subárbol."""
    spans = await run_in_threadpool(_store(request).get_trace_spans, trace_id, project_id)
    if not spans:
        raise HTTPException(status_code=404, detail="traza no encontrada")

    metadata = _metadata(request)
    return Trace(
        summary=summarize(spans, trace_id, project_id or spans[0].project_id),
        roots=build_tree(spans),
        # Huecos reservados: hoy devuelven None y [] (contrato §7).
        diagnosis=await run_in_threadpool(metadata.get_diagnosis, trace_id),
        annotations=await run_in_threadpool(metadata.list_annotations, trace_id),
    )


# ---------------------------------------------------------------------------------
# Diagnóstico y ahorro (Fase 2)
# ---------------------------------------------------------------------------------


def _window(days: int) -> Window:
    """Ventana de análisis. Todas las pantallas comparten el mismo rango."""
    until = datetime.now(timezone.utc)
    return Window(since=until - timedelta(days=days), until=until, days=days)


@router.get("/api/overview", response_model=Overview)
async def get_overview(
    request: Request,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> Overview:
    """Cuánto cuesta el agente, cuánto sobra y qué hay que arreglar."""
    return await run_in_threadpool(overview, _store(request), project_id, _window(days))


@router.get("/api/findings/{finding_id:path}", response_model=FindingDetail)
async def get_finding(
    request: Request,
    finding_id: str,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> FindingDetail:
    """Ficha completa de un hallazgo.

    El identificador es determinista (`tipo:clave`), así que se recalcula sobre la misma
    ventana en lugar de guardarse. Si el problema ya no aparece —porque el usuario lo
    arregló— devuelve 404, que es exactamente lo que queremos decir.
    """
    found = await run_in_threadpool(
        finding_detail, _store(request), project_id, _window(days), finding_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail="ese problema ya no aparece en esta ventana")
    return found


@router.get("/api/projects")
async def list_projects(request: Request) -> dict[str, Any]:
    """Proyectos con datos, con su volumen y coste acumulado."""
    stats = await run_in_threadpool(_store(request).list_projects)
    return {
        "projects": [
            {
                "id": s.project_id,
                "trace_count": s.trace_count,
                "span_count": s.span_count,
                "total_cost_usd": s.total_cost_usd,
                "last_seen": s.last_seen,
            }
            for s in stats
        ]
    }


@router.get("/api/pricing/models")
async def pricing_models() -> dict[str, Any]:
    """Tabla de precios en uso. USD por millón de tokens."""
    table = get_price_table()
    return {
        "models": {
            name: {
                "input": price.input,
                "output": price.output,
                "cached_input": price.cached_input,
            }
            for name, price in sorted(table.models.items())
        }
    }


@router.post("/api/pricing/reload")
async def pricing_reload() -> dict[str, Any]:
    """Recarga la tabla de precios sin reiniciar el proceso (D-006)."""
    table = await run_in_threadpool(reload_price_table)
    return {"models": len(table.models)}


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    store_ok = await run_in_threadpool(_store(request).health)
    metadata_ok = await run_in_threadpool(_metadata(request).health)
    return {
        "status": "ok" if store_ok else "degraded",
        "clickhouse": store_ok,
        "postgres": metadata_ok,
    }
