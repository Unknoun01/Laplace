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
from laplace.semconv import EVAL_TAG

from ._rows import (
    COLUMNS,
    coverage_from_rows,
    row_to_observed_prompt,
    row_to_prompt_usage,
    row_to_span,
    row_to_summary,
    row_to_trace_cost,
    span_to_row,
    utc,
)
from .base import (
    _MOTIVOS_SQL,
    MARGEN_FILTROS,
    Bucket,
    CostGroup,
    CoverageFacts,
    GraphFacts,
    HistoryGroup,
    Latency,
    LoopGroup,
    ModelUsage,
    ObservedPrompt,
    ProjectStats,
    PromptUsage,
    RedoneGroup,
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
    graph_facts,
    nearest_rank,
)

logger = logging.getLogger("laplace.sqlite")

#: El texto en el que busca `TraceFilter.content`, el mismo que en ClickHouse (D-144).
#: `minusculas` es `str.lower`: el `lower()` de SQLite sólo sabe de ASCII y «ÁRBOL» no
#: casaría con «árbol», que en ClickHouse (`lowerUTF8`) sí casa.
CONTENIDO = (
    "minusculas(input_messages || output_messages || tool_arguments || tool_output || "
    "retrieval_query || retrieval_documents || input_payload || output_payload)"
)


def _minusculas(texto: str | None) -> str:
    return texto.lower() if texto else ""

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
    loop_hash             TEXT NOT NULL DEFAULT '',
    loop_out_hash         TEXT NOT NULL DEFAULT '',
    step_key              TEXT NOT NULL DEFAULT '',
    step_site             TEXT NOT NULL DEFAULT '',
    step_label            TEXT NOT NULL DEFAULT '',
    step_hint             TEXT NOT NULL DEFAULT '',

    -- Prompt gestionado que produjo la llamada, y su versión. Vacío para quien no
    -- haya adoptado la gestión de prompts, que es el caso por defecto (D-090).
    prompt_name           TEXT NOT NULL DEFAULT '',
    prompt_version        INTEGER NOT NULL DEFAULT 0,

    customer_id           TEXT NOT NULL DEFAULT '',
    prefix_hash           TEXT NOT NULL DEFAULT '',
    sample_rate           REAL NOT NULL DEFAULT 1,
    output_json           TEXT NOT NULL DEFAULT '',

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

#: Columnas añadidas después de que existieran bases en marcha. `CREATE TABLE IF NOT
#: EXISTS` no toca una tabla que ya está, así que un fichero de hace dos semanas se
#: quedaría sin ellas y la primera consulta fallaría con «no such column». ClickHouse
#: tiene su `ALTER TABLE ADD COLUMN IF NOT EXISTS` al final de su .sql; esto es lo
#: mismo para el modo local, que es el que le pasa a un usuario de verdad al actualizar.
COLUMNAS_TARDIAS = (
    ("step_site", "TEXT NOT NULL DEFAULT ''"),
    ("loop_hash", "TEXT NOT NULL DEFAULT ''"),
    ("loop_out_hash", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_name", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_version", "INTEGER NOT NULL DEFAULT 0"),
    # El cliente que paga, para el margen por cliente (D-161).
    ("customer_id", "TEXT NOT NULL DEFAULT ''"),
    # La huella del prefijo, para la caché compartida entre pasos (D-178).
    ("prefix_hash", "TEXT NOT NULL DEFAULT ''"),
    # A cuántas trazas representa, si el SDK muestrea (D-181).
    ("sample_rate", "REAL NOT NULL DEFAULT 1"),
    # Si la salida es JSON que se lee (D-194).
    ("output_json", "TEXT NOT NULL DEFAULT ''"),
)

#: Índices sobre columnas tardías. Van aquí y **no** en `SCHEMA` por un motivo que costó
#: un test: en una base creada antes que esas columnas, el `CREATE INDEX` del script se
#: ejecuta antes que el `ALTER TABLE` y falla con «no such column». Creándolos después
#: de añadir las columnas, el orden es correcto en los dos casos.
INDICES_TARDIOS = (
    "CREATE INDEX IF NOT EXISTS idx_spans_prompt "
    "ON spans (project_id, prompt_name, prompt_version)",
)

WINDOW_WHERE = "project_id = :project_id AND start_time >= :since AND start_time <= :until"

#: Las reglas no miran las tiradas de evaluación. Son gasto de verdad y se cuentan en el
#: gasto, pero un experimento lanzado a propósito —el modelo caro contra el barato— no
#: es un derroche que nadie haya visto, y el inicio lo enseñaba como tal (D-135). La
#: etiqueta la lleva la raíz, así que se excluye por traza.
RULES_WHERE = (
    f"{WINDOW_WHERE} AND trace_id NOT IN (SELECT trace_id FROM spans WHERE {WINDOW_WHERE} "
    f"AND tags LIKE '%\"{EVAL_TAG}\"%')"
)

#: El grafo del proyecto (D-188). La identidad de un paso es la del grafo de la traza
#: (D-153): `step_key`, o `tipo:nombre`. Los alias son los mismos que en ClickHouse.
_NODO_SQL = "CASE WHEN step_key != '' THEN step_key ELSE span_type || ':' || name END"

GRAPH_STEPS_SQL = f"""
SELECT
    {_NODO_SQL}                                                   AS nodo,
    MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS etiqueta,
    MAX(span_type)                                                AS tipo,
    COUNT(*)                                                      AS llamadas,
    COUNT(DISTINCT trace_id)                                      AS trazas,
    SUM(CASE WHEN parent_span_id = '' THEN 1 ELSE 0 END)          AS raices,
    SUM(cost_total_usd)                                           AS coste,
    SUM(cost_unknown)                                             AS sin_tarifa,
    SUM(usage_estimated)                                          AS estimadas,
    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)             AS errores,
    GROUP_CONCAT(DISTINCT CASE WHEN span_type = 'llm' THEN
        CASE WHEN response_model != '' THEN response_model ELSE request_model END
    END)                                                          AS modelos
FROM spans
WHERE {RULES_WHERE}
GROUP BY nodo
ORDER BY llamadas DESC, nodo
"""

#: Las aristas unen un span con su padre dentro de la ventana y de la misma traza. Un
#: padre que empezó antes de la ventana se queda fuera: lo mismo en los dos almacenes.
GRAPH_LINKS_SQL = f"""
WITH v AS (
    SELECT trace_id, span_id, parent_span_id, {_NODO_SQL} AS nodo,
           cost_total_usd, cost_unknown
    FROM spans
    WHERE {RULES_WHERE}
)
SELECT
    p.nodo                  AS de,
    c.nodo                  AS a,
    COUNT(*)                AS llamadas,
    SUM(c.cost_total_usd)   AS coste,
    SUM(c.cost_unknown)     AS sin_tarifa
FROM v AS c
JOIN v AS p ON p.trace_id = c.trace_id AND p.span_id = c.parent_span_id
WHERE p.nodo != c.nodo
GROUP BY de, a
ORDER BY llamadas DESC, de, a
"""

