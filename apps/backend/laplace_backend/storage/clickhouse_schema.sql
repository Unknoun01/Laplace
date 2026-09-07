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
    -- Los tokens de cache van DENTRO de input_tokens, que es el total facturable
    -- (contrato §2). Aqui se guarda que parte del total fue cada cosa, porque cada
    -- tramo se cobra a una tarifa distinta y sin el desglose la factura no cuadra.
    cached_input_tokens UInt32,
    cache_write_tokens  UInt32,
    cache_write_1h_tokens UInt32,
    reasoning_tokens    UInt32,
    -- 1 = los tokens los contó el SDK porque el proveedor no los dio (streaming sin
    -- `include_usage`). El coste derivado es una aproximación, y se dice.
    usage_estimated     UInt8,

    -- Coste desglosado por span, no sólo el total de la traza (contrato §4).
    cost_input_usd      Float64,
    cost_output_usd     Float64,
    cost_total_usd      Float64,
    -- 1 = no sabemos cuánto cuesta ese modelo. NO es lo mismo que coste cero.
    cost_unknown        UInt8,
    -- Tarifa aplicada: `<modelo de la tabla> @ <versión de la tabla>`. Sirve para
    -- auditar un cálculo meses después, cuando los precios ya hayan cambiado.
    price_rate          String,
    -- Reparto del coste de entrada entre leer y escribir cache.
    cost_cache_read_usd  Float64,
    cost_cache_write_usd Float64,
    -- Lo que la cache YA ha ahorrado en este span. Dinero medido, no proyectado.
    cost_cache_saving_usd Float64,
    -- 1 = no se pudo saber que metro aplico (contexto largo, residencia de datos,
    -- modo rapido) y se cobro el estandar. El coste real podria ser mayor.
    cost_rate_assumed   UInt8,
    price_note          String,
    -- Metro pedido por la llamada: standard, batch, fast (o lo que diga el proveedor).
    billing_tier        LowCardinality(String),
    billing_region      LowCardinality(String),

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

-- Instalaciones anteriores al coste desconocido. `cost_estimated` significaba "no había
-- tarifa" pero guardaba un 0 que se sumaba a los totales; ahora se llama por su nombre.
ALTER TABLE spans RENAME COLUMN IF EXISTS cost_estimated TO cost_unknown;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS price_rate String;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS usage_estimated UInt8 DEFAULT 0;

-- Instalaciones anteriores al modelo de cache por tramos (§1.1 del encargo). Antes se
-- cobraba toda la entrada a una sola tarifa, lo que inflaba la factura de cualquier
-- agente con cache activa, que son casi todos.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cache_write_tokens UInt32 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cache_write_1h_tokens UInt32 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cost_cache_read_usd Float64 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cost_cache_write_usd Float64 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cost_cache_saving_usd Float64 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS cost_rate_assumed UInt8 DEFAULT 0;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS price_note String DEFAULT '';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS billing_tier LowCardinality(String) DEFAULT 'standard';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS billing_region LowCardinality(String) DEFAULT 'global';
