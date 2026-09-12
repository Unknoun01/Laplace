"""Almacén sobre SQLite: el mismo producto, en un fichero.

Es lo que hace posible `laplace ui`: `pip install` y ver tu primera traza sin cuenta,
sin servidor y sin Docker. No es una versión recortada. Implementa el mismo `SpanStore`
que ClickHouse, así que la ingesta, la API, las tres reglas de detección y la interfaz
son literalmente el mismo código; lo único que cambia es dónde están las filas (D-015).

Dos diferencias con el almacén de nube, las dos a favor de SQLite:

* **Los reenvíos no duplican coste** por la clave primaria, no por un motor de fusión:
  `INSERT OR REPLACE` sobre `(project_id, trace_id, span_id)`. Sin `FINAL`, sin
  `LIMIT 1 BY`, sin partes que colapsar.
* **Las consultas del motor son otras**, porque SQLite no tiene `uniqExact` ni `argMin`.
  Dicen lo mismo y se enseñan igual en «cómo lo hemos detectado»: la interfaz muestra la
  consulta que de verdad se ha ejecutado, que en local es ésta y no la de ClickHouse.

Lo que SQLite no da: concurrencia de escritura alta y agregaciones sobre cientos de
millones de filas. Para un agente en el portátil de quien lo está escribiendo, sobra.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from laplace.schema import Span

from ._rows import COLUMNS, row_to_span, row_to_summary, span_to_row, utc
from .base import (
    Bucket,
    ModelUsage,
    ProjectStats,
    RepeatedGroup,
    StepFacts,
    TraceFilter,
    TracePage,
    Window,
    WindowFacts,
    WindowSummary,
    densify,
    disambiguate,
    encode_cursor,
)

logger = logging.getLogger("laplace.sqlite")

#: Columnas que no son texto ni números sueltos y necesitan traducción al guardar.
_LIST_COLUMNS = {"finish_reasons", "tags"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    project_id            TEXT NOT NULL,
    trace_id              TEXT NOT NULL,
    span_id               TEXT NOT NULL,
    parent_span_id        TEXT NOT NULL DEFAULT '',

    name                  TEXT NOT NULL DEFAULT '',
    span_type             TEXT NOT NULL DEFAULT 'chain',
    status                TEXT NOT NULL DEFAULT 'unset',
    status_message        TEXT NOT NULL DEFAULT '',

    -- ISO-8601 en UTC. Con longitud fija ordena igual como texto que como fecha.
    start_time            TEXT NOT NULL,
    end_time              TEXT NOT NULL,
    duration_ms           REAL NOT NULL DEFAULT 0,

    gen_ai_system         TEXT NOT NULL DEFAULT '',
    operation             TEXT NOT NULL DEFAULT '',
    request_model         TEXT NOT NULL DEFAULT '',
    response_model        TEXT NOT NULL DEFAULT '',
    response_id           TEXT NOT NULL DEFAULT '',

    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens      INTEGER NOT NULL DEFAULT 0,
    usage_estimated       INTEGER NOT NULL DEFAULT 0,

    cost_input_usd        REAL NOT NULL DEFAULT 0,
    cost_output_usd       REAL NOT NULL DEFAULT 0,
    cost_total_usd        REAL NOT NULL DEFAULT 0,
    cost_unknown          INTEGER NOT NULL DEFAULT 0,
    price_rate            TEXT NOT NULL DEFAULT '',
    cost_cache_read_usd   REAL NOT NULL DEFAULT 0,
    cost_cache_write_usd  REAL NOT NULL DEFAULT 0,
    cost_cache_saving_usd REAL NOT NULL DEFAULT 0,
    cost_rate_assumed     INTEGER NOT NULL DEFAULT 0,
    price_note            TEXT NOT NULL DEFAULT '',
    billing_tier          TEXT NOT NULL DEFAULT 'standard',
    billing_region        TEXT NOT NULL DEFAULT 'global',

    input_messages        TEXT NOT NULL DEFAULT '',
    output_messages       TEXT NOT NULL DEFAULT '',
    llm_params            TEXT NOT NULL DEFAULT '',
    finish_reasons        TEXT NOT NULL DEFAULT '',

    tool_name             TEXT NOT NULL DEFAULT '',
    tool_call_id          TEXT NOT NULL DEFAULT '',
    tool_arguments        TEXT NOT NULL DEFAULT '',
    tool_output           TEXT NOT NULL DEFAULT '',

    retrieval_query       TEXT NOT NULL DEFAULT '',
    retrieval_documents   TEXT NOT NULL DEFAULT '',

    input_payload         TEXT NOT NULL DEFAULT '',
    output_payload        TEXT NOT NULL DEFAULT '',

    session_id            TEXT NOT NULL DEFAULT '',
    user_id               TEXT NOT NULL DEFAULT '',
    tags                  TEXT NOT NULL DEFAULT '',
    metadata              TEXT NOT NULL DEFAULT '',

    dedup_hash            TEXT NOT NULL DEFAULT '',
    step_key              TEXT NOT NULL DEFAULT '',
    step_label            TEXT NOT NULL DEFAULT '',
    step_hint             TEXT NOT NULL DEFAULT '',

    events                TEXT NOT NULL DEFAULT '',
    attributes            TEXT NOT NULL DEFAULT '',

    -- El exportador OTLP reintenta en timeouts y 5xx. Con esta clave, un span reenviado
    -- sustituye al original en lugar de duplicar su coste.
    PRIMARY KEY (project_id, trace_id, span_id)
);

CREATE INDEX IF NOT EXISTS idx_spans_trace   ON spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_ventana ON spans (project_id, start_time);
CREATE INDEX IF NOT EXISTS idx_spans_dedup   ON spans (project_id, dedup_hash);
CREATE INDEX IF NOT EXISTS idx_spans_paso    ON spans (project_id, step_key);
"""

