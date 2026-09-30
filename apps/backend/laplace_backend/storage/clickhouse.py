"""Almacén de spans sobre ClickHouse."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import clickhouse_connect
from laplace.schema import Span
from laplace.semconv import EVAL_TAG

from ..config import Settings
from . import preagregados
from ._rows import (
    COLUMNS,
    coverage_from_rows,
    row_to_observed_prompt,
    row_to_prompt_usage,
    row_to_span,
    row_to_summary,
    row_to_trace_cost,
    span_to_row,
)
from ._rows import utc as _utc
from .base import (
    MARGEN_FILTROS,
    Bucket,
    CostGroup,
    CoverageFacts,
    Latency,
    LoopGroup,
    ModelUsage,
    ObservedPrompt,
    ProjectStats,
    PromptUsage,
    RepeatedGroup,
    StepCostSeries,
    StepFacts,
    TraceCost,
    TraceFilter,
    TracePage,
    Window,
    WindowFacts,
    WindowSummary,
    _sujeto,
    densify,
    densify_steps,
    disambiguate,
    encode_cursor,
)

#: El texto en el que busca `TraceFilter.content`. **Tiene que ser idéntica** a la
#: expresión de `idx_contenido` en `clickhouse_schema.sql`: el analizador sólo usa el
#: índice si la consulta escribe la misma, y cualquier constante dentro —un separador
#: entre columnas— hace que deje de reconocerla (D-144). Por eso las columnas van
#: pegadas: casar a caballo entre dos es posible, pero no cambia qué traza se encuentra
#: más que en casos rebuscados.
CONTENIDO = (
    "lowerUTF8(concat(input_messages, output_messages, tool_arguments, tool_output, "
    "retrieval_query, retrieval_documents, input_payload, output_payload))"
)


def literal_like(texto: str) -> str:
    """`texto` para un `LIKE`, sin que `%`, `_` ni la barra invertida hagan de comodín."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


logger = logging.getLogger("laplace.storage")

_SCHEMA = Path(__file__).with_name("clickhouse_schema.sql")

# ---------------------------------------------------------------------------------
# Consultas del motor de detección (Fase 2)
#
# Son constantes de módulo, no cadenas incrustadas en los métodos, porque la interfaz
# enseña al usuario **la consulta que de verdad se ha ejecutado** ("cómo lo hemos
# detectado"). Si estuviera copiada a mano en la UI, acabaría mintiendo.
# ---------------------------------------------------------------------------------

WINDOW_WHERE = "project_id = %(project_id)s AND start_time >= %(since)s AND start_time <= %(until)s"

#: Las reglas no miran las tiradas de evaluación. Son gasto de verdad y se cuentan en el
#: gasto, pero un experimento lanzado a propósito —el modelo caro contra el barato— no
#: es un derroche que nadie haya visto, y el inicio lo enseñaba como tal (D-135). La
#: etiqueta la lleva la raíz, así que se excluye por traza.
RULES_WHERE = (
    f"{WINDOW_WHERE} AND trace_id NOT IN (SELECT trace_id FROM spans WHERE {WINDOW_WHERE} "
    f"AND has(tags, '{EVAL_TAG}'))"
)
#: Hasta cuántas trazas de evaluación se pasan como lista (D-177). Con más, que sería una
#: ventana casi toda de tiradas, se deja la subconsulta: una lista enorme en cada
#: consulta cuesta más que lo que ahorra.
_MAX_EVALUACIONES = 5000

#: Sobre el determinismo de estas consultas, que ya ha mordido una vez y por eso está
#: escrito aquí arriba y no en un comentario suelto: **ninguna agregación de esta tabla
#: puede escoger «una fila cualquiera»**. `any()` lo hace por definición, y `argMax(x, k)`
#: y `argMin(x, k)` lo hacen cuando hay empate en `k`, que es el caso **normal** y no el
#: raro: dos trazas que repiten un paso tres veces empatan en `n`, y dos spans lanzados
#: en paralelo empatan en `start_time`.
#:
#: SQLite no tiene `any()`: usa `MAX()` y desempata por `span_id`. Así que aquí se usa
#: `max()`/`min()` donde allí se usa `MAX()`/`MIN()`, y las claves de `argMax`/`argMin`
#: llevan el desempate dentro de una tupla. No es cosmética: sin esto, el mismo proyecto
#: con los mismos spans puede enseñar un paso de ejemplo distinto en local y en la nube,
#: y eso afecta a las reglas de detección, que es el peor sitio posible (D-099).

#: Un mismo paso, con la misma entrada, repetido dentro de una misma traza.
#:
#: Se detecta por entrada repetida (`dedup_hash`) pero se **reporta por paso**: un
#: agente que reintenta lo mismo lo hace con cada pregunta de cada usuario, así que
#: agrupar el informe por entrada llenaba el panel de tarjetas idénticas —una por
#: pregunta— diciendo todas lo mismo. Es un solo problema y se arregla una sola vez.
REPEATED_GROUPS_SQL = f"""
SELECT
    paso,
    modelo,
    max(etiqueta)                    AS nombre,
    max(pista)                       AS pista,
    max(sitio)                       AS sitio,
    max(tipo)                        AS tipo,
    -- El desempate va DENTRO de la clave: con `argMax(x, n)` a secas, dos trazas que
    -- repiten lo mismo el mismo número de veces —el caso normal— devuelven un ejemplo
    -- cualquiera de las dos. SQLite desempata por el máximo del propio valor.
    argMax(dedup_hash, (n, dedup_hash)) AS hash_ejemplo,
    max(ultima)                      AS ultima,
    uniqExact(trace_id)              AS trazas,
    sum(n)                           AS total_spans,
    sum(n - 1)                       AS extra_spans,
    sum(coste - coste_primera)       AS extra_coste,
    sum(duracion - duracion_primera) AS extra_duracion,
    sum(tok_in - tok_in_primera)     AS extra_tok_in,
    sum(tok_out - tok_out_primera)   AS extra_tok_out,
    sum(sin_tarifa - sin_tarifa_primera) AS extra_sin_tarifa,
    sum(asumida - asumida_primera)       AS extra_asumida,
    max(n)                           AS max_por_traza,
    argMax(trace_id, (n, trace_id))  AS traza_ejemplo
FROM (
    SELECT
        trace_id,
        dedup_hash,
        max(if(step_label != '', step_label, name)) AS etiqueta,
        max(step_hint)                     AS pista,
        max(step_site)                     AS sitio,
        max(span_type)                     AS tipo,
        max(request_model)                 AS modelo,
        max(if(step_key != '', step_key, name)) AS paso,
        max(start_time)                    AS ultima,
        count()                            AS n,
        sum(cost_total_usd)                AS coste,
        argMin(cost_total_usd, (start_time, span_id)) AS coste_primera,
        sum(duration_ms)                   AS duracion,
        argMin(duration_ms, (start_time, span_id))    AS duracion_primera,
        sum(input_tokens)                  AS tok_in,
        argMin(input_tokens, (start_time, span_id))   AS tok_in_primera,
        sum(output_tokens)                 AS tok_out,
        argMin(output_tokens, (start_time, span_id))  AS tok_out_primera,
        -- Sólo de las ocurrencias sobrantes: son las únicas cuyo dinero reclamamos.
        sum(cost_unknown)                        AS sin_tarifa,
        argMin(cost_unknown, (start_time, span_id))         AS sin_tarifa_primera,
        sum(cost_rate_assumed)                   AS asumida,
        argMin(cost_rate_assumed, (start_time, span_id))    AS asumida_primera
    FROM spans FINAL
    WHERE {RULES_WHERE} AND dedup_hash != ''
      -- Primero, qué parejas (traza, entrada) se repiten, contando sin más. Sin este
      -- filtro, la agregación de abajo —una docena de estados por pareja, con FINAL—
      -- se hacía para todas, y casi ninguna se repite: con 5 millones de spans pedía más
      -- de 2 GB y no terminaba en cinco minutos (D-142). Va sin FINAL porque sólo acota:
      -- un span reenviado puede colar una pareja de más, y el HAVING de abajo, que sí
      -- cuenta sobre FINAL, la descarta. El hash de 64 bits agrupa mucho más barato que
      -- dos cadenas, y una colisión también es sólo una candidata de más.
      AND sipHash64(trace_id, dedup_hash) IN (
          SELECT sipHash64(trace_id, dedup_hash) FROM spans
          WHERE {RULES_WHERE} AND dedup_hash != ''
          GROUP BY sipHash64(trace_id, dedup_hash)
          HAVING count() >= %(min_repeats)s
      )
    GROUP BY trace_id, dedup_hash
    HAVING n >= %(min_repeats)s
)
-- Por paso Y modelo: un paso que se ejecuta con dos modelos son dos problemas, con
-- dinero distinto, y mezclarlos dejaría las repeticiones de uno sin descontar del otro.
GROUP BY paso, modelo
ORDER BY extra_coste DESC, extra_spans DESC, paso, modelo
LIMIT %(limit)s
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
SELECT
    intDiv(dateDiff('second', toDateTime64(%(origen)s, 3), inicio), %(ancho_s)s) AS tramo,
    count()          AS trazas,
    sum(pasos)       AS pasos,
    sum(llamadas)    AS llamadas,
    sum(fallo)       AS trazas_con_error,
    sum(coste)       AS coste,
    sum(tok_in)      AS tok_in,
    sum(tok_out)     AS tok_out,
    sum(dateDiff('millisecond', inicio, fin)) AS duracion
FROM (
    SELECT
        trace_id,
        min(start_time)          AS inicio,
        max(end_time)            AS fin,
        count()                  AS pasos,
        countIf(span_type = 'llm') AS llamadas,
        sum(cost_total_usd)      AS coste,
        sum(input_tokens)        AS tok_in,
        sum(output_tokens)       AS tok_out,
        max(status = 'error')    AS fallo
    FROM spans FINAL
    WHERE {WINDOW_WHERE}
    GROUP BY trace_id
)
GROUP BY tramo
ORDER BY tramo
"""