#: El mismo paso, con la misma entrada, repetido dentro de una misma traza.
#:
#: Se detecta por entrada repetida y se reporta por paso (D-062). SQLite no tiene
#: `argMax`, así que el representante de cada grupo sale del truco de concatenar el
#: número de repeticiones por delante y quedarse con el máximo alfabético.
REPEATED_GROUPS_SQL = f"""
WITH numerados AS (
    SELECT
        trace_id, dedup_hash, span_type, request_model, name, start_time,
        cost_total_usd, duration_ms, input_tokens, output_tokens,
        cost_unknown, cost_rate_assumed,
        CASE WHEN step_key   != '' THEN step_key   ELSE name END AS paso,
        CASE WHEN step_label != '' THEN step_label ELSE name END AS etiqueta,
        step_hint, step_site,
        ROW_NUMBER() OVER (
            PARTITION BY trace_id, dedup_hash ORDER BY start_time, span_id
        ) AS orden
    FROM spans
    WHERE {RULES_WHERE} AND dedup_hash != ''
),
por_traza AS (
    SELECT
        trace_id,
        dedup_hash,
        MAX(paso)          AS paso,
        MAX(etiqueta)      AS etiqueta,
        MAX(step_hint)     AS pista,
        MAX(step_site)     AS sitio,
        MAX(span_type)     AS tipo,
        MAX(request_model) AS modelo,
        MAX(start_time)    AS ultima,
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
    MAX(sitio)                                             AS sitio,
    MAX(tipo)                                              AS tipo,
    substr(MAX(printf('%012d', n) || dedup_hash), 13)      AS hash_ejemplo,
    MAX(ultima)                                            AS ultima,
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
ORDER BY extra_coste DESC, extra_spans DESC, paso, modelo
LIMIT :limit
"""

#: Medianas por (paso, modelo): duración y tokens de salida. Va aparte de `MODEL_USAGE_SQL` porque
#: SQLite no trae percentiles y hay que sacarla ordenando: se numeran las filas del
#: grupo y se toma la que coge `quantileExact(0.5)` en ClickHouse, la fila `n / 2 + 1`
#: (con un número par, la de arriba de las dos del medio).
#:
#: **Cada mediana con su propio orden.** La de la salida se sacaba de las filas ordenadas
#: por duración —la salida de la llamada mediana en tiempo, no la mediana de las
#: salidas—, y con un número par se hacía la media de las dos del medio: ninguna de las
#: dos era la cuenta de la nube (D-183).
MEDIAN_DURATION_SQL = f"""
WITH grupo AS (
    SELECT
        CASE WHEN step_key != '' THEN step_key ELSE name END AS paso_clave,
        request_model, span_id, duration_ms, output_tokens
    FROM spans
    WHERE {RULES_WHERE} AND span_type = 'llm' AND request_model != '' AND status != 'error'
),
ordenadas AS (
    SELECT
        paso_clave, request_model, duration_ms, output_tokens,
        ROW_NUMBER() OVER (
            PARTITION BY paso_clave, request_model ORDER BY duration_ms, span_id
        ) AS fila_duracion,
        ROW_NUMBER() OVER (
            PARTITION BY paso_clave, request_model ORDER BY output_tokens, span_id
        ) AS fila_salida,
        COUNT(*) OVER (PARTITION BY paso_clave, request_model) AS n
    FROM grupo
)
SELECT
    paso_clave,
    request_model,
    MAX(CASE WHEN fila_duracion = n / 2 + 1 THEN duration_ms END)  AS mediana,
    MAX(CASE WHEN fila_salida = n / 2 + 1 THEN output_tokens END)  AS mediana_salida
FROM ordenadas
GROUP BY paso_clave, request_model
"""

#: Bucles: el mismo paso muchas veces en una traza, con entradas que sólo cambian en
#: los números y pocas salidas distintas. Es la regla 4 (D-109). Se excluyen las
#: repeticiones exactas —`distintas_entradas = 1`—, que ya cuenta la regla 1.
LOOP_GROUPS_SQL = f"""
WITH por_traza AS (
    SELECT
        trace_id,
        loop_hash,
        MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS etiqueta,
        MAX(step_hint)                     AS pista,
        MAX(step_site)                     AS sitio,
        MAX(span_type)                     AS tipo,
        MAX(request_model)                 AS modelo,
        MAX(CASE WHEN step_key != '' THEN step_key ELSE name END) AS paso_clave,
        MAX(start_time)                    AS ultima,
        COUNT(*)                           AS n,
        COUNT(DISTINCT dedup_hash)         AS entradas,
        COUNT(DISTINCT loop_out_hash)      AS salidas,
        SUM(cost_total_usd)                AS coste,
        SUM(duration_ms)                   AS duracion,
        SUM(input_tokens)                  AS tok_in,
        SUM(output_tokens)                 AS tok_out,
        SUM(cost_unknown)                  AS sin_tarifa,
        MIN(cost_total_usd)                AS coste_primera,
        MIN(duration_ms)                   AS duracion_primera,
        MIN(input_tokens)                  AS tok_in_primera,
        MIN(output_tokens)                 AS tok_out_primera
    FROM spans
    WHERE {RULES_WHERE} AND loop_hash != ''
    GROUP BY trace_id, loop_hash
    HAVING n >= :min_vueltas AND entradas > 1 AND salidas <= :max_salidas
)
-- Una vuelta se reconoce por su entrada sin números (`loop_hash`), DENTRO de una
-- ejecución. El hallazgo, en cambio, es del PASO: agrupar también fuera por el hash
-- abría una tarjeta por cada pregunta de usuario distinta, y con tráfico real no hay
-- dos iguales (D-135). El hash que se devuelve es sólo un ejemplo para la ficha.
SELECT
    MAX(loop_hash)                     AS hash_ejemplo,
    MAX(ultima)                        AS ultima,
    MAX(etiqueta)                      AS nombre,
    MAX(pista)                         AS pista,
    MAX(sitio)                         AS sitio,
    MAX(tipo)                          AS tipo,
    MAX(modelo)                        AS modelo,
    paso_clave                         AS paso,
    COUNT(DISTINCT trace_id)           AS trazas,
    SUM(n)                             AS total_spans,
    SUM(n - 1)                         AS extra_spans,
    MAX(n)                             AS max_por_traza,
    MAX(entradas)                      AS distintas_entradas,
    MAX(salidas)                       AS distintas_salidas,
    SUM(coste - coste_primera)         AS extra_coste,
    SUM(duracion - duracion_primera)   AS extra_duracion,
    SUM(tok_in - tok_in_primera)       AS extra_tok_in,
    SUM(tok_out - tok_out_primera)     AS extra_tok_out,
    SUM(sin_tarifa)                    AS extra_sin_tarifa,
    MAX(trace_id)                      AS traza_ejemplo
FROM por_traza
GROUP BY paso_clave
ORDER BY extra_spans DESC, paso_clave
LIMIT :limit
"""

#: Una llamada cortada por el tope de salida (D-193). Se guarda como texto JSON; antes de
#: abrirla se descartan las vacías, que son casi todas.
_CORTADA = (
    "(finish_reasons LIKE '[%' AND finish_reasons != '[]' AND EXISTS ("
    f"SELECT 1 FROM json_each(finish_reasons) WHERE lower(value) IN ({_MOTIVOS_SQL})))"
)