WINDOW_WHERE = "project_id = :project_id AND start_time >= :since AND start_time <= :until"

#: El mismo paso, con la misma entrada, repetido dentro de una misma traza.
#:
#: Se detecta por entrada repetida y se reporta por paso (D-062). SQLite no tiene
#: `argMax`, así que el representante de cada grupo sale del truco de concatenar el
#: número de repeticiones por delante y quedarse con el máximo alfabético.
REPEATED_GROUPS_SQL = f"""
WITH numerados AS (
    SELECT
        trace_id, dedup_hash, span_type, request_model, name,
        cost_total_usd, duration_ms, input_tokens, output_tokens,
        cost_unknown, cost_rate_assumed,
        CASE WHEN step_key   != '' THEN step_key   ELSE name END AS paso,
        CASE WHEN step_label != '' THEN step_label ELSE name END AS etiqueta,
        step_hint,
        ROW_NUMBER() OVER (
            PARTITION BY trace_id, dedup_hash ORDER BY start_time, span_id
        ) AS orden
    FROM spans
    WHERE {WINDOW_WHERE} AND dedup_hash != ''
),
por_traza AS (
    SELECT
        trace_id,
        dedup_hash,
        MAX(paso)          AS paso,
        MAX(etiqueta)      AS etiqueta,
        MAX(step_hint)     AS pista,
        MAX(span_type)     AS tipo,
        MAX(request_model) AS modelo,
        COUNT(*)           AS n,
        SUM(CASE WHEN orden > 1 THEN cost_total_usd ELSE 0 END) AS extra_coste,
        SUM(CASE WHEN orden > 1 THEN duration_ms    ELSE 0 END) AS extra_duracion,
        SUM(CASE WHEN orden > 1 THEN input_tokens   ELSE 0 END) AS extra_tok_in,
        SUM(CASE WHEN orden > 1 THEN output_tokens  ELSE 0 END) AS extra_tok_out,
        -- Sólo de las ocurrencias sobrantes: son las únicas cuyo dinero reclamamos.
        SUM(CASE WHEN orden > 1 THEN cost_unknown      ELSE 0 END) AS extra_sin_tarifa,
        SUM(CASE WHEN orden > 1 THEN cost_rate_assumed ELSE 0 END) AS extra_asumida
    FROM numerados
    GROUP BY trace_id, dedup_hash
    HAVING n >= :min_repeats
)
SELECT
    paso,
    modelo,
    MAX(etiqueta)                                          AS nombre,
    MAX(pista)                                             AS pista,
    MAX(tipo)                                              AS tipo,
    substr(MAX(printf('%012d', n) || dedup_hash), 13)      AS hash_ejemplo,
    COUNT(DISTINCT trace_id)                               AS trazas,
    SUM(n)                                                 AS total_spans,
    SUM(n - 1)                                             AS extra_spans,
    SUM(extra_coste)                                       AS extra_coste,
    SUM(extra_duracion)                                    AS extra_duracion,
    SUM(extra_tok_in)                                      AS extra_tok_in,
    SUM(extra_tok_out)                                     AS extra_tok_out,
    SUM(extra_sin_tarifa)                                  AS extra_sin_tarifa,
    SUM(extra_asumida)                                     AS extra_asumida,
    MAX(n)                                                 AS max_por_traza,
    substr(MAX(printf('%012d', n) || trace_id), 13)        AS traza_ejemplo
FROM por_traza
GROUP BY paso, modelo
ORDER BY extra_coste DESC, extra_spans DESC
LIMIT :limit
"""