#: Bucles: gemelo de `LOOP_GROUPS_SQL` de sqlite.py (D-109). Mismo criterio, mismos
#: alias: el contrato entre los dos almacenes son los nombres de las columnas (D-066).
LOOP_GROUPS_SQL = f"""
-- Una vuelta se reconoce por su entrada sin números (`loop_hash`), DENTRO de una
-- ejecución. El hallazgo, en cambio, es del PASO: agrupar también fuera por el hash
-- abría una tarjeta por cada pregunta de usuario distinta, y con tráfico real no hay
-- dos iguales (D-135). El hash que se devuelve es sólo un ejemplo para la ficha.
SELECT
    max(loop_hash)                   AS hash_ejemplo,
    max(ultima)                      AS ultima,
    max(etiqueta)                    AS nombre,
    max(pista)                       AS pista,
    max(sitio)                       AS sitio,
    max(tipo)                        AS tipo,
    max(modelo)                      AS modelo,
    paso_clave                       AS paso,
    uniqExact(trace_id)              AS trazas,
    sum(n)                           AS total_spans,
    sum(n - 1)                       AS extra_spans,
    max(n)                           AS max_por_traza,
    max(entradas)                    AS distintas_entradas,
    max(salidas)                     AS distintas_salidas,
    sum(coste - coste_primera)       AS extra_coste,
    sum(duracion - duracion_primera) AS extra_duracion,
    sum(tok_in - tok_in_primera)     AS extra_tok_in,
    sum(tok_out - tok_out_primera)   AS extra_tok_out,
    sum(sin_tarifa)                  AS extra_sin_tarifa,
    max(trace_id)                    AS traza_ejemplo
FROM (
    SELECT
        trace_id,
        loop_hash,
        max(if(step_label != '', step_label, name)) AS etiqueta,
        max(step_hint)                     AS pista,
        max(step_site)                     AS sitio,
        max(span_type)                     AS tipo,
        max(request_model)                 AS modelo,
        max(if(step_key != '', step_key, name)) AS paso_clave,
        max(start_time)                    AS ultima,
        count()                            AS n,
        uniqExact(dedup_hash)              AS entradas,
        uniqExact(loop_out_hash)           AS salidas,
        sum(cost_total_usd)                AS coste,
        min(cost_total_usd)                AS coste_primera,
        sum(duration_ms)                   AS duracion,
        min(duration_ms)                   AS duracion_primera,
        sum(input_tokens)                  AS tok_in,
        min(input_tokens)                  AS tok_in_primera,
        sum(output_tokens)                 AS tok_out,
        min(output_tokens)                 AS tok_out_primera,
        sum(cost_unknown)                  AS sin_tarifa
    FROM spans FINAL
    WHERE {RULES_WHERE} AND loop_hash != ''
      -- El mismo atajo que en las repeticiones: primero las parejas con vueltas de
      -- sobra, sin FINAL y por hash; el HAVING de abajo decide con los datos exactos.
      AND sipHash64(trace_id, loop_hash) IN (
          SELECT sipHash64(trace_id, loop_hash) FROM spans
          WHERE {RULES_WHERE} AND loop_hash != ''
          GROUP BY sipHash64(trace_id, loop_hash)
          HAVING count() >= %(min_vueltas)s
      )
    GROUP BY trace_id, loop_hash
    HAVING n >= %(min_vueltas)s AND entradas > 1 AND salidas <= %(max_salidas)s
)
GROUP BY paso_clave
ORDER BY extra_spans DESC, paso_clave
LIMIT %(limit)s
"""