def _rehechas_sql(fallida: str) -> str:
    """Llamadas fallidas y rehechas, por (paso, modelo): la consulta de la salida truncada
    (D-193) y de los reintentos por JSON roto (D-194), cada una con su `fallida`.

    Una llamada fallida se ha tirado si el mismo paso vuelve a llamar más tarde en la
    misma ejecución. No se cuentan las que ya reclama la repetición exacta (tantas copias
    de la misma entrada en la traza como pide esa regla) ni las de un bucle posible
    (tantas vueltas como pide esa regla): por lo bajo, nunca dos veces.
    """
    return f"""
WITH paso AS (
    SELECT
        trace_id, span_id, start_time, request_model, dedup_hash, loop_hash,
        CASE WHEN step_key != '' THEN step_key ELSE name END AS paso_clave,
        CASE WHEN step_label != '' THEN step_label ELSE name END AS etiqueta,
        step_hint, step_site, cost_total_usd, duration_ms, input_tokens, output_tokens,
        cost_unknown, cost_rate_assumed,
        {fallida} AS fallida
    FROM spans
    WHERE {RULES_WHERE} AND span_type = 'llm'
      AND trace_id IN (
          SELECT trace_id FROM spans
          WHERE {WINDOW_WHERE} AND span_type = 'llm' AND {fallida}
      )
),
marcadas AS (
    SELECT
        paso.*,
        MAX(start_time) OVER (PARTITION BY trace_id, paso_clave) AS ultima_del_paso,
        COUNT(*) OVER (PARTITION BY trace_id, dedup_hash)        AS copias,
        COUNT(*) OVER (PARTITION BY trace_id, loop_hash)         AS vueltas
    FROM paso
),
fallidas AS (
    SELECT
        marcadas.*,
        (start_time < ultima_del_paso
         AND (dedup_hash = '' OR copias < :min_repeats)
         AND (loop_hash = '' OR vueltas < :min_vueltas)) AS rehecha,
        (start_time = ultima_del_paso)                     AS sola
    FROM marcadas
    WHERE fallida
)
SELECT
    paso_clave                                                    AS paso,
    request_model                                                 AS modelo,
    MAX(etiqueta)                                                 AS nombre,
    MAX(step_hint)                                                AS pista,
    MAX(step_site)                                                AS sitio,
    COUNT(DISTINCT CASE WHEN rehecha THEN trace_id END)           AS trazas,
    SUM(rehecha)                                                  AS rehechas,
    SUM(sola)                                                     AS sin_rehacer,
    SUM(CASE WHEN rehecha THEN cost_total_usd ELSE 0 END)         AS coste,
    SUM(CASE WHEN rehecha THEN duration_ms ELSE 0 END)            AS duracion,
    SUM(CASE WHEN rehecha THEN input_tokens ELSE 0 END)           AS tok_in,
    SUM(CASE WHEN rehecha THEN output_tokens ELSE 0 END)          AS tok_out,
    SUM(CASE WHEN rehecha THEN cost_unknown ELSE 0 END)           AS sin_tarifa,
    SUM(CASE WHEN rehecha THEN cost_rate_assumed ELSE 0 END)      AS asumida,
    MAX(CASE WHEN rehecha THEN trace_id END)                      AS traza_ejemplo,
    MAX(CASE WHEN rehecha THEN start_time END)                    AS ultima
FROM fallidas
GROUP BY paso_clave, request_model
HAVING rehechas >= :min_rehechas
ORDER BY coste DESC, rehechas DESC, paso_clave, modelo
LIMIT :limit
"""


#: Salidas truncadas (D-193).
TRUNCATED_GROUPS_SQL = _rehechas_sql(_CORTADA)

#: Historial que crece sin límite (D-195). La conversación es la sesión si la hay, si no
#: la ejecución; dentro, las llamadas de un paso en orden. Se dejan fuera las que ya
#: cuentan la repetición, los bucles y las rehechas (cortadas o con el JSON roto).
HISTORY_GROUPS_SQL = f"""
WITH base AS (
    SELECT
        trace_id, span_id, start_time, input_tokens, request_model, dedup_hash, loop_hash,
        output_json, step_hint, step_site,
        CASE WHEN session_id != '' THEN 's:' || session_id ELSE 't:' || trace_id END
                                                                AS conversacion,
        CASE WHEN step_key != '' THEN step_key ELSE name END     AS paso_clave,
        CASE WHEN step_label != '' THEN step_label ELSE name END AS etiqueta,
        {_CORTADA}                                              AS cortada,
        COUNT(*) OVER (PARTITION BY trace_id, dedup_hash)        AS copias,
        COUNT(*) OVER (PARTITION BY trace_id, loop_hash)         AS vueltas
    FROM spans
    WHERE {RULES_WHERE} AND span_type = 'llm' AND input_tokens > 0
),
turnos AS (
    SELECT
        base.*,
        input_tokens - LAG(input_tokens) OVER w AS crece,
        FIRST_VALUE(input_tokens) OVER w        AS primera
    FROM base
    WHERE (dedup_hash = '' OR copias < :min_repeats)
      AND (loop_hash = '' OR vueltas < :min_vueltas)
      AND output_json != 'roto' AND NOT cortada
    WINDOW w AS (PARTITION BY conversacion, paso_clave ORDER BY start_time, span_id)
),
conversaciones AS (
    SELECT
        conversacion, paso_clave,
        MAX(etiqueta)                               AS etiqueta,
        MAX(step_hint)                              AS pista,
        MAX(step_site)                              AS sitio,
        MAX(request_model)                          AS modelo,
        COUNT(*)                                    AS n,
        MIN(primera)                                AS entrada_primera,
        MAX(input_tokens)                           AS entrada_ultima,
        SUM(input_tokens - primera)                 AS historial,
        SUM(CASE WHEN crece < 0 THEN 1 ELSE 0 END)  AS bajadas,
        COUNT(DISTINCT trace_id)                    AS trazas,
        MAX(trace_id)                               AS traza,
        MAX(start_time)                             AS ultima_vez
    FROM turnos
    GROUP BY conversacion, paso_clave
    HAVING n >= :min_turnos AND bajadas = 0
       AND entrada_ultima - entrada_primera >= :min_crece * (n - 1)
),
marcadas AS (
    SELECT conversaciones.*, MAX(n) OVER (PARTITION BY paso_clave) AS max_n
    FROM conversaciones
),
con_ejemplo AS (
    SELECT
        marcadas.*,
        MAX(CASE WHEN n = max_n THEN conversacion END) OVER (PARTITION BY paso_clave)
                                                                  AS ejemplo
    FROM marcadas
)
SELECT
    paso_clave                                                  AS paso,
    MAX(etiqueta)                                               AS nombre,
    MAX(pista)                                                  AS pista,
    MAX(sitio)                                                  AS sitio,
    MAX(modelo)                                                 AS modelo,
    COUNT(*)                                                    AS conversaciones,
    SUM(n)                                                      AS llamadas,
    SUM(trazas)                                                 AS trazas,
    SUM(historial)                                              AS historial,
    CAST(SUM(entrada_ultima - entrada_primera) AS REAL) / SUM(n - 1) AS crece,
    MAX(n)                                                      AS max_turnos,
    MAX(ejemplo)                                                AS conv_ejemplo,
    MAX(CASE WHEN conversacion = ejemplo THEN traza END)        AS traza_ejemplo,
    MAX(CASE WHEN conversacion = ejemplo THEN n END)            AS turnos_ejemplo,
    MAX(CASE WHEN conversacion = ejemplo THEN entrada_primera END) AS primera_ejemplo,
    MAX(CASE WHEN conversacion = ejemplo THEN entrada_ultima END)  AS ultima_ejemplo,
    MAX(ultima_vez)                                             AS ultima
FROM con_ejemplo
GROUP BY paso_clave
HAVING conversaciones >= :min_conversaciones
ORDER BY historial DESC, paso_clave
LIMIT :limit
"""

