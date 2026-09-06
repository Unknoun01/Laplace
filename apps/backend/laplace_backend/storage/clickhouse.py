"""Almacén de spans sobre ClickHouse."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import clickhouse_connect
from laplace.schema import (
    Cost,
    LLMAttributes,
    RetrievalAttributes,
    Span,
    SpanEvent,
    TokenUsage,
    ToolAttributes,
    TraceSummary,
)

from ..config import Settings
from .base import ProjectStats, TraceFilter, TracePage, encode_cursor

logger = logging.getLogger("laplace.storage")

_SCHEMA = Path(__file__).with_name("clickhouse_schema.sql")

#: Orden de columnas de la tabla `spans`. Los inserts se construyen contra esta lista.
COLUMNS = (
    "project_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "span_type",
    "status",
    "status_message",
    "start_time",
    "end_time",
    "duration_ms",
    "gen_ai_system",
    "operation",
    "request_model",
    "response_model",
    "response_id",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "cost_input_usd",
    "cost_output_usd",
    "cost_total_usd",
    "cost_estimated",
    "input_messages",
    "output_messages",
    "llm_params",
    "finish_reasons",
    "tool_name",
    "tool_call_id",
    "tool_arguments",
    "tool_output",
    "retrieval_query",
    "retrieval_documents",
    "input_payload",
    "output_payload",
    "session_id",
    "user_id",
    "tags",
    "metadata",
    "dedup_hash",
    "events",
    "attributes",
)


def _json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"_unserializable": str(value)[:2000]})


def _loads(text: str, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ClickHouseStore:
    """Implementación de `SpanStore` sobre ClickHouse."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connect_args = {
            "host": settings.clickhouse_host,
            "port": settings.clickhouse_port,
            "username": settings.clickhouse_user,
            "password": settings.clickhouse_password,
            "database": settings.clickhouse_database,
            "secure": settings.clickhouse_secure,
        }
        self._local = threading.local()

    @property
    def _client(self) -> Any:
        """Un cliente por hilo.

        `clickhouse-connect` rechaza consultas concurrentes sobre el mismo cliente
        ("concurrent queries within the same session"), y FastAPI sirve las lecturas
        desde un pool de hilos: la lista de trazas y la de proyectos se piden a la vez
        en la pantalla principal. Como el pool reutiliza hilos, esto son unos pocos
        clientes, no uno por petición.
        """
        client = getattr(self._local, "client", None)
        if client is None:
            client = clickhouse_connect.get_client(**self._connect_args)
            self._local.client = client
        return client

    # -- esquema -------------------------------------------------------------------

    def migrate(self) -> None:
        database = self._settings.clickhouse_database
        self._client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
        for statement in _statements(_SCHEMA.read_text(encoding="utf-8")):
            self._client.command(statement)
        logger.info("esquema de clickhouse aplicado")

    # -- escritura -----------------------------------------------------------------

    def insert_spans(self, spans: list[Span]) -> int:
        if not spans:
            return 0
        self._client.insert(
            "spans",
            [self._to_row(span) for span in spans],
            column_names=list(COLUMNS),
        )
        return len(spans)

    @staticmethod
    def _to_row(span: Span) -> list[Any]:
        llm = span.llm
        tool = span.tool
        retrieval = span.retrieval
        usage = llm.usage if llm else TokenUsage()
        cost = llm.cost if llm else Cost()

        return [
            span.project_id,
            span.trace_id,
            span.span_id,
            span.parent_span_id or "",
            span.name,
            span.type,
            span.status,
            span.status_message,
            _utc(span.start_time),
            _utc(span.end_time),
            float(span.duration_ms),
            (llm.system if llm else None) or "",
            (llm.operation if llm else None) or "",
            (llm.request_model if llm else None) or "",
            (llm.response_model if llm else None) or "",
            (llm.response_id if llm else None) or "",
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_input_tokens,
            usage.reasoning_tokens,
            cost.input_usd,
            cost.output_usd,
            cost.total_usd,
            1 if cost.estimated else 0,
            _json(llm.input_messages) if llm else "",
            _json(llm.output_messages) if llm else "",
            _json(llm.params) if llm else "",
            list(llm.finish_reasons) if llm else [],
            (tool.name if tool else None) or "",
            (tool.call_id if tool else None) or "",
            _json(tool.arguments) if tool else "",
            _json(tool.output) if tool else "",
            (retrieval.query if retrieval else None) or "",
            _json(retrieval.documents) if retrieval else "",
            _json(span.input),
            _json(span.output),
            span.session_id or "",
            span.user_id or "",
            list(span.tags),
            _json(span.metadata),
            span.dedup_hash,
            _json([event.model_dump(mode="json") for event in span.events]),
            _json(span.attributes),
        ]

    # -- lectura -------------------------------------------------------------------

    def list_traces(self, filters: TraceFilter) -> TracePage:
        where, params = self._where(filters)
        params["limit"] = max(1, min(filters.limit, 200))

        having = ""
        if filters.before is not None:
            params["before"] = _utc(filters.before)
            params["before_id"] = filters.before_trace_id or ""
            # Comparación por tupla: avanza aunque varias trazas compartan instante.
            having = "HAVING (started, trace_id) < (%(before)s, %(before_id)s)"

        # Los alias de las agregaciones no pueden llamarse igual que la columna que
        # agregan: ClickHouse resuelve el nombre del WHERE contra el alias y falla con
        # ILLEGAL_AGGREGATION en cuanto se filtra por proyecto, sesión o usuario.
        sql = f"""
            SELECT
                trace_id,
                any(project_id)                                        AS trace_project_id,
                if(maxIf(name, parent_span_id = '') != '',
                   maxIf(name, parent_span_id = ''),
                   argMin(name, start_time))                           AS root_name,
                min(start_time)                                        AS started,
                max(end_time)                                          AS ended,
                dateDiff('millisecond', min(start_time), max(end_time)) AS duration_ms,
                count()                                                AS span_count,
                countIf(status = 'error')                              AS error_count,
                countIf(span_type = 'llm')                             AS llm_call_count,
                countIf(span_type = 'tool')                            AS tool_call_count,
                sum(input_tokens)                                      AS input_tokens,
                sum(output_tokens)                                     AS output_tokens,
                sum(cached_input_tokens)                               AS cached_input_tokens,
                sum(reasoning_tokens)                                  AS reasoning_tokens,
                sum(cost_input_usd)                                    AS cost_input_usd,
                sum(cost_output_usd)                                   AS cost_output_usd,
                sum(cost_total_usd)                                    AS cost_total_usd,
                max(cost_estimated)                                    AS cost_estimated,
                max(session_id)                                        AS trace_session_id,
                max(user_id)                                           AS trace_user_id
            FROM spans FINAL
            {where}
            GROUP BY trace_id
            {having}
            ORDER BY started DESC, trace_id DESC
            LIMIT %(limit)s
        """
        rows = self._client.query(sql, parameters=params).result_rows

        traces = [self._to_summary(row) for row in rows]
        # Sólo hay siguiente página si ésta vino llena.
        full_page = len(traces) == params["limit"]
        next_cursor = encode_cursor(traces[-1]) if full_page else None
        return TracePage(traces=traces, next_cursor=next_cursor)

    def _where(self, filters: TraceFilter) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if filters.project_id:
            clauses.append("project_id = %(project_id)s")
            params["project_id"] = filters.project_id
        if filters.since is not None:
            clauses.append("start_time >= %(since)s")
            params["since"] = _utc(filters.since)
        if filters.until is not None:
            clauses.append("start_time <= %(until)s")
            params["until"] = _utc(filters.until)
        if filters.session_id:
            clauses.append("session_id = %(session_id)s")
            params["session_id"] = filters.session_id
        if filters.user_id:
            clauses.append("user_id = %(user_id)s")
            params["user_id"] = filters.user_id

        # El estado es una propiedad de la traza entera, no de un span: "ok" significa
        # que ninguno de sus spans falló, así que va por exclusión.
        failed = "SELECT DISTINCT trace_id FROM spans WHERE status = 'error'"
        if filters.status == "error":
            clauses.append(f"trace_id IN ({failed})")
        elif filters.status == "ok":
            clauses.append(f"trace_id NOT IN ({failed})")

        # Los demás filtros por traza (búsqueda, tipo) también se resuelven con una
        # subconsulta de ids: filtrar spans sueltos rompería las agregaciones.
        sub: list[str] = []
        if filters.span_type:
            sub.append("span_type = %(span_type)s")
            params["span_type"] = filters.span_type
        if filters.search:
            sub.append(
                "(positionCaseInsensitive(name, %(search)s) > 0"
                " OR startsWith(trace_id, %(search)s))"
            )
            params["search"] = filters.search
        if sub:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE " + " AND ".join(sub) + ")"
            )

        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    @staticmethod
    def _to_summary(row: tuple) -> TraceSummary:
        (
            trace_id,
            project_id,
            root_name,
            started,
            ended,
            duration_ms,
            span_count,
            error_count,
            llm_call_count,
            tool_call_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            reasoning_tokens,
            cost_input_usd,
            cost_output_usd,
            cost_total_usd,
            cost_estimated,
            session_id,
            user_id,
        ) = row

        return TraceSummary(
            trace_id=trace_id,
            project_id=project_id,
            root_name=root_name,
            status="error" if error_count else "ok",
            start_time=_utc(started),
            end_time=_utc(ended),
            duration_ms=float(duration_ms),
            span_count=int(span_count),
            error_count=int(error_count),
            llm_call_count=int(llm_call_count),
            tool_call_count=int(tool_call_count),
            usage=TokenUsage(
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                cached_input_tokens=int(cached_input_tokens),
                reasoning_tokens=int(reasoning_tokens),
            ),
            cost=Cost(
                input_usd=float(cost_input_usd),
                output_usd=float(cost_output_usd),
                total_usd=float(cost_total_usd),
                estimated=bool(cost_estimated),
            ),
            session_id=session_id or None,
            user_id=user_id or None,
        )

    def get_trace_spans(self, trace_id: str, project_id: str | None = None) -> list[Span]:
        params: dict[str, Any] = {"trace_id": trace_id}
        where = "trace_id = %(trace_id)s"
        if project_id:
            where += " AND project_id = %(project_id)s"
            params["project_id"] = project_id

        # Deliberadamente SIN `FINAL`. Ésta es la consulta que se ejecuta cada vez que
        # alguien abre una traza, y `FINAL` obliga a ClickHouse a leer la partición
        # entera en lugar de los gránulos que el índice de `trace_id` selecciona:
        # medido sobre 800.000 spans, 300.349 filas leídas con FINAL frente a 16.384
        # sin él. `LIMIT 1 BY span_id` sobre `ingested_at DESC` da exactamente la misma
        # garantía que necesitamos —quedarnos con la última versión de cada span si el
        # exportador reintentó— conservando la poda.
        columns = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columns} FROM (
                SELECT {columns}
                FROM spans
                WHERE {where}
                ORDER BY span_id, ingested_at DESC
                LIMIT 1 BY span_id
            )
            ORDER BY start_time
        """
        rows = self._client.query(sql, parameters=params).result_rows
        return [self._to_span(row) for row in rows]

    @staticmethod
    def _to_span(row: tuple) -> Span:
        r = dict(zip(COLUMNS, row, strict=True))
        span_type = r["span_type"]

        llm = None
        if span_type == "llm":
            llm = LLMAttributes(
                system=r["gen_ai_system"] or None,
                operation=r["operation"] or None,
                request_model=r["request_model"] or None,
                response_model=r["response_model"] or None,
                response_id=r["response_id"] or None,
                usage=TokenUsage(
                    input_tokens=int(r["input_tokens"]),
                    output_tokens=int(r["output_tokens"]),
                    cached_input_tokens=int(r["cached_input_tokens"]),
                    reasoning_tokens=int(r["reasoning_tokens"]),
                ),
                cost=Cost(
                    input_usd=float(r["cost_input_usd"]),
                    output_usd=float(r["cost_output_usd"]),
                    total_usd=float(r["cost_total_usd"]),
                    estimated=bool(r["cost_estimated"]),
                ),
                input_messages=_loads(r["input_messages"], []) or [],
                output_messages=_loads(r["output_messages"], []) or [],
                params=_loads(r["llm_params"], {}) or {},
                finish_reasons=list(r["finish_reasons"] or []),
            )

        tool = None
        if span_type == "tool":
            tool = ToolAttributes(
                name=r["tool_name"] or None,
                call_id=r["tool_call_id"] or None,
                arguments=_loads(r["tool_arguments"], None),
                output=_loads(r["tool_output"], None),
            )

        retrieval = None
        if span_type == "retrieval":
            retrieval = RetrievalAttributes(
                query=r["retrieval_query"] or None,
                documents=_loads(r["retrieval_documents"], []) or [],
            )

        raw_events = _loads(r["events"], []) or []
        events = [SpanEvent(**event) for event in raw_events if isinstance(event, dict)]

        return Span(
            span_id=r["span_id"],
            trace_id=r["trace_id"],
            parent_span_id=r["parent_span_id"] or None,
            project_id=r["project_id"],
            name=r["name"],
            type=span_type,
            status=r["status"],
            status_message=r["status_message"],
            start_time=_utc(r["start_time"]),
            end_time=_utc(r["end_time"]),
            duration_ms=float(r["duration_ms"]),
            llm=llm,
            tool=tool,
            retrieval=retrieval,
            input=_loads(r["input_payload"], None),
            output=_loads(r["output_payload"], None),
            session_id=r["session_id"] or None,
            user_id=r["user_id"] or None,
            tags=list(r["tags"] or []),
            metadata=_loads(r["metadata"], {}) or {},
            dedup_hash=r["dedup_hash"],
            events=events,
            attributes=_loads(r["attributes"], {}) or {},
        )

    def list_projects(self) -> list[ProjectStats]:
        sql = """
            SELECT project_id,
                   uniqExact(trace_id) AS traces,
                   count()             AS spans,
                   sum(cost_total_usd) AS cost,
                   max(start_time)     AS last_seen
            FROM spans FINAL
            GROUP BY project_id
            ORDER BY last_seen DESC
        """
        return [
            ProjectStats(
                project_id=row[0],
                trace_count=int(row[1]),
                span_count=int(row[2]),
                total_cost_usd=float(row[3]),
                last_seen=_utc(row[4]) if row[4] else None,
            )
            for row in self._client.query(sql).result_rows
        ]

    def delete_project(self, project_id: str) -> None:
        """Borra todos los spans de un proyecto.

        Hoy sólo lo usan las pruebas, para no dejar proyectos sembrados en la base de
        datos de desarrollo. Cuando haya cuentas, es también el borrado que exige el
        RGPD: si un cliente pide que se vayan sus datos, tienen que irse de verdad.
        """
        self._client.command(
            "DELETE FROM spans WHERE project_id = %(project_id)s",
            parameters={"project_id": project_id},
        )

    def health(self) -> bool:
        try:
            self._client.command("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("clickhouse no responde", exc_info=True)
            return False


def _statements(sql: str) -> list[str]:
    """Parte un fichero .sql en sentencias, ignorando comentarios."""
    cleaned = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in cleaned.split(";") if s.strip()]