#: Uso por (paso, modelo): base de las reglas de modelo caro y de contexto fijo.
MODEL_USAGE_SQL = f"""
SELECT
    -- Las filas anteriores a la identidad de paso no la tienen. Caer al nombre del
    -- span deja esas trazas exactamente como estaban, en lugar de juntarlas todas
    -- bajo una clave vacía y dejar sin pareja al descuento que evita contar dos veces
    -- el mismo ahorro (D-061).
    -- El alias NO puede llamarse `step_key`: taparía la columna y ClickHouse dejaría
    -- de encontrarla en el GROUP BY. Ya pasó una vez con `any(project_id) AS project_id`.
    if(step_key != '', step_key, name) AS paso_clave,
    max(if(step_label != '', step_label, name)) AS paso,
    max(step_hint)           AS pista,
    max(step_site)           AS sitio,
    max(prompt_name)         AS prompt,
    max(prompt_version)      AS version_prompt,
    request_model,
    count()                  AS llamadas,
    uniqExact(trace_id)      AS trazas,
    sum(input_tokens)        AS in_tok,
    sum(output_tokens)       AS out_tok,
    sum(cached_input_tokens) AS cache_tok,
    sum(cache_write_tokens + cache_write_1h_tokens) AS cache_escrito,
    sum(cost_cache_saving_usd) AS ahorro_cache,
    sum(cost_total_usd)      AS coste,
    countIf(cost_unknown = 1)      AS sin_tarifa,
    countIf(cost_rate_assumed = 1) AS tarifa_asumida,
    avg(output_tokens)       AS media_salida,
    avg(input_tokens)        AS media_entrada,
    -- Sólo de las llamadas que respondieron: gemelo del de sqlite.py (D-108).
    minIf(input_tokens, status != 'error' AND input_tokens > 0) AS min_entrada,
    sum(duration_ms)         AS duracion,
    -- La MEDIANA por llamada, que es lo que se compara entre modelos: una media se la
    -- lleva por delante un solo span anómalo (D-108). Las fallidas no cuentan: no
    -- tardan lo que tarda el modelo, tardan lo que tarda un error.
    quantileExactIf(0.5)(duration_ms, status != 'error')    AS mediana,
    quantileExactIf(0.5)(output_tokens, status != 'error') AS mediana_salida,
    -- `min` y no `any`: es lo que hace SQLite, y una traza de ejemplo que cambia entre
    -- almacenes manda a dos personas a mirar ejecuciones distintas del mismo hallazgo.
    max(start_time)          AS ultima,
    min(trace_id)            AS traza_ejemplo
FROM spans FINAL
WHERE {RULES_WHERE} AND span_type = 'llm' AND request_model != ''
GROUP BY if(step_key != '', step_key, name), request_model
HAVING llamadas >= %(min_calls)s
ORDER BY coste DESC, paso_clave, request_model
LIMIT %(limit)s
"""


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
        preagregados.marcar_todo_si_nuevo(self._client)
        # `CREATE TABLE IF NOT EXISTS` no cambia la clave de una tabla que ya existe, y
        # migrarla es una copia entera: no se hace sola al arrancar, se avisa (D-168).
        from .migrar_orden import necesita

        if necesita(self._client):
            logger.warning(
                "la tabla spans tiene una clave de ordenación de antes de D-177 y cada "
                "consulta lee más de lo que necesita. Para migrarla: "
                "python -m laplace_backend.storage.migrar_orden"
            )

    # -- escritura -----------------------------------------------------------------

    def insert_spans(self, spans: list[Span]) -> int:
        if not spans:
            return 0
        self._client.insert(
            "spans",
            [span_to_row(span) for span in spans],
            column_names=list(COLUMNS),
            # El servidor junta los lotes pequeños antes de escribir: sin esto, cada
            # petición de la ingesta creaba una parte nueva y las fusiones no daban
            # abasto con volumen. Esperar la confirmación mantiene lo que promete el
            # 200: que está guardado y el exportador no tiene que reintentar (D-142).
            settings={"async_insert": 1, "wait_for_async_insert": 1},
        )
        # Después de escribir: la marca tiene que ser posterior a lo que ensucia (D-177).
        preagregados.marcar(self._client, {(s.project_id, s.start_time) for s in spans})
        return len(spans)

    def recalcular_preagregados(
        self, limite: int = preagregados.POR_VUELTA, quieta_s: int = 120
    ) -> int:
        """Recalcula las horas con escrituras posteriores a su último cálculo (D-177)."""
        return preagregados.recalcular_pendientes(self._client, limite, quieta_s)

    @property
    def _pre(self) -> bool:
        return bool(getattr(self._settings, "preagregados", False))

    def _en_pre(self, project_id: str, window: Window) -> preagregados.Ventana:
        return preagregados.ventana(self._client, project_id, window.since, window.until)

    # -- lectura -------------------------------------------------------------------

    def _consulta(self, sql: str, parameters: dict[str, Any] | None = None) -> Any:
        """Una lectura de las reglas, con las tiradas de evaluación ya resueltas (D-177).

        `RULES_WHERE` deja fuera las trazas de evaluación con `trace_id NOT IN
        (subconsulta)`, y ClickHouse comprueba así cada `trace_id` de la ventana contra
        el conjunto aunque esté vacío, que es lo normal: con cinco millones de spans eran
        0,2–0,35 s por consulta, en las cinco de las reglas. Se buscan antes, en una
        lectura que no toca más que `tags`, y se pasan como lista; sin ninguna, el filtro
        es el de la ventana a secas. La consulta que enseña la ficha no cambia: es la
        misma cuenta.
        """
        params = dict(parameters or {})
        if RULES_WHERE in sql:
            evaluaciones = [
                fila[0]
                for fila in self._client.query(
                    f"SELECT DISTINCT trace_id FROM spans WHERE {WINDOW_WHERE} "
                    f"AND has(tags, '{EVAL_TAG}') LIMIT {_MAX_EVALUACIONES + 1}",
                    parameters=params,
                ).result_rows
            ]
            if not evaluaciones:
                sql = sql.replace(RULES_WHERE, WINDOW_WHERE)
            elif len(evaluaciones) <= _MAX_EVALUACIONES:
                params["evaluaciones"] = evaluaciones
                sql = sql.replace(
                    RULES_WHERE, f"{WINDOW_WHERE} AND trace_id NOT IN %(evaluaciones)s"
                )
        return self._client.query(sql, parameters=params or None)

    def list_traces(self, filters: TraceFilter) -> TracePage:
        where, params = self._where(filters)
        params["limit"] = max(1, min(filters.limit, 200))

        # El cursor sólo se aplica al orden temporal: ordenar por coste sobre datos que
        # siguen entrando no da una secuencia estable que paginar.
        orden = {
            "cost": "cost_total_usd DESC, trace_id DESC",
            "duration": "duration_ms DESC, trace_id DESC",
        }.get(filters.sort, "started DESC, trace_id DESC")
        por_tiempo = filters.sort not in ("cost", "duration")

        condiciones_having: list[str] = []
        if filters.before is not None and por_tiempo:
            params["before"] = _utc(filters.before)
            params["before_id"] = filters.before_trace_id or ""
            # Comparación por tupla: avanza aunque varias trazas compartan instante.
            condiciones_having.append("(started, trace_id) < (%(before)s, %(before_id)s)")
        if filters.min_cost_usd is not None:
            # El coste es una suma sobre los spans: sólo se puede filtrar tras agregar.
            condiciones_having.append("cost_total_usd >= %(min_cost)s")
            params["min_cost"] = float(filters.min_cost_usd)
        having = ("HAVING " + " AND ".join(condiciones_having)) if condiciones_having else ""

        # Los alias de las agregaciones no pueden llamarse igual que la columna que
        # agregan: ClickHouse resuelve el nombre del WHERE contra el alias y falla con
        # ILLEGAL_AGGREGATION en cuanto se filtra por proyecto, sesión o usuario.
        sql = f"""
            SELECT
                trace_id,
                max(project_id)                                        AS trace_project_id,
                if(maxIf(name, parent_span_id = '') != '',
                   maxIf(name, parent_span_id = ''),
                   argMin(name, (start_time, span_id)))                AS root_name,
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
                sum(cache_write_tokens)                                AS cache_write_tokens,
                sum(cache_write_1h_tokens)                             AS cache_write_1h_tokens,
                sum(reasoning_tokens)                                  AS reasoning_tokens,
                sum(cost_input_usd)                                    AS cost_input_usd,
                sum(cost_output_usd)                                   AS cost_output_usd,
                sum(cost_total_usd)                                    AS cost_total_usd,
                sum(cost_cache_saving_usd)                             AS cache_saving_usd,
                countIf(cost_unknown = 1)                              AS unknown_cost_spans,
                countIf(cost_rate_assumed = 1)                         AS assumed_rate_spans,
                max(session_id)                                        AS trace_session_id,
                max(user_id)                                           AS trace_user_id,
                substring(maxIf(input_payload, parent_span_id = ''), 1, 600) AS root_input,
                arrayDistinct(groupArrayIf(request_model, request_model != '')) AS modelos
            FROM spans FINAL
            {where}
            GROUP BY trace_id
            {having}
            ORDER BY {orden}
            LIMIT %(limit)s
        """
        traces = [row_to_summary(r) for r in _named(self._consulta(sql, parameters=params))]
        # Sólo hay siguiente página si ésta vino llena y el orden es paginable.
        full_page = len(traces) == params["limit"]
        next_cursor = encode_cursor(traces[-1]) if full_page and por_tiempo else None
        return TracePage(traces=traces, next_cursor=next_cursor)

    def _where(self, filters: TraceFilter) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        # Las subconsultas de ids van acotadas al proyecto igual que la de fuera. La clave
        # de ordenación empieza por `project_id`: sin él, cada filtro (estado, sesión,
        # usuario, modelo, búsqueda) leía la tabla entera de la instalación, y `status=ok`
        # hacía un NOT IN contra las trazas fallidas de todos los clientes.
        acotar = ""
        if filters.project_id:
            clauses.append("project_id = %(project_id)s")
            params["project_id"] = filters.project_id
            acotar = " AND project_id = %(project_id)s"
        if filters.since is not None:
            clauses.append("start_time >= %(since)s")
            params["since"] = _utc(filters.since)
        if filters.until is not None:
            clauses.append("start_time <= %(until)s")
            params["until"] = _utc(filters.until)
        # Y a la ventana, con margen (D-168): con la clave por día, es lo que evita que
        # cada filtro recorra todo el histórico del proyecto.
        if filters.since is not None:
            acotar += " AND start_time >= %(sub_since)s"
            params["sub_since"] = _utc(filters.since - MARGEN_FILTROS)
        if filters.until is not None:
            acotar += " AND start_time <= %(sub_until)s"
            params["sub_until"] = _utc(filters.until + MARGEN_FILTROS)
        # Sesión y usuario los lleva el span raíz: se filtran trazas, no spans (D-123).
        if filters.session_id:
            clauses.append(
                "trace_id IN (SELECT trace_id FROM spans "
                f"WHERE session_id = %(session_id)s{acotar})"
            )
            params["session_id"] = filters.session_id
        if filters.model:
            # El modelo es de un span, no de la traza: se filtra por trazas que lo usan.
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans"
                f" WHERE request_model = %(model)s{acotar})"
            )
            params["model"] = filters.model
        if filters.user_id:
            clauses.append(
                f"trace_id IN (SELECT trace_id FROM spans WHERE user_id = %(user_id)s{acotar})"
            )
            params["user_id"] = filters.user_id
        if filters.customer_id:
            clauses.append(
                "trace_id IN (SELECT trace_id FROM spans "
                f"WHERE customer_id = %(customer_id)s{acotar})"
            )
            params["customer_id"] = filters.customer_id

        # El estado es una propiedad de la traza entera, no de un span: "ok" significa
        # que ninguno de sus spans falló, así que va por exclusión.
        failed = f"SELECT DISTINCT trace_id FROM spans WHERE status = 'error'{acotar}"
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
        if filters.step_key:
            sub.append("if(step_key != '', step_key, name) = %(paso)s")
            params["paso"] = filters.step_key
        if filters.search:
            sub.append(
                "(positionCaseInsensitive(name, %(search)s) > 0"
                " OR startsWith(trace_id, %(search)s))"
            )
            params["search"] = filters.search
        if sub:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE "
                + " AND ".join(sub)
                + acotar
                + ")"
            )

        # El contenido es casi todo el disco: la subconsulta va acotada también a la
        # ventana, y se escribe con la misma expresión que `idx_contenido` para que
        # ClickHouse pueda saltarse los gránulos que no lo tienen (D-144).
        if filters.content:
            ventana = ""
            if filters.since is not None:
                ventana += " AND start_time >= %(since)s"
            if filters.until is not None:
                ventana += " AND start_time <= %(until)s"
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE "
                f"{CONTENIDO} LIKE %(contenido)s{acotar}{ventana})"
            )
            params["contenido"] = f"%{literal_like(filters.content.lower())}%"

        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

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
            ORDER BY start_time, span_id
        """
        return [row_to_span(r) for r in _named(self._consulta(sql, parameters=params))]

    def list_projects(self) -> list[ProjectStats]:
        sql = """
            SELECT project_id,
                   uniqExact(trace_id) AS traces,
                   count()             AS spans,
                   sum(cost_total_usd) AS cost,
                   max(start_time)     AS last_seen
            FROM spans FINAL
            GROUP BY project_id
            ORDER BY last_seen DESC, project_id
        """
        return [
            ProjectStats(
                project_id=row[0],
                trace_count=int(row[1]),
                span_count=int(row[2]),
                total_cost_usd=float(row[3]),
                last_seen=_utc(row[4]) if row[4] else None,
            )
            for row in self._consulta(sql).result_rows
        ]

    # -- analítica para el motor de detección (Fase 2) -------------------------------

    def _window_params(self, project_id: str, window: Window) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "since": _utc(window.since),
            "until": _utc(window.until),
        }


    def summarize_window(self, project_id: str, window: Window) -> WindowSummary:
        p95_sql = ""
        sql = f"""
            SELECT
                uniqExact(trace_id)                                   AS traces,
                count()                                               AS spans,
                uniqExactIf(trace_id, status = 'error')               AS error_traces,
                countIf(span_type = 'llm')                            AS llm_calls,
                countIf(span_type = 'tool')                           AS tool_calls,
                sum(input_tokens)                                     AS input_tokens,
                sum(output_tokens)                                    AS output_tokens,
                sum(cost_total_usd)                                   AS cost,
                sum(cost_cache_saving_usd)                            AS ahorro_cache,
                countIf(cost_unknown = 1)                             AS sin_tarifa,
                countIf(cost_rate_assumed = 1)                        AS tarifa_asumida,
                arrayDistinct(groupArrayIf(request_model, cost_unknown = 1 AND request_model != ''))
                                                                      AS modelos_sin_tarifa,
                min(start_time)                                       AS primero,
                max(start_time)                                       AS ultimo
            FROM spans FINAL
            WHERE {WINDOW_WHERE}
        """
        params = self._window_params(project_id, window)
        if self._pre:
            v = self._en_pre(project_id, window)
            sql, params = preagregados.sql(v, preagregados.RESUMEN), v.params
            p95_sql = preagregados.sql(v, preagregados.P95)
        row = self._consulta(sql, parameters=params).result_rows
        if not row:
            return WindowSummary()

        # La latencia que importa es la de la traza entera, no la de un span suelto:
        # es la que espera el usuario final del agente.
        p95_sql = p95_sql if self._pre else f"""
            SELECT quantile(0.95)(duracion) FROM (
                SELECT dateDiff('millisecond', min(start_time), max(end_time)) AS duracion
                FROM spans FINAL
                WHERE {WINDOW_WHERE}
                GROUP BY trace_id
            )
        """
        p95 = self._consulta(p95_sql, parameters=params).result_rows

        (traces, spans, error_traces, llm_calls, tool_calls, in_tok, out_tok, cost,
         ahorro_cache, sin_tarifa, tarifa_asumida, modelos_sin_tarifa,
         primero, ultimo) = row[0]
        return WindowSummary(
            traces=int(traces),
            spans=int(spans),
            error_traces=int(error_traces),
            llm_calls=int(llm_calls),
            tool_calls=int(tool_calls),
            input_tokens=int(in_tok),
            output_tokens=int(out_tok),
            total_cost_usd=float(cost),
            p95_duration_ms=float(p95[0][0]) if p95 and p95[0][0] is not None else 0.0,
            cache_saving_usd=float(ahorro_cache),
            unknown_cost_spans=int(sin_tarifa),
            assumed_rate_spans=int(tarifa_asumida),
            models_without_price=sorted(modelos_sin_tarifa or []),
            first_seen=_utc(primero) if primero else None,
            last_seen=_utc(ultimo) if ultimo else None,
        )

    @property
    def repeated_groups_sql(self) -> str:
        return REPEATED_GROUPS_SQL

    @property
    def loop_groups_sql(self) -> str:
        return LOOP_GROUPS_SQL

    @property
    def model_usage_sql(self) -> str:
        return MODEL_USAGE_SQL

    def repeated_groups(
        self, project_id: str, window: Window, *, min_repeats: int = 3, limit: int = 20
    ) -> list[RepeatedGroup]:
        """Agrupa primero por (traza, hash) y luego por hash.

        Los dos niveles importan: repetir tres veces dentro de una traza es un bucle;
        aparecer tres veces en tres trazas distintas es uso normal.
        """
        params = self._window_params(project_id, window)
        sql = REPEATED_GROUPS_SQL
        if self._pre:
            v = self._en_pre(project_id, window)
            sql, params = preagregados.sql(v, preagregados.REPETICIONES), v.params
        params["min_repeats"] = min_repeats
        params["limit"] = limit
        # Por nombre de columna, no por posición: añadir una columna en medio de la
        # consulta y desplazar el resto en silencio es un error demasiado barato de
        # cometer para una función que decide cuánto dinero se le promete a alguien.
        grupos = [
            RepeatedGroup(
                dedup_hash=r["hash_ejemplo"],
                name=r["nombre"],
                hint=r["pista"],
                span_type=r["tipo"],
                model=r["modelo"] or "",
                step_key=r["paso"] or "",
                site=r["sitio"] or "",
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
                last_seen=_utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in _named(self._consulta(sql, parameters=params))
        ]
        # Mismo título para dos pasos distintos es peor que no enseñarlos.
        return disambiguate(grupos)

    def loop_groups(
        self, project_id: str, window: Window, *, min_vueltas: int = 4,
        max_salidas: int = 2, limit: int = 20,
    ) -> list[LoopGroup]:
        """Gemelo del de sqlite.py: mismos umbrales, mismos alias (D-066, D-109)."""
        params = self._window_params(project_id, window)
        sql = LOOP_GROUPS_SQL
        if self._pre:
            v = self._en_pre(project_id, window)
            sql, params = preagregados.sql(v, preagregados.BUCLES), v.params
        params.update(min_vueltas=min_vueltas, max_salidas=max_salidas, limit=limit)
        grupos = [
            LoopGroup(
                loop_hash=r["hash_ejemplo"],
                name=r["nombre"],
                hint=r["pista"] or "",
                span_type=r["tipo"],
                model=r["modelo"] or "",
                step_key=r["paso"] or "",
                site=r["sitio"] or "",
                traces=int(r["trazas"]),
                total_spans=int(r["total_spans"]),
                extra_spans=int(r["extra_spans"]),
                max_per_trace=int(r["max_por_traza"]),
                distinct_inputs=int(r["distintas_entradas"]),
                distinct_outputs=int(r["distintas_salidas"]),
                extra_cost_usd=float(r["extra_coste"] or 0.0),
                extra_duration_ms=float(r["extra_duracion"] or 0.0),
                extra_input_tokens=int(r["extra_tok_in"] or 0),
                extra_output_tokens=int(r["extra_tok_out"] or 0),
                extra_unknown_cost_spans=int(r["extra_sin_tarifa"] or 0),
                sample_trace_id=r["traza_ejemplo"],
                last_seen=_utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in _named(self._consulta(sql, parameters=params))
        ]
        # El gemelo de sqlite: los bucles nunca pasaron por el desambiguado, así que dos
        # llamantes del mismo paso daban dos tarjetas con el título idéntico (D-115).
        return disambiguate(grupos)

    def model_usage(
        self, project_id: str, window: Window, *, min_calls: int = 5, limit: int = 50
    ) -> list[ModelUsage]:
        params = self._window_params(project_id, window)
        sql = MODEL_USAGE_SQL
        if self._pre:
            v = self._en_pre(project_id, window)
            sql, params = preagregados.sql(v, preagregados.USO), v.params
        params["min_calls"] = min_calls
        params["limit"] = limit
        usos = [
            ModelUsage(
                key=r["paso_clave"],
                name=r["paso"],
                hint=r["pista"],
                prompt_name=r["prompt"] or "",
                prompt_version=int(r["version_prompt"] or 0),
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
                # `None` cuando todas las llamadas del paso fallaron: no hay suelo
                # de entrada que afirmar, y cero diría otra cosa (D-108).
                min_input_tokens=int(r["min_entrada"] or 0),
                duration_ms=float(r["duracion"] or 0.0),
                p50_duration_ms=float(r["mediana"] or 0.0),
                p50_output_tokens=float(r["mediana_salida"] or 0.0),
                sample_trace_id=r["traza_ejemplo"],
                last_seen=_utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in _named(self._consulta(sql, parameters=params))
        ]
        return disambiguate(usos)

    def traces_with_repeats(
        self, project_id: str | None, trace_ids: list[str], *, min_repeats: int = 3
    ) -> set[str]:
        """Marca de bucle para la lista.

        Se resuelve en una segunda consulta sobre las trazas ya seleccionadas (unas
        decenas) en lugar de complicar la agregación de la lista con dos niveles.
        """
        if not trace_ids:
            return set()
        params: dict[str, Any] = {"trace_ids": trace_ids, "min_repeats": min_repeats}
        where = "trace_id IN %(trace_ids)s AND dedup_hash != ''"
        if project_id:
            where += " AND project_id = %(project_id)s"
            params["project_id"] = project_id

        sql = f"""
            SELECT DISTINCT trace_id FROM (
                SELECT trace_id, dedup_hash, count() AS n
                FROM spans FINAL
                WHERE {where}
                GROUP BY trace_id, dedup_hash
                HAVING n >= %(min_repeats)s
            )
        """
        return {row[0] for row in self._consulta(sql, parameters=params).result_rows}

    def sample_repetition(
        self, project_id: str, window: Window, dedup_hash: str, limit: int = 40
    ) -> list[Span]:
        """La traza donde más se repite ese paso, con sus ocurrencias."""
        params = self._window_params(project_id, window)
        params["dedup_hash"] = dedup_hash
        params["limit"] = limit

        trace_sql = f"""
            SELECT trace_id, count() AS n
            FROM spans FINAL
            WHERE {WINDOW_WHERE} AND dedup_hash = %(dedup_hash)s
            GROUP BY trace_id
            ORDER BY n DESC, trace_id
            LIMIT 1
        """
        rows = self._consulta(trace_sql, parameters=params).result_rows
        if not rows:
            return []
        params["trace_id"] = rows[0][0]

        columns = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columns} FROM (
                SELECT {columns}
                FROM spans
                WHERE trace_id = %(trace_id)s AND dedup_hash = %(dedup_hash)s
                ORDER BY span_id, ingested_at DESC
                LIMIT 1 BY span_id
            )
            ORDER BY start_time, span_id
            LIMIT %(limit)s
        """
        return [row_to_span(r) for r in _named(self._consulta(sql, parameters=params))]

    def sample_loop(
        self, project_id: str, window: Window, loop_hash: str, limit: int = 40
    ) -> list[Span]:
        """La traza donde más vueltas da ese bucle, con sus vueltas."""
        params = self._window_params(project_id, window)
        params["loop_hash"] = loop_hash
        params["limit"] = limit

        trace_sql = f"""
            SELECT trace_id, count() AS n
            FROM spans FINAL
            WHERE {WINDOW_WHERE} AND loop_hash = %(loop_hash)s
            GROUP BY trace_id
            ORDER BY n DESC, trace_id
            LIMIT 1
        """
        rows = self._consulta(trace_sql, parameters=params).result_rows
        if not rows:
            return []
        params["trace_id"] = rows[0][0]

        columns = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columns} FROM (
                SELECT {columns}
                FROM spans
                WHERE trace_id = %(trace_id)s AND loop_hash = %(loop_hash)s
                ORDER BY span_id, ingested_at DESC
                LIMIT 1 BY span_id
            )
            ORDER BY start_time, span_id
            LIMIT %(limit)s
        """
        return [row_to_span(r) for r in _named(self._consulta(sql, parameters=params))]

    # -- panel (Fase 4) ---------------------------------------------------------------

    def timeseries(
        self, project_id: str, window: Window, bucket_minutes: int
    ) -> list[Bucket]:
        params = self._window_params(project_id, window)
        params["origen"] = _utc(window.since)
        params["ancho_s"] = bucket_minutes * 60
        filas = _named(self._consulta(TIMESERIES_SQL, parameters=params))
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

    def step_cost_series(
        self, project_id: str, window: Window, bucket_minutes: int
    ) -> StepCostSeries:
        params = self._window_params(project_id, window)
        params["origen"] = _utc(window.since)
        params["ancho_s"] = bucket_minutes * 60
        tramo = "intDiv(dateDiff('second', toDateTime64(%(origen)s, 3), start_time), %(ancho_s)s)"
        if self._pre and preagregados.alineada(window.since):
            # Con la ventana en minutos enteros cada minuto cae entero en su tramo.
            v = self._en_pre(project_id, window)
            params = {**v.params, "origen": params["origen"], "ancho_s": params["ancho_s"]}
            por_minuto = tramo.replace("start_time", "minuto")
            total_sql = preagregados.sql(v, preagregados.SERIE_TOTAL, tramo=por_minuto)
            pasos_sql = preagregados.sql(v, preagregados.SERIE_PASOS, tramo=por_minuto)
        else:
            total_sql = f"""SELECT {tramo} AS tramo, sum(cost_total_usd) AS coste
                    FROM spans FINAL WHERE {WINDOW_WHERE} GROUP BY tramo"""
            pasos_sql = f"""SELECT if(step_key != '', step_key, name) AS paso,
                           max(if(step_label != '', step_label, name)) AS etiqueta,
                           max(step_site) AS sitio, max(step_hint) AS pista,
                           {tramo} AS tramo, sum(cost_total_usd) AS coste
                    FROM spans FINAL WHERE {RULES_WHERE} AND cost_total_usd > 0
                    GROUP BY paso, tramo"""
        total = _named(self._consulta(total_sql, parameters=params))
        pasos = _named(self._consulta(pasos_sql, parameters=params))
        return densify_steps(
            [(int(r["tramo"]), float(r["coste"] or 0.0)) for r in total],
            [
                (r["paso"], r["etiqueta"] or "", r["sitio"] or "", r["pista"] or "",
                 int(r["tramo"]), float(r["coste"] or 0.0))
                for r in pasos
            ],
            window,
            bucket_minutes,
        )

    def trace_latency(self, project_id: str, window: Window) -> Latency:
        # Rango más cercano sobre el array ordenado, el mismo índice que
        # `nearest_rank` en Python: las dos cifras son de ejecuciones que existieron.
        # La duración es la de `TIMESERIES_SQL`, de principio a fin de la traza.
        sql = f"""
            SELECT count() AS n, arraySort(groupArray(d)) AS o,
                   o[toUInt64(greatest(1, ceil(0.5 * n)))]  AS p50,
                   o[toUInt64(greatest(1, ceil(0.95 * n)))] AS p95
            FROM (
                SELECT trace_id,
                       dateDiff('millisecond', min(start_time), max(end_time)) AS d
                FROM spans FINAL
                WHERE {WINDOW_WHERE}
                GROUP BY trace_id
            )
        """
        r = _named(
            self._consulta(sql, parameters=self._window_params(project_id, window))
        )[0]
        n = int(r["n"])
        if not n:
            return Latency()
        return Latency(traces=n, p50_ms=float(r["p50"]), p95_ms=float(r["p95"]))

    def window_facts(self, project_id: str, since: Any, until: Any) -> WindowFacts:
        params = {"project_id": project_id, "since": _utc(since), "until": _utc(until)}
        cabecera = self._consulta(
            f"""SELECT uniqExact(trace_id), sum(cost_total_usd)
                FROM spans FINAL WHERE {WINDOW_WHERE}""",
            parameters=params,
        ).result_rows
        modelos = self._consulta(
            f"""SELECT DISTINCT request_model FROM spans FINAL
                WHERE {WINDOW_WHERE} AND request_model != ''""",
            parameters=params,
        ).result_rows
        herramientas = self._consulta(
            f"""SELECT DISTINCT if(tool_name != '', tool_name, name) FROM spans FINAL
                WHERE {WINDOW_WHERE} AND span_type = 'tool'""",
            parameters=params,
        ).result_rows
        # Por sitio de llamada, no por nombre: gemelo del de sqlite.py (D-106).
        pasos = self._consulta(
            f"""SELECT multiIf(step_site != '', step_site, step_label != '', step_label,
                               name) AS paso,
                       max(if(step_label != '', step_label, name)) AS etiqueta,
                       sum(cost_total_usd), count()
                FROM spans FINAL WHERE {WINDOW_WHERE}
                GROUP BY paso""",
            parameters=params,
        ).result_rows
        prompts = self._consulta(
            f"""SELECT DISTINCT concat(prompt_name, '@', toString(prompt_version))
                FROM spans FINAL WHERE {WINDOW_WHERE} AND prompt_name != ''""",
            parameters=params,
        ).result_rows
        fila = cabecera[0] if cabecera else (0, 0.0)
        return WindowFacts(
            traces=int(fila[0] or 0),
            cost_usd=float(fila[1] or 0.0),
            models={r[0] for r in modelos if r[0]},
            tools={r[0] for r in herramientas if r[0]},
            steps={
                r[0]: StepFacts(
                    cost_usd=float(r[2] or 0.0), calls=int(r[3]), label=r[1] or r[0]
                )
                for r in pasos
                if r[0]
            },
            prompts={r[0] for r in prompts if r[0]},
        )

    def costs_for_traces(
        self, project_id: str, trace_ids: list[str]
    ) -> dict[str, TraceCost]:
        if not trace_ids:
            return {}
        params = {"project_id": project_id, "ids": list(trace_ids)}
        filas = _named(
            self._consulta(
                """
                SELECT
                    trace_id,
                    sum(cost_total_usd)   AS coste,
                    sum(input_tokens)     AS tok_in,
                    sum(output_tokens)    AS tok_out,
                    count()               AS pasos,
                    max(status = 'error') AS fallo,
                    countIf(cost_unknown = 1)      AS sin_tarifa,
                    countIf(cost_rate_assumed = 1) AS asumida,
                    dateDiff('millisecond', min(start_time), max(end_time)) AS duracion
                FROM spans FINAL
                WHERE project_id = %(project_id)s AND trace_id IN %(ids)s
                GROUP BY trace_id
                """,
                parameters=params,
            )
        )
        return {r["trace_id"]: row_to_trace_cost(r) for r in filas}

    # -- prompts (Fase 6) --------------------------------------------------------------

    def prompt_usage(
        self, project_id: str, window: Window, *, rules: bool = False
    ) -> list[PromptUsage]:
        """Coste y volumen por versión de prompt gestionado.

        `uniqExact` y no `count()` en el denominador: lo que se enseña es coste **por
        ejecución**, igual que en el panel, y un prompt llamado dos veces por ejecución
        no cuesta el doble por ejecución.
        """
        donde = RULES_WHERE if rules else WINDOW_WHERE
        pasos = "groupUniqArray(step_key)" if rules else "emptyArrayString()"
        ejemplo = "max((step_key, trace_id))" if rules else "('', '')"
        if self._pre:
            v = self._en_pre(project_id, window)
            sql = preagregados.sql(
                v, preagregados.PROMPTS, pasos=pasos,
                ejemplo="max((step_key, traza_max))" if rules else ejemplo,
                reglas="es_eval = 0 AND" if rules else "",
            )
            return [row_to_prompt_usage(f) for f in _named(self._consulta(sql, v.params))]
        filas = _named(
            self._consulta(
                f"""
                SELECT
                    prompt_name                    AS nombre,
                    prompt_version                 AS version,
                    uniqExact(trace_id)            AS trazas,
                    count()                        AS llamadas,
                    sum(cost_total_usd)            AS coste,
                    sum(input_tokens)              AS tok_in,
                    sum(output_tokens)             AS tok_out,
                    sum(duration_ms)               AS duracion,
                    countIf(cost_unknown = 1)      AS sin_tarifa,
                    countIf(cost_rate_assumed = 1) AS asumida,
                    min(start_time)                AS primero,
                    max(start_time)                AS ultimo,
                    {pasos}                        AS pasos,
                    {ejemplo}                      AS ejemplo
                FROM spans FINAL
                WHERE {donde} AND prompt_name != ''
                GROUP BY prompt_name, prompt_version
                ORDER BY nombre, version DESC
                """,
                parameters=self._window_params(project_id, window),
            )
        )
        return [row_to_prompt_usage(f) for f in filas]

    def prompt_versions_by_trace(
        self, project_id: str, trace_ids: list[str], *, sin_evaluaciones: bool = False
    ) -> dict[str, list[tuple[str, int]]]:
        if not trace_ids:
            return {}
        fuera = (
            "AND trace_id NOT IN (SELECT trace_id FROM spans WHERE project_id = "
            f"%(project_id)s AND has(tags, '{EVAL_TAG}'))"
            if sin_evaluaciones
            else ""
        )
        filas = _named(
            self._consulta(
                f"""
                SELECT DISTINCT trace_id, prompt_name AS nombre, prompt_version AS version
                FROM spans FINAL
                WHERE project_id = %(project_id)s AND trace_id IN %(ids)s
                  AND prompt_name != '' {fuera}
                """,
                parameters={"project_id": project_id, "ids": list(trace_ids)},
            )
        )
        salida: dict[str, list[tuple[str, int]]] = {}
        for f in filas:
            salida.setdefault(f["trace_id"], []).append((f["nombre"], int(f["version"])))
        return salida

    def observed_prompts(
        self, project_id: str, window: Window, *, rules: bool = False
    ) -> list[ObservedPrompt]:
        """Juegos de instrucciones vistos en las trazas, para quien no gestiona prompts.

        El alias del `GROUP BY` no puede llamarse `step_key`: taparía la columna, que es
        el mismo tropiezo que ya documenta `MODEL_USAGE_SQL`.
        """
        donde = RULES_WHERE if rules else WINDOW_WHERE
        filas = _named(
            self._consulta(
                f"""
                SELECT
                    step_key                                        AS clave,
                    -- `max` y no `any`: `any` escoge una fila cualquiera, así que dos
                    -- almacenes con los mismos spans pueden devolver representantes
                    -- distintos y la misma pantalla leerse distinto en local y en la
                    -- nube. Lo destapó el test de paridad de esta consulta.
                    max(if(step_label != '', step_label, name))     AS paso,
                    max(step_site)         AS sitio,
                    max(step_hint)         AS pista,
                    uniqExact(trace_id)    AS trazas,
                    count()                AS llamadas,
                    sum(cost_total_usd)    AS coste,
                    sum(input_tokens)      AS tok_in,
                    sum(output_tokens)     AS tok_out,
                    min(start_time)        AS primero,
                    max(start_time)        AS ultimo
                FROM spans FINAL
                WHERE {donde} AND span_type = 'llm' AND step_key != ''
                GROUP BY step_key
                ORDER BY coste DESC, clave
                """,
                parameters=self._window_params(project_id, window),
            )
        )
        return [row_to_observed_prompt(f) for f in filas]

    def co_occurring_step_keys(self, project_id: str, window: Window) -> set[str]:
        """Claves que comparten ejecución y camino con otra clave distinta."""
        filas = _named(
            self._consulta(
                f"""
                SELECT DISTINCT arrayJoin(claves) AS clave
                FROM (
                    SELECT groupUniqArray(step_key) AS claves
                    FROM spans FINAL
                    WHERE {WINDOW_WHERE} AND span_type = 'llm' AND step_key != ''
                    GROUP BY step_site, trace_id
                    HAVING length(claves) > 1
                )
                """,
                parameters=self._window_params(project_id, window),
            )
        )
        return {f["clave"] for f in filas}

    def coverage(self, project_id: str, window: Window) -> CoverageFacts:
        """La misma cuenta que en local, con los mismos alias (D-066).

        `countIf` en vez de `SUM(CASE WHEN …)`: dice lo mismo y es lo idiomático aquí.
        Lo que no puede cambiar son las condiciones ni los nombres de las columnas de
        salida, que son el contrato entre los dos almacenes.
        """
        params = self._window_params(project_id, window)
        if self._pre:
            v = self._en_pre(project_id, window)
            filas = _named(self._consulta(preagregados.sql(v, preagregados.COBERTURA), v.params))
            pasos = _named(
                self._consulta(preagregados.sql(v, preagregados.COBERTURA_PASOS), v.params)
            )
            return coverage_from_rows(filas[0], pasos)
        filas = _named(
            self._consulta(
                f"""
                SELECT
                    count() AS llamadas,
                    countIf(step_hint != '' OR step_label != name) AS identificadas,
                    countIf(cost_unknown = 0)                      AS con_tarifa,
                    countIf(usage_estimated = 0
                            AND (input_tokens > 0 OR output_tokens > 0)) AS con_tokens,
                    countIf(prompt_name != '')                     AS con_prompt
                FROM spans FINAL
                WHERE {WINDOW_WHERE} AND span_type = 'llm'
                """,
                parameters=params,
            )
        )
        pasos = _named(
            self._consulta(
                f"""
                -- Por sitio de llamada y no por nombre: ver el comentario gemelo en
                -- sqlite.py. Dos agentes con una función homónima se mezclaban (D-106).
                SELECT
                    multiIf(step_site != '', step_site, step_label != '', step_label, name) AS paso,
                    max(if(step_label != '', step_label, name)) AS etiqueta,
                    uniqExact(step_key)  AS identidades,
                    uniqExact(trace_id)  AS trazas
                FROM spans FINAL
                WHERE {WINDOW_WHERE} AND span_type = 'llm'
                GROUP BY paso
                """,
                parameters=params,
            )
        )
        return coverage_from_rows(filas[0], pasos)

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
        for tabla in (*preagregados.TABLAS, "pre_horas", "pre_sucias"):
            self._client.command(
                f"DELETE FROM {tabla} WHERE project_id = %(project_id)s",
                parameters={"project_id": project_id},
            )

    def delete_project_before(self, project_id: str, cutoff: datetime) -> None:
        # Con la clave por día (D-168) esto toca sólo las partes con días viejos.
        self._client.command(
            "DELETE FROM spans WHERE project_id = %(p)s AND start_time < %(c)s",
            parameters={"p": project_id, "c": _utc(cutoff)},
        )
        self._olvidar_preagregados("project_id = %(p)s AND ", {"p": project_id}, cutoff)
        preagregados.marcar(self._client, {(project_id, cutoff)})

    def delete_subject(
        self, project_id: str, *, user_id: str | None = None, customer_id: str | None = None
    ) -> int:
        columna, valor = _sujeto(user_id, customer_id)
        sub = (
            f"SELECT DISTINCT trace_id FROM spans WHERE project_id = %(p)s AND {columna} = %(v)s"
        )
        parametros = {"p": project_id, "v": valor}
        cuantas = int(
            self._consulta(f"SELECT count() FROM ({sub})", parameters=parametros)
            .result_rows[0][0]
        )
        if cuantas:
            horas = self._consulta(
                f"""SELECT DISTINCT toStartOfHour(start_time) FROM spans
                    WHERE project_id = %(p)s AND trace_id IN ({sub})""",
                parameters=parametros,
            ).result_rows
            self._client.command(
                f"DELETE FROM spans WHERE project_id = %(p)s AND trace_id IN ({sub})",
                parameters=parametros,
            )
            preagregados.marcar(self._client, {(project_id, h[0]) for h in horas})
        return cuantas

    def _olvidar_preagregados(self, filtro: str, params: dict[str, Any], cutoff: datetime) -> None:
        """Lo anterior a un corte: sus horas dejan de estar calculadas, y la del corte,
        que queda a medias, se recalcula."""
        corte = preagregados.hora_de(cutoff)
        self._client.command(
            f"DELETE FROM pre_horas WHERE {filtro}hora <= %(h)s", parameters={**params, "h": corte}
        )
        for tabla in preagregados.TABLAS:
            self._client.command(
                f"DELETE FROM {tabla} WHERE {filtro}minuto < %(h)s",
                parameters={**params, "h": corte},
            )

    # -- tarifas propias, reparto por usuario y retención (D-123) -------------------

    def unpriced_models(self, project_ids: list[str] | None, window: Window) -> list[str]:
        """Modelos con llamadas sin tarifa en la ventana, de esos proyectos (`None`: todos).

        Una consulta para todos, en vez de un `summarize_window` de 90 días por proyecto.
        Sin `FINAL`: un duplicado sin fusionar no cambia un `DISTINCT`.
        """
        if project_ids is not None and not project_ids:
            return []
        params: dict[str, Any] = {"since": _utc(window.since), "until": _utc(window.until)}
        donde = (
            "cost_unknown = 1 AND request_model != '' "
            "AND start_time >= %(since)s AND start_time <= %(until)s"
        )
        if project_ids is not None:
            donde += " AND project_id IN %(proyectos)s"
            params["proyectos"] = list(project_ids)
        filas = self._consulta(
            f"SELECT DISTINCT request_model FROM spans WHERE {donde}", parameters=params
        ).result_rows
        return sorted(f[0] for f in filas)

    def spans_by_model(self, model: str) -> list[Span]:
        """Todas las llamadas a un modelo, de todos los proyectos. Para el recálculo."""
        columns = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columns} FROM spans FINAL
            WHERE span_type = 'llm' AND (request_model = %(m)s OR response_model = %(m)s)
            ORDER BY start_time, span_id
        """
        return [
            row_to_span(r)
            for r in _named(self._consulta(sql, parameters={"m": model}))
        ]

    def customer_steps(self, project_id: str, window: Window) -> dict[str, dict[str, int]]:
        sql = f"""
            SELECT cliente, paso, max(n) AS veces FROM (
                SELECT c.cliente AS cliente,
                       if(s.step_key != '', s.step_key, s.name) AS paso,
                       count() AS n
                FROM (SELECT * FROM spans FINAL WHERE {WINDOW_WHERE}) AS s
                INNER JOIN (
                    SELECT trace_id, max(customer_id) AS cliente FROM spans FINAL
                    WHERE {RULES_WHERE} GROUP BY trace_id
                ) AS c ON s.trace_id = c.trace_id
                WHERE c.cliente != ''
                GROUP BY s.trace_id, cliente, paso
            )
            GROUP BY cliente, paso
        """
        salida: dict[str, dict[str, int]] = {}
        for r in _named(
            self._consulta(sql, parameters=self._window_params(project_id, window))
        ):
            salida.setdefault(r["cliente"], {})[r["paso"]] = int(r["veces"])
        return salida

    def cost_by(
        self,
        project_id: str,
        window: Window,
        dimension: str,
        limit: int = 20,
        *,
        rules: bool = False,
    ) -> list[CostGroup]:
        columna = {"user": "user_id", "session": "session_id", "customer": "customer_id"}[
            dimension
        ]
        params = {**self._window_params(project_id, window), "limit": limit}
        sql = f"""
            SELECT clave, count() AS trazas, sum(coste) AS coste, sum(tokens) AS tokens,
                   sum(sin_tarifa) AS sin_tarifa
            FROM (
                SELECT trace_id,
                       max({columna})                                    AS clave,
                       sum(cost_total_usd)                               AS coste,
                       sum(input_tokens + output_tokens)                 AS tokens,
                       countIf(span_type = 'llm' AND cost_unknown = 1)   AS sin_tarifa
                FROM spans FINAL
                WHERE {RULES_WHERE if rules else WINDOW_WHERE}
                GROUP BY trace_id
            )
            GROUP BY clave
            ORDER BY coste DESC, trazas DESC, clave
            LIMIT %(limit)s
        """
        return [
            CostGroup(
                key=r["clave"] or "",
                traces=int(r["trazas"] or 0),
                cost_usd=float(r["coste"] or 0.0),
                tokens=int(r["tokens"] or 0),
                unknown_cost_spans=int(r["sin_tarifa"] or 0),
            )
            for r in _named(self._consulta(sql, parameters=params))
        ]

    def apply_retention(self, days: int) -> None:
        """La retención como TTL de la tabla, que es como ClickHouse borra lo viejo.

        Antes era un `DELETE ... WHERE start_time < corte` una vez al día: un borrado
        ligero que marca filas en todas las particiones afectadas y deja el trabajo de
        verdad a las fusiones. El TTL lo hace ClickHouse en esas mismas fusiones, y
        suelta particiones enteras cuando caducan del todo. Con `days` 0 se quita: borrar
        datos no se enciende solo, y tampoco se queda encendido.
        """
        dias = int(days)
        # Los preagregados caducan con los spans de los que salen (D-177).
        columnas = {"spans": "toDateTime(start_time)", "pre_horas": "hora", "pre_sucias": "hora"}
        columnas.update({tabla: "minuto" for tabla in preagregados.TABLAS})
        for tabla, columna in columnas.items():
            if dias > 0:
                self._client.command(
                    f"ALTER TABLE {tabla} MODIFY TTL {columna} + INTERVAL {dias} DAY"
                )
                continue
            try:
                self._client.command(f"ALTER TABLE {tabla} REMOVE TTL")
            except Exception:  # noqa: BLE001 - no había TTL que quitar
                logger.debug("la tabla %s no tenía TTL", tabla, exc_info=True)

    def delete_before(self, cutoff: datetime) -> int:
        """Borra los spans que empezaron antes de `cutoff`. Es la retención."""
        self._client.command(
            "DELETE FROM spans WHERE start_time < %(c)s", parameters={"c": cutoff}
        )
        self._olvidar_preagregados("", {}, cutoff)
        return -1

    def health(self) -> bool:
        try:
            self._client.command("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("clickhouse no responde", exc_info=True)
            return False


def _named(result: Any) -> list[dict[str, Any]]:
    """Filas como diccionarios, con las etiquetas que puso la propia consulta."""
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _statements(sql: str) -> list[str]:
    """Parte un fichero .sql en sentencias, ignorando comentarios."""
    cleaned = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in cleaned.split(";") if s.strip()]