#: Reintentos por JSON mal formado (D-194). Una salida cortada es de la regla de arriba:
#: aquí no entra aunque su JSON esté roto, y así lo que reclama aquélla no se cuenta dos
#: veces.
JSON_ROTO_SQL = _rehechas_sql(f"(output_json = 'roto' AND NOT {_CORTADA})")

#: Uso por (paso, modelo): base de las reglas de modelo caro y de contexto fijo.
MODEL_USAGE_SQL = f"""
SELECT
    CASE WHEN step_key != '' THEN step_key ELSE name END AS paso_clave,
    MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS paso,
    MAX(step_hint)                                  AS pista,
    MAX(step_site)                                  AS sitio,
    MAX(prompt_name)                                AS prompt,
    MAX(prompt_version)                             AS version_prompt,
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
    -- El mínimo, sólo de las llamadas que respondieron: una fallida trae 0 tokens y
    -- hundía el suelo del contexto fijo (D-108). `NULLIF` las saca del MIN.
    MIN(CASE WHEN status != 'error' AND input_tokens > 0 THEN input_tokens END)
                                                    AS min_entrada,
    SUM(duration_ms)                                AS duracion,
    MAX(start_time)                                 AS ultima,
    MIN(trace_id)                                   AS traza_ejemplo,
    MAX(prefix_hash)                                AS prefijo
FROM spans
WHERE {RULES_WHERE} AND span_type = 'llm' AND request_model != ''
GROUP BY paso_clave, request_model
HAVING llamadas >= :min_calls
ORDER BY coste DESC, paso_clave, request_model
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
        -- Con `ORDER BY s2.start_time` a secas, dos spans que empiezan en el mismo
        -- instante dejan el nombre de la traza al azar del orden de lectura. El
        -- desempate por `span_id` es el mismo que usa ClickHouse en su `argMin`.
        (SELECT s2.name FROM spans s2
          WHERE s2.trace_id = spans.trace_id
          ORDER BY s2.start_time, s2.span_id LIMIT 1)
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
    MAX(customer_id)                                   AS trace_customer_id,
    MAX(CASE WHEN parent_span_id = '' THEN substr(input_payload, 1, 600) END)
                                                       AS root_input,
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
            conn.create_function("minusculas", 1, _minusculas, deterministic=True)
            self._local.conn = conn
        return conn

    def _query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, params or {})
        return [dict(row) for row in cursor.fetchall()]

    # -- escritura -------------------------------------------------------------------

    def migrate(self) -> None:
        """Crea el esquema si no existe, y pone al día una base ya creada. Idempotente."""
        self._conn.executescript(SCHEMA)
        self._poner_al_dia()
        self._conn.commit()
        logger.info("sqlite listo en %s", self._path)

    def _poner_al_dia(self) -> None:
        """Las columnas que no existían cuando se creó el fichero.

        Sin esto, actualizar Laplace con un `~/.laplace/laplace.db` de antes deja la
        interfaz con un «no such column» que el usuario no puede arreglar sin borrar sus
        trazas. Un producto que se instala con `pip install -U` tiene que sobrevivir a
        su propia actualización.
        """
        existentes = {f["name"] for f in self._query("PRAGMA table_info(spans)")}
        for nombre, tipo in COLUMNAS_TARDIAS:
            if nombre not in existentes:
                self._conn.execute(f"ALTER TABLE spans ADD COLUMN {nombre} {tipo}")
                logger.info("sqlite: columna %s añadida a spans", nombre)
        for sql in INDICES_TARDIOS:
            self._conn.execute(sql)

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

        # Las subconsultas de ids van acotadas al proyecto igual que la de fuera. Sin
        # esto, cada filtro recorría los spans de todos los proyectos para quedarse con
        # los de uno: el coste de filtrar crecía con la instalación, no con el proyecto.
        acotar = ""
        if filters.project_id:
            clauses.append("project_id = :project_id")
            params["project_id"] = filters.project_id
            acotar = " AND project_id = :project_id"
        if filters.since is not None:
            clauses.append("start_time >= :since")
            params["since"] = _iso(filters.since)
        if filters.until is not None:
            clauses.append("start_time <= :until")
            params["until"] = _iso(filters.until)
        # Y a la ventana, con el mismo margen que la nube (D-168).
        if filters.since is not None:
            acotar += " AND start_time >= :sub_since"
            params["sub_since"] = _iso(filters.since - MARGEN_FILTROS)
        if filters.until is not None:
            acotar += " AND start_time <= :sub_until"
            params["sub_until"] = _iso(filters.until + MARGEN_FILTROS)
        # Sesión y usuario los lleva el span raíz, no cada llamada: filtrar los spans
        # por ellos dejaba fuera los hijos y la traza salía con coste y tokens a cero.
        # Se filtran trazas, igual que el modelo (D-123).
        if filters.session_id:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans "
                f"WHERE session_id = :session_id{acotar})"
            )
            params["session_id"] = filters.session_id
        if filters.user_id:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans "
                f"WHERE user_id = :user_id{acotar})"
            )
            params["user_id"] = filters.user_id
        if filters.customer_id:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans "
                f"WHERE customer_id = :customer_id{acotar})"
            )
            params["customer_id"] = filters.customer_id
        if filters.model:
            # El modelo es de un span, no de la traza: se filtra por trazas que lo usan.
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans "
                f"WHERE request_model = :model{acotar})"
            )
            params["model"] = filters.model

        # El estado es una propiedad de la traza entera: «ok» significa que ninguno de
        # sus spans falló, así que va por exclusión.
        fallidas = f"SELECT DISTINCT trace_id FROM spans WHERE status = 'error'{acotar}"
        if filters.status == "error":
            clauses.append(f"trace_id IN ({fallidas})")
        elif filters.status == "ok":
            clauses.append(f"trace_id NOT IN ({fallidas})")

        sub: list[str] = []
        if filters.span_type:
            sub.append("span_type = :span_type")
            params["span_type"] = filters.span_type
        if filters.step_key:
            sub.append("(CASE WHEN step_key != '' THEN step_key ELSE name END) = :paso")
            params["paso"] = filters.step_key
        if filters.search:
            sub.append(
                "(instr(lower(name), lower(:search)) > 0 OR trace_id LIKE :prefijo ESCAPE '\\')"
            )
            params["search"] = filters.search
            # El texto se busca tal cual: un `%` o un `_` escritos por el usuario no son
            # comodines. Sin escapar, buscar «_» encontraba todas las trazas.
            literal = (
                filters.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            params["prefijo"] = f"{literal}%"
        if sub:
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE "
                + " AND ".join(sub)
                + acotar
                + ")"
            )

        # Lo mismo que en ClickHouse: acotada a la ventana, sin distinguir mayúsculas y
        # con `%` y `_` literales. Aquí se recorre: en local el volumen lo permite y un
        # índice de texto completo duplicaría el fichero (D-144).
        if filters.content:
            ventana = ""
            if filters.since is not None:
                ventana += " AND start_time >= :since"
            if filters.until is not None:
                ventana += " AND start_time <= :until"
            clauses.append(
                "trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE "
                f"instr({CONTENIDO}, :contenido) > 0{acotar}{ventana})"
            )
            params["contenido"] = filters.content.lower()

        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    def get_trace_spans(self, trace_id: str, project_id: str | None = None) -> list[Span]:
        params: dict[str, Any] = {"trace_id": trace_id}
        where = "trace_id = :trace_id"
        if project_id:
            where += " AND project_id = :project_id"
            params["project_id"] = project_id
        columnas = ", ".join(COLUMNS)
        sql = f"SELECT {columnas} FROM spans WHERE {where} ORDER BY start_time, span_id"
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
            ORDER BY last_seen DESC, project_id
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
    def loop_groups_sql(self) -> str:
        return LOOP_GROUPS_SQL

    @property
    def truncated_groups_sql(self) -> str:
        return TRUNCATED_GROUPS_SQL

    @property
    def json_retry_groups_sql(self) -> str:
        return JSON_ROTO_SQL

    @property
    def history_groups_sql(self) -> str:
        return HISTORY_GROUPS_SQL

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
                last_seen=utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in self._query(REPEATED_GROUPS_SQL, params)
        ]
        return disambiguate(grupos)

    def loop_groups(
        self, project_id: str, window: Window, *, min_vueltas: int = 4,
        max_salidas: int = 2, limit: int = 20,
    ) -> list[LoopGroup]:
        params = self._window_params(project_id, window)
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
                last_seen=utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in self._query(LOOP_GROUPS_SQL, params)
        ]
        # Los bucles nunca pasaron por aquí, y por eso el inicio enseñaba dos tarjetas
        # con el título idéntico para dos llamantes distintos del mismo paso (D-115).
        return disambiguate(grupos)

    def truncated_groups(
        self, project_id: str, window: Window, *, min_redone: int = 5,
        min_repeats: int = 3, min_vueltas: int = 4, limit: int = 20,
    ) -> list[RedoneGroup]:
        return self._rehechas(
            TRUNCATED_GROUPS_SQL, project_id, window, min_redone, min_repeats, min_vueltas,
            limit,
        )

    def json_retry_groups(
        self, project_id: str, window: Window, *, min_redone: int = 5,
        min_repeats: int = 3, min_vueltas: int = 4, limit: int = 20,
    ) -> list[RedoneGroup]:
        return self._rehechas(
            JSON_ROTO_SQL, project_id, window, min_redone, min_repeats, min_vueltas, limit
        )

    def _rehechas(
        self, sql: str, project_id: str, window: Window, min_redone: int,
        min_repeats: int, min_vueltas: int, limit: int,
    ) -> list[RedoneGroup]:
        params = self._window_params(project_id, window)
        params.update(
            min_rehechas=min_redone, min_repeats=min_repeats, min_vueltas=min_vueltas,
            limit=limit,
        )
        grupos = [
            RedoneGroup(
                name=r["nombre"],
                model=r["modelo"] or "",
                step_key=r["paso"] or "",
                site=r["sitio"] or "",
                hint=r["pista"] or "",
                traces=int(r["trazas"]),
                redone=int(r["rehechas"]),
                not_redone=int(r["sin_rehacer"] or 0),
                redone_cost_usd=float(r["coste"] or 0.0),
                redone_duration_ms=float(r["duracion"] or 0.0),
                redone_input_tokens=int(r["tok_in"] or 0),
                redone_output_tokens=int(r["tok_out"] or 0),
                redone_unknown_cost_spans=int(r["sin_tarifa"] or 0),
                redone_assumed_rate_spans=int(r["asumida"] or 0),
                sample_trace_id=r["traza_ejemplo"] or "",
                last_seen=utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in self._query(sql, params)
        ]
        return disambiguate(grupos)

    def history_groups(
        self, project_id: str, window: Window, *, min_turnos: int = 4,
        min_crece: int = 200, min_conversaciones: int = 3, min_repeats: int = 3,
        min_vueltas: int = 4, limit: int = 20,
    ) -> list[HistoryGroup]:
        params = self._window_params(project_id, window)
        params.update(
            min_turnos=min_turnos, min_crece=min_crece,
            min_conversaciones=min_conversaciones, min_repeats=min_repeats,
            min_vueltas=min_vueltas, limit=limit,
        )
        return disambiguate(
            [_history_group(r) for r in self._query(HISTORY_GROUPS_SQL, params)]
        )

    def sample_conversation(
        self, project_id: str, window: Window, step_key: str, conversation: str,
        limit: int = 40,
    ) -> list[Span]:
        params = self._window_params(project_id, window)
        tipo, _, valor = conversation.partition(":")
        params.update(paso=step_key, valor=valor, limit=limit)
        columna = "session_id" if tipo == "s" else "trace_id"
        columnas = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columnas} FROM spans
            WHERE {WINDOW_WHERE} AND {columna} = :valor AND span_type = 'llm'
              AND (CASE WHEN step_key != '' THEN step_key ELSE name END) = :paso
            ORDER BY start_time, span_id LIMIT :limit
        """
        return [row_to_span(r) for r in self._query(sql, params)]

    def sample_step_calls(
        self, project_id: str, window: Window, step_key: str, trace_id: str, limit: int = 40
    ) -> list[Span]:
        params = self._window_params(project_id, window)
        params.update(paso=step_key, trace_id=trace_id, limit=limit)
        columnas = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columnas} FROM spans
            WHERE {WINDOW_WHERE} AND trace_id = :trace_id AND span_type = 'llm'
              AND (CASE WHEN step_key != '' THEN step_key ELSE name END) = :paso
            ORDER BY start_time, span_id LIMIT :limit
        """
        return [row_to_span(r) for r in self._query(sql, params)]

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
                site=r["sitio"] or "",
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
                prefix=r["prefijo"] or "",
                sample_trace_id=r["traza_ejemplo"],
                last_seen=utc(r["ultima"]) if r["ultima"] else None,
            )
            for r in self._query(MODEL_USAGE_SQL, params)
        ]
        medianas = {
            (r["paso_clave"], r["request_model"]): (
                float(r["mediana"] or 0.0),
                float(r["mediana_salida"] or 0.0),
            )
            for r in self._query(MEDIAN_DURATION_SQL, params)
        }
        for uso in usos:
            uso.p50_duration_ms, uso.p50_output_tokens = medianas.get(
                (uso.key, uso.model), (0.0, 0.0)
            )
        return disambiguate(usos)

    def prefix_traces(self, project_id: str, window: Window) -> dict[tuple[str, str], int]:
        """Ejecuciones distintas por (prefijo, modelo), sin las tiradas de evaluación.

        Es lo que no sale de sumar los usos por paso: una ejecución que llama a dos pasos
        con el mismo prefijo cuenta una vez aquí y una en cada paso (D-178).
        """
        filas = self._conn.execute(
            f"""SELECT prefix_hash, request_model, COUNT(DISTINCT trace_id)
                FROM spans
                WHERE {RULES_WHERE} AND span_type = 'llm' AND prefix_hash != ''
                  AND request_model != ''
                GROUP BY prefix_hash, request_model""",
            self._window_params(project_id, window),
        ).fetchall()
        return {(f[0], f[1]): int(f[2]) for f in filas}

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
                GROUP BY trace_id ORDER BY n DESC, trace_id LIMIT 1""",
            params,
        )
        if not trazas:
            return []
        params["trace_id"] = trazas[0]["trace_id"]
        columnas = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columnas} FROM spans
            WHERE trace_id = :trace_id AND dedup_hash = :dedup_hash
            ORDER BY start_time, span_id LIMIT :limit
        """
        return [row_to_span(r) for r in self._query(sql, params)]

    def sample_loop(
        self, project_id: str, window: Window, loop_hash: str, limit: int = 40
    ) -> list[Span]:
        """La traza donde más vueltas da ese bucle, con sus vueltas."""
        params = self._window_params(project_id, window)
        params["loop_hash"] = loop_hash
        params["limit"] = limit
        trazas = self._query(
            f"""SELECT trace_id, COUNT(*) AS n FROM spans
                WHERE {WINDOW_WHERE} AND loop_hash = :loop_hash
                GROUP BY trace_id ORDER BY n DESC, trace_id LIMIT 1""",
            params,
        )
        if not trazas:
            return []
        params["trace_id"] = trazas[0]["trace_id"]
        columnas = ", ".join(COLUMNS)
        sql = f"""
            SELECT {columnas} FROM spans
            WHERE trace_id = :trace_id AND loop_hash = :loop_hash
            ORDER BY start_time, span_id LIMIT :limit
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

    def step_cost_series(
        self, project_id: str, window: Window, bucket_minutes: int
    ) -> StepCostSeries:
        params = self._window_params(project_id, window)
        params["origen"] = _iso(window.since)
        params["ancho"] = bucket_minutes
        tramo = "CAST((julianday(start_time) - julianday(:origen)) * 1440 / :ancho AS INTEGER)"
        total = self._query(
            f"""SELECT {tramo} AS tramo, SUM(cost_total_usd) AS coste
                FROM spans WHERE {WINDOW_WHERE} GROUP BY tramo""",
            params,
        )
        pasos = self._query(
            f"""SELECT CASE WHEN step_key != '' THEN step_key ELSE name END AS paso,
                       MAX(CASE WHEN step_label != '' THEN step_label ELSE name END)
                                                                        AS etiqueta,
                       MAX(step_site) AS sitio, MAX(step_hint) AS pista,
                       {tramo} AS tramo, SUM(cost_total_usd) AS coste
                FROM spans WHERE {RULES_WHERE} AND cost_total_usd > 0
                GROUP BY paso, tramo""",
            params,
        )
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
        # SQLite no tiene percentiles: se traen las duraciones ordenadas y se elige en
        # Python con el mismo rango que ClickHouse. En local son miles, no millones. La
        # duración, redondeada al milisegundo como en `TIMESERIES_SQL`.
        filas = self._query(
            f"""SELECT CAST(ROUND((julianday(MAX(end_time)) - julianday(MIN(start_time)))
                               * 86400000) AS INTEGER) AS d
                FROM spans WHERE {WINDOW_WHERE}
                GROUP BY trace_id ORDER BY d""",
            self._window_params(project_id, window),
        )
        ordenados = [float(f["d"]) for f in filas]
        return Latency(
            traces=len(ordenados),
            p50_ms=nearest_rank(ordenados, 0.5),
            p95_ms=nearest_rank(ordenados, 0.95),
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
                    -- Por sitio de llamada, no por nombre: dos agentes con una función
                    -- homónima se mezclaban y un pico de uno quedaba diluido en el otro
                    -- (D-106). La etiqueta se guarda aparte porque es lo que se enseña.
                    CASE
                        WHEN step_site  != '' THEN step_site
                        WHEN step_label != '' THEN step_label
                        ELSE name
                    END AS paso,
                    MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS etiqueta,
                    SUM(cost_total_usd) AS coste,
                    COUNT(*)            AS llamadas
                FROM spans WHERE {WINDOW_WHERE}
                GROUP BY paso""",
            params,
        )
        prompts = self._query(
            f"""SELECT DISTINCT prompt_name || '@' || prompt_version AS v FROM spans
                WHERE {WINDOW_WHERE} AND prompt_name != ''""",
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
                    cost_usd=float(f["coste"] or 0.0),
                    calls=int(f["llamadas"]),
                    label=f["etiqueta"] or f["paso"],
                )
                for f in pasos
                if f["paso"]
            },
            prompts={f["v"] for f in prompts if f["v"]},
        )

    def costs_for_traces(
        self, project_id: str, trace_ids: list[str]
    ) -> dict[str, TraceCost]:
        if not trace_ids:
            return {}
        marcas = ", ".join(f":t{i}" for i in range(len(trace_ids)))
        params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(trace_ids)}
        params["project_id"] = project_id
        filas = self._query(
            f"""
            SELECT
                trace_id,
                SUM(cost_total_usd)   AS coste,
                SUM(input_tokens)     AS tok_in,
                SUM(output_tokens)    AS tok_out,
                COUNT(*)              AS pasos,
                MAX(status = 'error') AS fallo,
                SUM(cost_unknown = 1)      AS sin_tarifa,
                SUM(cost_rate_assumed = 1) AS asumida,
                CAST(ROUND((julianday(MAX(end_time)) - julianday(MIN(start_time)))
                     * 86400000) AS INTEGER) AS duracion
            FROM spans
            WHERE project_id = :project_id AND trace_id IN ({marcas})
            GROUP BY trace_id
            """,
            params,
        )
        return {r["trace_id"]: row_to_trace_cost(r) for r in filas}

    # -- prompts (Fase 6) --------------------------------------------------------------

    def prompt_usage(
        self, project_id: str, window: Window, *, rules: bool = False
    ) -> list[PromptUsage]:
        """Coste y volumen por versión de prompt gestionado.

        El denominador es `COUNT(DISTINCT trace_id)` y no el número de llamadas: lo que
        se enseña es coste **por ejecución**, igual que en el panel, porque un prompt
        que se llame dos veces por ejecución no es el doble de caro por ejecución.
        """
        donde = RULES_WHERE if rules else WINDOW_WHERE
        pasos = "GROUP_CONCAT(DISTINCT step_key)" if rules else "''"
        ejemplo = "MAX(step_key || char(31) || trace_id)" if rules else "''"
        filas = self._query(
            f"""
            SELECT
                prompt_name                AS nombre,
                prompt_version             AS version,
                COUNT(DISTINCT trace_id)   AS trazas,
                COUNT(*)                   AS llamadas,
                SUM(cost_total_usd)        AS coste,
                SUM(input_tokens)          AS tok_in,
                SUM(output_tokens)         AS tok_out,
                SUM(duration_ms)           AS duracion,
                SUM(cost_unknown = 1)      AS sin_tarifa,
                SUM(cost_rate_assumed = 1) AS asumida,
                MIN(start_time)            AS primero,
                MAX(start_time)            AS ultimo,
                {pasos}                    AS pasos,
                {ejemplo}                  AS ejemplo
            FROM spans
            WHERE {donde} AND prompt_name != ''
            GROUP BY prompt_name, prompt_version
            ORDER BY prompt_name, prompt_version DESC
            """,
            self._window_params(project_id, window),
        )
        return [row_to_prompt_usage(f) for f in filas]

    def prompt_versions_by_trace(
        self, project_id: str, trace_ids: list[str], *, sin_evaluaciones: bool = False
    ) -> dict[str, list[tuple[str, int]]]:
        if not trace_ids:
            return {}
        marcas = ", ".join(f":t{i}" for i in range(len(trace_ids)))
        params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(trace_ids)}
        params["project_id"] = project_id
        # Sin las tiradas de evaluación cuando lo pide la pestaña de Prompts (D-160); la
        # comparación de tiradas sí las necesita, que es de lo que hablan.
        fuera = (
            f"AND trace_id NOT IN (SELECT trace_id FROM spans WHERE project_id = :project_id "
            f"AND tags LIKE '%\"{EVAL_TAG}\"%')"
            if sin_evaluaciones
            else ""
        )
        filas = self._query(
            f"""
            SELECT DISTINCT trace_id, prompt_name AS nombre, prompt_version AS version
            FROM spans
            WHERE project_id = :project_id AND trace_id IN ({marcas}) AND prompt_name != ''
              {fuera}
            """,
            params,
        )
        salida: dict[str, list[tuple[str, int]]] = {}
        for f in filas:
            salida.setdefault(f["trace_id"], []).append((f["nombre"], int(f["version"])))
        return salida

    def observed_prompts(
        self, project_id: str, window: Window, *, rules: bool = False
    ) -> list[ObservedPrompt]:
        """Juegos de instrucciones vistos en las trazas, para quien no gestiona prompts.

        Se agrupa por `step_key`, que incluye el camino de llamada y la huella del
        prompt de sistema. El camino sale también en cada fila porque es lo único que
        deja separar «otro llamante» de «otro prompt» más arriba (D-115). Sólo spans de
        LLM: un `tool` no tiene instrucciones que versionar.
        """
        donde = RULES_WHERE if rules else WINDOW_WHERE
        filas = self._query(
            f"""
            SELECT
                step_key                                             AS clave,
                MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS paso,
                MAX(step_site)            AS sitio,
                MAX(step_hint)            AS pista,
                COUNT(DISTINCT trace_id)  AS trazas,
                COUNT(*)                  AS llamadas,
                SUM(cost_total_usd)       AS coste,
                SUM(input_tokens)         AS tok_in,
                SUM(output_tokens)        AS tok_out,
                MIN(start_time)           AS primero,
                MAX(start_time)           AS ultimo
            FROM spans
            WHERE {donde} AND span_type = 'llm' AND step_key != ''
            GROUP BY step_key
            ORDER BY coste DESC, clave
            """,
            self._window_params(project_id, window),
        )
        return [row_to_observed_prompt(f) for f in filas]

    def co_occurring_step_keys(self, project_id: str, window: Window) -> set[str]:
        """Claves que comparten ejecución y camino con otra clave distinta.

        Primero los pares (camino, traza) con más de una clave; después, las claves que
        aparecen en esos pares. Acotado por proyecto y ventana en los dos pasos.
        """
        filas = self._query(
            f"""
            WITH juntas AS (
                SELECT step_site AS sitio, trace_id AS traza
                FROM spans
                WHERE {WINDOW_WHERE} AND span_type = 'llm' AND step_key != ''
                GROUP BY step_site, trace_id
                HAVING COUNT(DISTINCT step_key) > 1
            )
            SELECT DISTINCT s.step_key AS clave
            FROM spans s
            JOIN juntas j ON s.step_site = j.sitio AND s.trace_id = j.traza
            WHERE s.project_id = :project_id AND s.start_time >= :since
                AND s.start_time <= :until AND s.span_type = 'llm' AND s.step_key != ''
            """,
            self._window_params(project_id, window),
        )
        return {f["clave"] for f in filas}

    def project_graph(self, project_id: str, window: Window) -> GraphFacts:
        params = self._window_params(project_id, window)
        pasos = self._query(GRAPH_STEPS_SQL, params)
        aristas = self._query(GRAPH_LINKS_SQL, params)
        trazas = self._query(
            f"SELECT COUNT(DISTINCT trace_id) AS n FROM spans WHERE {RULES_WHERE}", params
        )
        return graph_facts(pasos, aristas, trazas[0]["n"] if trazas else 0)

    def coverage(self, project_id: str, window: Window) -> CoverageFacts:
        """Una sola consulta de cuentas sobre las llamadas a modelos de la ventana.

        La identidad «fuerte» se reconoce por lo que ya está guardado: cuando
        `step_identity` no encuentra ni sitio de llamada ni instrucciones, cae al nombre
        del span y deja la pista vacía y la etiqueta igual al nombre. Esa pareja es la
        firma exacta del caso débil, así que no hace falta una columna nueva para
        contarlo (y una columna nueva mentiría sobre los spans ya ingeridos).
        """
        params = self._window_params(project_id, window)
        fila = self._query(
            f"""
            SELECT
                COUNT(*) AS llamadas,
                SUM(CASE WHEN step_hint != '' OR step_label != name THEN 1 ELSE 0 END)
                                                   AS identificadas,
                SUM(CASE WHEN cost_unknown = 0 THEN 1 ELSE 0 END) AS con_tarifa,
                SUM(CASE WHEN usage_estimated = 0 AND (input_tokens > 0 OR output_tokens > 0)
                         THEN 1 ELSE 0 END)        AS con_tokens,
                SUM(CASE WHEN prompt_name != '' THEN 1 ELSE 0 END) AS con_prompt
            FROM spans
            WHERE {WINDOW_WHERE} AND span_type = 'llm'
            """,
            params,
        )[0]
        # Se agrupa por SITIO DE LLAMADA, no por nombre de función. Con el nombre, dos
        # agentes que tengan una función homónima caían en el mismo grupo y sus
        # poblaciones se mezclaban: el que llamaba bien compensaba al que llamaba mal y
        # la señal se callaba justo cuando había algo roto (D-106). El `CASE` deja que
        # las trazas anteriores al camino sigan agrupándose como antes.
        pasos = self._query(
            f"""
            SELECT
                CASE
                    WHEN step_site  != '' THEN step_site
                    WHEN step_label != '' THEN step_label
                    ELSE name
                END AS paso,
                MAX(CASE WHEN step_label != '' THEN step_label ELSE name END) AS etiqueta,
                COUNT(DISTINCT step_key)  AS identidades,
                COUNT(DISTINCT trace_id)  AS trazas
            FROM spans
            WHERE {WINDOW_WHERE} AND span_type = 'llm'
            GROUP BY paso
            """,
            params,
        )
        return coverage_from_rows(fila, pasos, self._muestreo(params))

    def _muestreo(self, params: dict[str, Any]) -> Any:
        """Lo que el SDK dejó fuera al muestrear (D-181). `sample_rate` es igual en todos
        los spans de una traza; `MAX` por traza para no contarla una vez por span."""
        return self._query(
            f"""
            SELECT COUNT(*) AS trazas, SUM(rate) AS representadas,
                   SUM(coste * (rate - 1)) AS no_visto
            FROM (
                SELECT trace_id, MAX(sample_rate) AS rate, SUM(cost_total_usd) AS coste
                FROM spans
                WHERE {WINDOW_WHERE} AND sample_rate > 1
                GROUP BY trace_id
            )
            """,
            params,
        )[0]

    def delete_project(self, project_id: str) -> None:
        self._conn.execute("DELETE FROM spans WHERE project_id = :p", {"p": project_id})
        self._conn.commit()

    # -- tarifas propias, reparto por usuario y retención (D-123) -------------------

    def unpriced_models(self, project_ids: list[str] | None, window: Window) -> list[str]:
        """Modelos con llamadas sin tarifa en la ventana, de esos proyectos (`None`: todos).

        Una consulta para todos los proyectos. La pantalla de tarifas lo sacaba antes de
        `summarize_window`, proyecto a proyecto y sobre 90 días: dos agregaciones enteras
        por proyecto para quedarse con una lista de nombres.
        """
        if project_ids is not None and not project_ids:
            return []
        params: dict[str, Any] = {"since": _iso(window.since), "until": _iso(window.until)}
        donde = (
            "cost_unknown = 1 AND request_model != '' "
            "AND start_time >= :since AND start_time <= :until"
        )
        if project_ids is not None:
            marcas = ", ".join(f":p{i}" for i in range(len(project_ids)))
            params.update({f"p{i}": p for i, p in enumerate(project_ids)})
            donde += f" AND project_id IN ({marcas})"
        filas = self._query(f"SELECT DISTINCT request_model AS m FROM spans WHERE {donde}", params)
        return sorted(f["m"] for f in filas)

    def spans_by_model(self, model: str) -> list[Span]:
        """Todas las llamadas a un modelo, de todos los proyectos. Para el recálculo."""
        columnas = ", ".join(COLUMNS)
        sql = (
            f"SELECT {columnas} FROM spans WHERE span_type = 'llm' "
            "AND (request_model = :m OR response_model = :m) ORDER BY start_time, span_id"
        )
        return [row_to_span(r) for r in self._query(sql, {"m": model})]

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
            WITH por_traza AS (
                SELECT trace_id,
                       MAX({columna})                                  AS clave,
                       SUM(cost_total_usd)                             AS coste,
                       SUM(input_tokens + output_tokens)               AS tokens,
                       SUM(span_type = 'llm' AND cost_unknown = 1)     AS sin_tarifa
                FROM spans
                WHERE {RULES_WHERE if rules else WINDOW_WHERE}
                GROUP BY trace_id
            )
            SELECT clave, COUNT(*) AS trazas, SUM(coste) AS coste, SUM(tokens) AS tokens,
                   SUM(sin_tarifa) AS sin_tarifa
            FROM por_traza
            GROUP BY clave
            ORDER BY coste DESC, trazas DESC, clave
            LIMIT :limit
        """
        return [
            CostGroup(
                key=r["clave"] or "",
                traces=int(r["trazas"] or 0),
                cost_usd=float(r["coste"] or 0.0),
                tokens=int(r["tokens"] or 0),
                unknown_cost_spans=int(r["sin_tarifa"] or 0),
            )
            for r in self._query(sql, params)
        ]

    def customer_step_costs(
        self, project_id: str, window: Window
    ) -> dict[str, dict[str, float]]:
        """Gemelo del de clickhouse.py (D-179)."""
        sql = f"""
            WITH cliente AS (
                SELECT trace_id, MAX(customer_id) AS c FROM spans
                WHERE {RULES_WHERE} GROUP BY trace_id
            )
            SELECT cliente.c AS cliente,
                   CASE WHEN step_key != '' THEN step_key ELSE name END AS paso,
                   SUM(cost_total_usd) AS coste
            FROM spans JOIN cliente USING (trace_id)
            WHERE {RULES_WHERE}
            GROUP BY cliente.c, paso
        """
        salida: dict[str, dict[str, float]] = {}
        for r in self._query(sql, self._window_params(project_id, window)):
            salida.setdefault(r["cliente"], {})[r["paso"]] = float(r["coste"] or 0.0)
        return salida

    def customer_steps(self, project_id: str, window: Window) -> dict[str, dict[str, int]]:
        sql = f"""
            WITH cliente AS (
                SELECT trace_id, MAX(customer_id) AS c FROM spans
                WHERE {RULES_WHERE} GROUP BY trace_id
            ),
            por_traza AS (
                SELECT cliente.c AS cliente,
                       CASE WHEN step_key != '' THEN step_key ELSE name END AS paso,
                       COUNT(*) AS n
                FROM spans JOIN cliente USING (trace_id)
                WHERE {WINDOW_WHERE} AND cliente.c != ''
                GROUP BY spans.trace_id, cliente.c, paso
            )
            SELECT cliente, paso, MAX(n) AS veces FROM por_traza GROUP BY cliente, paso
        """
        salida: dict[str, dict[str, int]] = {}
        for r in self._query(sql, self._window_params(project_id, window)):
            salida.setdefault(r["cliente"], {})[r["paso"]] = int(r["veces"])
        return salida

    def delete_before(self, cutoff: datetime) -> int:
        """Borra los spans que empezaron antes de `cutoff`. Es la retención."""
        cur = self._conn.execute("DELETE FROM spans WHERE start_time < :c", {"c": _iso(cutoff)})
        self._conn.commit()
        return cur.rowcount

    def delete_project_before(self, project_id: str, cutoff: datetime) -> None:
        self._conn.execute(
            "DELETE FROM spans WHERE project_id = :p AND start_time < :c",
            {"p": project_id, "c": _iso(cutoff)},
        )
        self._conn.commit()

    def delete_subject(
        self, project_id: str, *, user_id: str | None = None, customer_id: str | None = None
    ) -> int:
        columna, valor = _sujeto(user_id, customer_id)
        trazas = [
            r["trace_id"]
            for r in self._query(
                f"SELECT DISTINCT trace_id FROM spans WHERE project_id = :p AND {columna} = :v",
                {"p": project_id, "v": valor},
            )
        ]
        for inicio in range(0, len(trazas), 500):
            lote = trazas[inicio : inicio + 500]
            marcas = ", ".join(f":t{i}" for i in range(len(lote)))
            params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(lote)}
            params["p"] = project_id
            self._conn.execute(
                f"DELETE FROM spans WHERE project_id = :p AND trace_id IN ({marcas})", params
            )
        self._conn.commit()
        return len(trazas)

    def health(self) -> bool:
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("sqlite no responde", exc_info=True)
            return False


def _iso(value: datetime) -> str:
    return utc(value).isoformat()


def _history_group(r: Any) -> HistoryGroup:
    """Una fila de `HISTORY_GROUPS_SQL`; los alias son los de los dos almacenes."""
    return HistoryGroup(
        name=r["nombre"],
        model=r["modelo"] or "",
        step_key=r["paso"] or "",
        site=r["sitio"] or "",
        hint=r["pista"] or "",
        conversations=int(r["conversaciones"]),
        calls=int(r["llamadas"]),
        traces=int(r["trazas"]),
        history_tokens=int(r["historial"] or 0),
        growth_per_turn=float(r["crece"] or 0.0),
        max_turns=int(r["max_turnos"]),
        sample_conversation=r["conv_ejemplo"] or "",
        sample_trace_id=r["traza_ejemplo"] or "",
        sample_turns=int(r["turnos_ejemplo"] or 0),
        sample_first_input=int(r["primera_ejemplo"] or 0),
        sample_last_input=int(r["ultima_ejemplo"] or 0),
        last_seen=utc(r["ultima"]) if r["ultima"] else None,
    )
