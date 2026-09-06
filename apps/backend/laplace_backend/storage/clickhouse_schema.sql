-- Esquema de ClickHouse. Idempotente: se aplica en cada arranque.
-- Un cambio aquí es un cambio en docs/trace-contract.md y en laplace/schema.py,
-- en el mismo commit.

-- Una fila por span. ReplacingMergeTree porque el exportador OTLP reintenta en
-- timeouts y 5xx: un span reenviado colapsa con el original en vez de duplicar coste.
-- Las consultas usan FINAL (ver DECISIONS D-008).
CREATE TABLE IF NOT EXISTS spans
(
    project_id          String,
    trace_id            String,
    span_id             String,
    parent_span_id      String,

    name                String,
    span_type           LowCardinality(String),
    status              LowCardinality(String),
    status_message      String,

    start_time          DateTime64(6, 'UTC'),
    end_time            DateTime64(6, 'UTC'),
    duration_ms         Float64,

    -- Atributos de una llamada a modelo.
    gen_ai_system       LowCardinality(String),
    operation           LowCardinality(String),
    request_model       LowCardinality(String),
    response_model      LowCardinality(String),
    response_id         String,

    -- Entrada y salida SIEMPRE por separado: es la base del panel de ahorro.
    input_tokens        UInt32,
    output_tokens       UInt32,
    cached_input_tokens UInt32,
    reasoning_tokens    UInt32,

    -- Coste desglosado por span, no sólo el total de la traza (contrato §4).
    cost_input_usd      Float64,
    cost_output_usd     Float64,
    cost_total_usd      Float64,
    cost_estimated      UInt8,

    -- Payloads en crudo, sin truncar (contrato §3): el diagnóstico automático
    -- necesita el texto real para razonar sobre la causa del fallo.
    input_messages      String,
    output_messages     String,
    llm_params          String,
    finish_reasons      Array(String),

    tool_name           String,
    tool_call_id        String,
    tool_arguments      String,
    tool_output         String,

    retrieval_query     String,
    retrieval_documents String,

    input_payload       String,
    output_payload      String,

    session_id          String,
    user_id             String,
    tags                Array(String),
    metadata            String,

    -- Hash de (tipo, nombre, modelo, entrada). Mismo hash dentro de una traza =
    -- llamada repetida. Alimenta la detección de bucles y el diagnóstico.
    dedup_hash          String,

    events              String,
    attributes          String,

    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3),

    -- Abrir una traza es la consulta más frecuente del producto y la UI no siempre
    -- sabe el proyecto, así que `WHERE trace_id = ...` no puede apoyarse en el prefijo
    -- de la clave de ordenación. Sin este índice, cada traza abierta escanea la tabla
    -- entera. Los spans de una traza son contiguos en el orden (project_id, trace_id,
    -- span_id), así que el bloom filter poda casi todos los gránulos.
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_start_time start_time TYPE minmax GRANULARITY 1,
    INDEX idx_session    session_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_dedup      dedup_hash TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(start_time)
ORDER BY (project_id, trace_id, span_id)
SETTINGS index_granularity = 8192;

-- Instalaciones anteriores al índice de trace_id: CREATE TABLE IF NOT EXISTS no toca
-- una tabla que ya existe. No se materializa aquí, porque sería una mutación en cada
-- arranque; las partes nuevas lo construyen solas y las viejas siguen leyéndose bien.
ALTER TABLE spans ADD INDEX IF NOT EXISTS idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1;