#: Uso por (paso, modelo): base de las reglas de modelo caro y de contexto fijo.
MODEL_USAGE_SQL = f"""
SELECT
    CASE WHEN step_key != '' THEN step_key ELSE name END AS paso_clave,
    MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS paso,
    MAX(step_hint)                                  AS pista,
    request_model,
    COUNT(*)                                        AS llamadas,
    COUNT(DISTINCT trace_id)                        AS trazas,
    SUM(input_tokens)                               AS in_tok,
    SUM(output_tokens)                              AS out_tok,
    SUM(cached_input_tokens)                        AS cache_tok,
    SUM(cache_write_tokens + cache_write_1h_tokens) AS cache_escrito,
    SUM(cost_cache_saving_usd)                      AS ahorro_cache,
    SUM(cost_total_usd)                             AS coste,
    SUM(cost_unknown = 1)                           AS sin_tarifa,
    SUM(cost_rate_assumed = 1)                      AS tarifa_asumida,
    AVG(output_tokens)                              AS media_salida,
    AVG(input_tokens)                               AS media_entrada,
    MIN(input_tokens)                               AS min_entrada,
    MIN(trace_id)                                   AS traza_ejemplo
FROM spans
WHERE {WINDOW_WHERE} AND span_type = 'llm' AND request_model != ''
GROUP BY paso_clave, request_model
HAVING llamadas >= :min_calls
ORDER BY coste DESC
LIMIT :limit
"""

#: La ventana troceada en tramos, **agregando primero por traza**.
#:
#: El orden importa y es lo que separa este panel de un Grafana peor: si se agrupase
#: por span, «coste del tramo» subiría con el volumen y no diría nada. Agrupando antes
#: por traza, cada tramo sabe cuántas ejecuciones hubo y qué costó cada una.
#:
#: La duración es de traza —de su primer span a su último— porque los spans se solapan
#: y sumarlos daría un número sin significado.
TIMESERIES_SQL = f"""
WITH por_traza AS (
    SELECT
        trace_id,
        MIN(start_time)      AS inicio,
        MAX(end_time)        AS fin,
        COUNT(*)             AS pasos,
        SUM(span_type = 'llm') AS llamadas,
        SUM(cost_total_usd)  AS coste,
        SUM(input_tokens)    AS tok_in,
        SUM(output_tokens)   AS tok_out,
        MAX(status = 'error') AS fallo
    FROM spans
    WHERE {WINDOW_WHERE}
    GROUP BY trace_id
)
SELECT
    CAST((julianday(inicio) - julianday(:origen)) * 1440 / :ancho AS INTEGER) AS tramo,
    COUNT(*)          AS trazas,
    SUM(pasos)        AS pasos,
    SUM(llamadas)     AS llamadas,
    SUM(fallo)        AS trazas_con_error,
    SUM(coste)        AS coste,
    SUM(tok_in)       AS tok_in,
    SUM(tok_out)      AS tok_out,
    -- Redondeado al milisegundo: `julianday` trabaja en días con coma flotante y a
    -- escala de milisegundo deja restos de microsegundo que ClickHouse, que cuenta con
    -- `dateDiff('millisecond')`, no tiene. Nadie mide una espera en microsegundos, y
    -- así las dos series son idénticas y el test de paridad puede ser exacto.
    SUM(CAST(ROUND((julianday(fin) - julianday(inicio)) * 86400000) AS INTEGER)) AS duracion
FROM por_traza
GROUP BY tramo
ORDER BY tramo
"""

#: Agregación por traza. Los alias son parte del contrato con `_rows.row_to_summary`.
_TRACE_AGGREGATE = """
    trace_id,
    MAX(project_id)                                    AS trace_project_id,
    COALESCE(
        MAX(CASE WHEN parent_span_id = '' THEN name END),
        (SELECT s2.name FROM spans s2
          WHERE s2.trace_id = spans.trace_id
          ORDER BY s2.start_time LIMIT 1)
    )                                                  AS root_name,
    MIN(start_time)                                    AS started,
    MAX(end_time)                                      AS ended,
    (julianday(MAX(end_time)) - julianday(MIN(start_time))) * 86400000 AS duration_ms,
    COUNT(*)                                           AS span_count,
    SUM(status = 'error')                              AS error_count,
    SUM(span_type = 'llm')                             AS llm_call_count,
    SUM(span_type = 'tool')                            AS tool_call_count,
    SUM(input_tokens)                                  AS input_tokens,
    SUM(output_tokens)                                 AS output_tokens,
    SUM(cached_input_tokens)                           AS cached_input_tokens,
    SUM(cache_write_tokens)                            AS cache_write_tokens,
    SUM(cache_write_1h_tokens)                         AS cache_write_1h_tokens,
    SUM(reasoning_tokens)                              AS reasoning_tokens,
    SUM(cost_input_usd)                                AS cost_input_usd,
    SUM(cost_output_usd)                               AS cost_output_usd,
    SUM(cost_total_usd)                                AS cost_total_usd,
    SUM(cost_cache_saving_usd)                         AS cache_saving_usd,
    SUM(cost_unknown = 1)                              AS unknown_cost_spans,
    SUM(cost_rate_assumed = 1)                         AS assumed_rate_spans,
    MAX(session_id)                                    AS trace_session_id,
    MAX(user_id)                                       AS trace_user_id,
    GROUP_CONCAT(DISTINCT NULLIF(request_model, ''))   AS modelos
"""


class SQLiteStore:
    """Implementación de `SpanStore` sobre un fichero SQLite."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._local = threading.local()

    @property
    def path(self) -> Path:
        return self._path

    # -- conexión --------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        """Una conexión por hilo.

        FastAPI atiende cada petición en un hilo del pool y una conexión de SQLite no
        se puede compartir entre hilos. Es el mismo motivo por el que el almacén de
        ClickHouse tiene su cliente en `threading.local` (D-014).
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            # WAL: la ingesta escribe mientras la interfaz lee, y sin esto la lectura
            # se bloquearía justo cuando el usuario está mirando.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def _query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, params or {})
        return [dict(row) for row in cursor.fetchall()]

    # -- escritura -------------------------------------------------------------------

    def migrate(self) -> None:
        """Crea el esquema si no existe. Idempotente."""
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        logger.info("sqlite listo en %s", self._path)

    def insert_spans(self, spans: list[Span]) -> int:
        if not spans:
            return 0
        columnas = ", ".join(COLUMNS)
        marcas = ", ".join("?" for _ in COLUMNS)
        sql = f"INSERT OR REPLACE INTO spans ({columnas}) VALUES ({marcas})"
        self._conn.executemany(sql, [self._to_row(span) for span in spans])
        self._conn.commit()
        return len(spans)

    @staticmethod
    def _to_row(span: Span) -> list[Any]:
        fila = span_to_row(span)
        for indice, columna in enumerate(COLUMNS):
            valor = fila[indice]
            if columna in _LIST_COLUMNS:
                fila[indice] = json.dumps(list(valor), ensure_ascii=False)
            elif isinstance(valor, datetime):
                fila[indice] = utc(valor).isoformat()
        return fila

    # -- lectura ---------------------------------------------------------------------

    def list_traces(self, filters: TraceFilter) -> TracePage:
        where, params = self._where(filters)
        params["limit"] = max(1, min(filters.limit, 200))

        orden = {
            "cost": "cost_total_usd DESC, trace_id DESC",
            "duration": "duration_ms DESC, trace_id DESC",
        }.get(filters.sort, "started DESC, trace_id DESC")
        por_tiempo = filters.sort not in ("cost", "duration")

        having: list[str] = []
        if filters.before is not None and por_tiempo:
            params["before"] = _iso(filters.before)
            params["before_id"] = filters.before_trace_id or ""
            # Por tupla: dos agentes lanzados a la vez empiezan en el mismo instante y
            # una de las trazas se perdería entre páginas si sólo se comparase la fecha.
            having.append("(started < :before OR (started = :before AND trace_id < :before_id))")
        if filters.min_cost_usd is not None:
            having.append("cost_total_usd >= :min_cost")
            params["min_cost"] = float(filters.min_cost_usd)
        clausula = ("HAVING " + " AND ".join(having)) if having else ""

        sql = f"""
            SELECT {_TRACE_AGGREGATE}
            FROM spans
            {where}
            GROUP BY trace_id
            {clausula}
            ORDER BY {orden}
            LIMIT :limit
        """
        traces = [row_to_summary(r) for r in self._query(sql, params)]
        llena = len(traces) == params["limit"]
        return TracePage(
            traces=traces,
            next_cursor=encode_cursor(traces[-1]) if llena and por_tiempo else None,
        )

    def _where(self, filters: TraceFilter) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if filters.project_id:
            clauses.append("project_id = :project_id")
            params["project_id"] = filters.project_id
        if filters.since is not None:
            clauses.append("start_time >= :since")
            params["since"] = _iso(filters.since)
        if filters.until is not None:
            clauses.append("start_time <= :until")
            params["until"] = _iso(filters.until)
        if filters.session_id:
            clauses.append("session_id = :session_id")
            params["session_id"] = filters.session_id
        if filters.user_id:
            clauses.append("user_id = :user_id")
            params["user_id"] = filters.user_id
        if filters.model:
            # El modelo es de un span, no de la traza: se filtra por trazas que lo usan.
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE request_model = :model)"
            )
            params["model"] = filters.model

        # El estado es una propiedad de la traza entera: «ok» significa que ninguno de
        # sus spans falló, así que va por exclusión.
        fallidas = "SELECT DISTINCT trace_id FROM spans WHERE status = 'error'"
        if filters.status == "error":
            clauses.append(f"trace_id IN ({fallidas})")
        elif filters.status == "ok":
            clauses.append(f"trace_id NOT IN ({fallidas})")

        sub: list[str] = []
        if filters.span_type:
            sub.append("span_type = :span_type")
            params["span_type"] = filters.span_type
        if filters.search:
            sub.append("(instr(lower(name), lower(:search)) > 0 OR trace_id LIKE :prefijo)")
            params["search"] = filters.search
            params["prefijo"] = f"{filters.search}%"
        if sub:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE "
                + " AND ".join(sub)
                + ")"
            )

        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    def get_trace_spans(self, trace_id: str, project_id: str | None = None) -> list[Span]:
        params: dict[str, Any] = {"trace_id": trace_id}
        where = "trace_id = :trace_id"
        if project_id:
            where += " AND project_id = :project_id"
            params["project_id"] = project_id
        columnas = ", ".join(COLUMNS)
        sql = f"SELECT {columnas} FROM spans WHERE {where} ORDER BY start_time"
        return [row_to_span(r) for r in self._query(sql, params)]

    def list_projects(self) -> list[ProjectStats]:
        sql = """
            SELECT project_id,
                   COUNT(DISTINCT trace_id) AS traces,
                   COUNT(*)                 AS spans,
                   SUM(cost_total_usd)      AS cost,
                   MAX(start_time)          AS last_seen
            FROM spans
            GROUP BY project_id
            ORDER BY last_seen DESC
        """
        return [
            ProjectStats(
                project_id=r["project_id"],
                trace_count=int(r["traces"]),
                span_count=int(r["spans"]),
                total_cost_usd=float(r["cost"] or 0.0),
                last_seen=utc(r["last_seen"]) if r["last_seen"] else None,
            )
            for r in self._query(sql)
        ]

    # -- analítica del motor de detección --------------------------------------------

    @property
    def repeated_groups_sql(self) -> str:
        return REPEATED_GROUPS_SQL

    @property
    def model_usage_sql(self) -> str:
        return MODEL_USAGE_SQL

    @staticmethod
    def _window_params(project_id: str, window: Window) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "since": _iso(window.since),
            "until": _iso(window.until),
        }

    def summarize_window(self, project_id: str, window: Window) -> WindowSummary:
        params = self._window_params(project_id, window)
        sql = f"""
            SELECT
                COUNT(DISTINCT trace_id)                                   AS traces,
                COUNT(*)                                                   AS spans,
                COUNT(DISTINCT CASE WHEN status = 'error' THEN trace_id END) AS error_traces,
                SUM(span_type = 'llm')                                     AS llm_calls,
                SUM(span_type = 'tool')                                    AS tool_calls,
                SUM(input_tokens)                                          AS input_tokens,
                SUM(output_tokens)                                         AS output_tokens,
                SUM(cost_total_usd)                                        AS cost,
                SUM(cost_cache_saving_usd)                                 AS ahorro_cache,
                SUM(cost_unknown = 1)                                      AS sin_tarifa,
                SUM(cost_rate_assumed = 1)                                 AS tarifa_asumida,
                MIN(start_time)                                            AS primero,
                MAX(start_time)                                            AS ultimo
            FROM spans
            WHERE {WINDOW_WHERE}
        """
        filas = self._query(sql, params)
        if not filas or not filas[0]["spans"]:
            return WindowSummary()
        r = filas[0]

        sin_precio = self._query(
            f"""SELECT DISTINCT request_model FROM spans
                WHERE {WINDOW_WHERE} AND cost_unknown = 1 AND request_model != ''""",
            params,
        )

        # La latencia que importa es la de la traza entera, no la de un span suelto.
        # SQLite no trae percentiles, así que se ordenan las duraciones y se toma la
        # posición: con los volúmenes de un portátil, es instantáneo y es exacto.
        duraciones = [
            float(f["duracion"])
            for f in self._query(
                f"""SELECT (julianday(MAX(end_time)) - julianday(MIN(start_time))) * 86400000
                           AS duracion
                    FROM spans WHERE {WINDOW_WHERE}
                    GROUP BY trace_id ORDER BY duracion""",
                params,
            )
        ]
        posicion = min(int(len(duraciones) * 0.95), len(duraciones) - 1)
        p95 = duraciones[posicion] if duraciones else 0.0

        return WindowSummary(
            traces=int(r["traces"]),
            spans=int(r["spans"]),
            error_traces=int(r["error_traces"]),
            llm_calls=int(r["llm_calls"]),
            tool_calls=int(r["tool_calls"]),
            input_tokens=int(r["input_tokens"] or 0),
            output_tokens=int(r["output_tokens"] or 0),
            total_cost_usd=float(r["cost"] or 0.0),
            p95_duration_ms=p95,
            cache_saving_usd=float(r["ahorro_cache"] or 0.0),
            unknown_cost_spans=int(r["sin_tarifa"] or 0),
            assumed_rate_spans=int(r["tarifa_asumida"] or 0),
            models_without_price=sorted(f["request_model"] for f in sin_precio),
            first_seen=utc(r["primero"]) if r["primero"] else None,
            last_seen=utc(r["ultimo"]) if r["ultimo"] else None,
        )

    def repeated_groups(
        self, project_id: str, window: Window, *, min_repeats: int = 3, limit: int = 20
    ) -> list[RepeatedGroup]:
        params = self._window_params(project_id, window)
        params["min_repeats"] = min_repeats
        params["limit"] = limit
        grupos = [
            RepeatedGroup(
                dedup_hash=r["hash_ejemplo"],
                name=r["nombre"],
                hint=r["pista"] or "",
                span_type=r["tipo"],
                model=r["modelo"] or "",
                step_key=r["paso"] or "",
                traces=int(r["trazas"]),
                total_spans=int(r["total_spans"]),
                extra_spans=int(r["extra_spans"]),
                extra_cost_usd=float(r["extra_coste"]),
                extra_duration_ms=float(r["extra_duracion"]),
                extra_input_tokens=int(r["extra_tok_in"]),
                extra_output_tokens=int(r["extra_tok_out"]),
                extra_unknown_cost_spans=int(r["extra_sin_tarifa"] or 0),
                extra_assumed_rate_spans=int(r["extra_asumida"] or 0),
                max_per_trace=int(r["max_por_traza"]),
                sample_trace_id=r["traza_ejemplo"],
            )
            for r in self._query(REPEATED_GROUPS_SQL, params)
        ]
        return disambiguate(grupos)

    def model_usage(
        self, project_id: str, window: Window, *, min_calls: int = 5, limit: int = 50
    ) -> list[ModelUsage]:
        params = self._window_params(project_id, window)
        params["min_calls"] = min_calls
        params["limit"] = limit
        usos = [
            ModelUsage(
                key=r["paso_clave"],
                name=r["paso"],
                hint=r["pista"] or "",
                model=r["request_model"],
                calls=int(r["llamadas"]),
                traces=int(r["trazas"]),
                input_tokens=int(r["in_tok"]),
                output_tokens=int(r["out_tok"]),
                cached_input_tokens=int(r["cache_tok"]),
                cache_write_tokens=int(r["cache_escrito"]),
                cache_saving_usd=float(r["ahorro_cache"]),
                cost_usd=float(r["coste"]),
                unknown_cost_spans=int(r["sin_tarifa"] or 0),
                assumed_rate_spans=int(r["tarifa_asumida"] or 0),
                avg_output_tokens=float(r["media_salida"]),
                avg_input_tokens=float(r["media_entrada"]),
                min_input_tokens=int(r["min_entrada"]),
                sample_trace_id=r["traza_ejemplo"],
            )
            for r in self._query(MODEL_USAGE_SQL, params)
        ]
        return disambiguate(usos)

    def traces_with_repeats(
        self, project_id: str | None, trace_ids: list[str], *, min_repeats: int = 3
    ) -> set[str]:
        if not trace_ids:
            return set()
        marcas = ", ".join(f":t{i}" for i in range(len(trace_ids)))
        params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(trace_ids)}
        params["min_repeats"] = min_repeats
        where = f"trace_id IN ({marcas}) AND dedup_hash != ''"
        if project_id:
            where += " AND project_id = :project_id"
            params["project_id"] = project_id
        sql = f"""
            SELECT trace_id FROM spans
            WHERE {where}
            GROUP BY trace_id, dedup_hash
            HAVING COUNT(*) >= :min_repeats
        """
        return {r["trace_id"] for r in self._query(sql, params)}

    def sample_repetition(
        self, project_id: str, window: Window, dedup_hash: str, limit: int = 40
    ) -> list[Span]:
        params = self._window_params(project_id, window)
        params["dedup_hash"] = dedup_hash
        params["limit"] = limit
        trazas = self._query(
            f"""SELECT trace_id, COUNT(*) AS n FROM spans
                WHERE {WINDOW_WHERE} AND dedup_hash = :dedup_hash
                GROUP BY trace_id ORDER BY n DESC LIMIT 1""",
            params,
        )
        if not trazas:
            return []
        params["trace_id"] = trazas[0]["trace_id"]
        columnas = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columnas} FROM spans
            WHERE trace_id = :trace_id AND dedup_hash = :dedup_hash
            ORDER BY start_time LIMIT :limit
        """
        return [row_to_span(r) for r in self._query(sql, params)]

    # -- panel (Fase 4) ---------------------------------------------------------------

    def timeseries(
        self, project_id: str, window: Window, bucket_minutes: int
    ) -> list[Bucket]:
        params = self._window_params(project_id, window)
        params["origen"] = _iso(window.since)
        params["ancho"] = bucket_minutes
        filas = self._query(TIMESERIES_SQL, params)
        return densify(
            [
                (
                    int(r["tramo"]),
                    Bucket(
                        start=window.since,
                        traces=int(r["trazas"]),
                        spans=int(r["pasos"] or 0),
                        llm_calls=int(r["llamadas"] or 0),
                        error_traces=int(r["trazas_con_error"] or 0),
                        cost_usd=float(r["coste"] or 0.0),
                        input_tokens=int(r["tok_in"] or 0),
                        output_tokens=int(r["tok_out"] or 0),
                        duration_ms_sum=float(r["duracion"] or 0.0),
                    ),
                )
                for r in filas
            ],
            window,
            bucket_minutes,
        )

    def window_facts(
        self, project_id: str, since: datetime, until: datetime
    ) -> WindowFacts:
        params = {"project_id": project_id, "since": _iso(since), "until": _iso(until)}
        cabecera = self._query(
            f"""SELECT COUNT(DISTINCT trace_id) AS trazas, SUM(cost_total_usd) AS coste
                FROM spans WHERE {WINDOW_WHERE}""",
            params,
        )
        modelos = self._query(
            f"""SELECT DISTINCT request_model AS v FROM spans
                WHERE {WINDOW_WHERE} AND request_model != ''""",
            params,
        )
        herramientas = self._query(
            f"""SELECT DISTINCT COALESCE(NULLIF(tool_name, ''), name) AS v FROM spans
                WHERE {WINDOW_WHERE} AND span_type = 'tool'""",
            params,
        )
        pasos = self._query(
            f"""SELECT
                    CASE WHEN step_label != '' THEN step_label ELSE name END AS paso,
                    SUM(cost_total_usd) AS coste,
                    COUNT(*)            AS llamadas
                FROM spans WHERE {WINDOW_WHERE}
                GROUP BY paso""",
            params,
        )
        r = cabecera[0] if cabecera else {}
        return WindowFacts(
            traces=int(r.get("trazas") or 0),
            cost_usd=float(r.get("coste") or 0.0),
            models={f["v"] for f in modelos if f["v"]},
            tools={f["v"] for f in herramientas if f["v"]},
            steps={
                f["paso"]: StepFacts(
                    cost_usd=float(f["coste"] or 0.0), calls=int(f["llamadas"])
                )
                for f in pasos
                if f["paso"]
            },
        )

    def delete_project(self, project_id: str) -> None:
        self._conn.execute("DELETE FROM spans WHERE project_id = :p", {"p": project_id})
        self._conn.commit()

    def health(self) -> bool:
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("sqlite no responde", exc_info=True)
            return False


def _iso(value: datetime) -> str:
    return utc(value).isoformat()
