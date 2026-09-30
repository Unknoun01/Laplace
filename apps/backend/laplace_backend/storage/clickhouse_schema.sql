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
    input_messages      String CODEC(ZSTD(3)),
    output_messages     String CODEC(ZSTD(3)),
    llm_params          String CODEC(ZSTD(3)),
    finish_reasons      Array(String),

    tool_name           String,
    tool_call_id        String,
    tool_arguments      String CODEC(ZSTD(3)),
    tool_output         String CODEC(ZSTD(3)),

    retrieval_query     String,
    retrieval_documents String CODEC(ZSTD(3)),

    input_payload       String CODEC(ZSTD(3)),
    output_payload      String CODEC(ZSTD(3)),

    session_id          String,
    user_id             String,
    tags                Array(String),
    metadata            String CODEC(ZSTD(3)),

    -- Hash de (tipo, nombre, modelo, entrada). Mismo hash dentro de una traza =
    -- llamada repetida. Alimenta la detección de bucles y el diagnóstico.
    dedup_hash          String,

    -- Identidad del PASO: mismo sitio de llamada y mismas instrucciones. Es por lo que
    -- agrupan las reglas 2 y 3. No es dedup_hash, que ademas exige la misma entrada.
    loop_hash           String,
    loop_out_hash       String,
    step_key            String,
    step_site           String,
    step_label          String,
    step_hint           String,

    -- Prompt gestionado que produjo la llamada, y su version. Vacio para quien no haya
    -- adoptado la gestion de prompts, que es el caso por defecto (D-090).
    prompt_name         LowCardinality(String),
    prompt_version      UInt32,

    customer_id         String,

    events              String CODEC(ZSTD(3)),
    attributes          String CODEC(ZSTD(3)),

    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3),

    -- Abrir una traza es la consulta más frecuente del producto y la UI no siempre
    -- sabe el proyecto, así que `WHERE trace_id = ...` no puede apoyarse en el prefijo
    -- de la clave de ordenación. Sin este índice, cada traza abierta escanea la tabla
    -- entera. Los spans de una traza son contiguos en el orden (project_id, trace_id,
    -- span_id), así que el bloom filter poda casi todos los gránulos.
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_start_time start_time TYPE minmax GRANULARITY 1,
    INDEX idx_session    session_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_dedup      dedup_hash TYPE bloom_filter(0.01) GRANULARITY 4,
    -- Las tiradas de evaluación se buscan por su etiqueta en cada lectura de las reglas
    -- (D-177): sin índice era recorrer la columna de la ventana entera.
    INDEX idx_tags       tags TYPE bloom_filter(0.01) GRANULARITY 4,
    -- La búsqueda en el contenido (D-144). Bloques de 4 bytes: con 3 casi todos los
    -- gránulos tienen todos los trozos de un id y no se descarta ninguno. La expresión
    -- es `CONTENIDO` de clickhouse.py, letra por letra.
    INDEX idx_contenido lowerUTF8(concat(input_messages, output_messages, tool_arguments, tool_output, retrieval_query, retrieval_documents, input_payload, output_payload)) TYPE ngrambf_v1(4, 65536, 2, 0) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(start_time)
-- La hora va antes que la traza (D-168, D-177): con `(project_id, trace_id, span_id)` los
-- días se mezclaban en cada gránulo, y una ventana de un día leía casi todo el histórico
-- del proyecto; con el día, leer una hora costaba el día entero, y los preagregados leen
-- en crudo la hora en curso en cada consulta. Las tablas de antes se migran con
-- `python -m laplace_backend.storage.migrar_orden`.
ORDER BY (project_id, toStartOfHour(start_time), trace_id, span_id)
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

-- Instalaciones anteriores a la identidad de paso (D-060). Antes las reglas agrupaban
-- por el nombre del span, que para una llamada auto-instrumentada es `chat <modelo>`:
-- todas las llamadas del agente al mismo modelo caian en el mismo grupo.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS step_key String DEFAULT '';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS step_label String DEFAULT '';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS step_hint String DEFAULT '';

-- Instalaciones anteriores a D-106. El sitio de un paso era el nombre de la funcion
-- que lo envuelve, asi que dos agentes con una funcion homonima compartian sitio y
-- sus poblaciones se mezclaban: el sano tapaba al roto. Ahora es el camino entero.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS step_site String DEFAULT '';

-- Instalaciones anteriores a D-109, cuando la unica senal de repeticion era el
-- hash exacto y un bucle con contador de intentos era invisible.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS loop_hash String DEFAULT '';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS loop_out_hash String DEFAULT '';

-- Instalaciones anteriores a la gestion de prompts (D-090). La version va en la traza
-- porque las metricas de la pestana son POR VERSION: sin la columna habria que
-- deducirlas del texto, que es justo lo que no se puede hacer sin mentir.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS prompt_name LowCardinality(String) DEFAULT '';
ALTER TABLE spans ADD COLUMN IF NOT EXISTS prompt_version UInt32 DEFAULT 0;

-- Instalaciones anteriores al margen por cliente (D-161): quien paga por el trabajo.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS customer_id String DEFAULT '';

-- Instalaciones anteriores a D-142. Los payloads son casi todo el disco, y con ZSTD(3)
-- ocupan menos de la mitad que con el LZ4 por defecto (medido con la prueba de carga).
-- Cambiar el códec no reescribe nada: las partes nuevas nacen con él y las viejas lo
-- toman al fusionarse.
ALTER TABLE spans MODIFY COLUMN input_messages String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN output_messages String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN llm_params String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN tool_arguments String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN tool_output String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN retrieval_documents String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN input_payload String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN output_payload String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN metadata String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN events String CODEC(ZSTD(3));
ALTER TABLE spans MODIFY COLUMN attributes String CODEC(ZSTD(3));

-- Instalaciones anteriores a la búsqueda en el contenido (D-144). Como el de trace_id, no
-- se materializa en el arranque: las partes nuevas lo traen, y en las viejas la búsqueda
-- funciona igual, recorriéndolas. Para tenerlo ya en todo:
-- ALTER TABLE spans MATERIALIZE INDEX idx_contenido
ALTER TABLE spans ADD INDEX IF NOT EXISTS idx_contenido lowerUTF8(concat(input_messages, output_messages, tool_arguments, tool_output, retrieval_query, retrieval_documents, input_payload, output_payload)) TYPE ngrambf_v1(4, 65536, 2, 0) GRANULARITY 1;

-- Instalaciones anteriores al índice de etiquetas (D-177). Como los otros, no se
-- materializa en el arranque: ALTER TABLE spans MATERIALIZE INDEX idx_tags
ALTER TABLE spans ADD INDEX IF NOT EXISTS idx_tags tags TYPE bloom_filter(0.01) GRANULARITY 4;

-- Preagregados del Diagnóstico (D-177). Parciales por minuto, recalculados por horas
-- enteras desde `spans FINAL` por `preagregados.py`, nunca sumados al insertar: un lote
-- reenviado o `recalcular_coste` vuelven a insertar spans, y una suma al insertar los
-- contaría dos veces. Cada fila lleva el `calculado` de su hora, y sólo vale la del
-- último cálculo (`pre_horas`): lo de cálculos anteriores se ignora al leer y se va
-- solo al fusionar. Lo que no se suma (trazas distintas, medianas, la primera
-- ocurrencia) va como estado de ClickHouse.
CREATE TABLE IF NOT EXISTS pre_pasos
(
    project_id      String,
    minuto          DateTime('UTC'),
    es_eval         UInt8,
    span_type       LowCardinality(String),
    step_key        String,
    name            String,
    step_label      String,
    step_site       String,
    step_hint       String,
    request_model   LowCardinality(String),
    prompt_name     LowCardinality(String),
    prompt_version  UInt32,
    n               UInt64,
    n_con_coste     UInt64,
    coste           Float64,
    tok_in          UInt64,
    tok_out         UInt64,
    cache_tok       UInt64,
    cache_escrito   UInt64,
    ahorro_cache    Float64,
    duracion        Float64,
    sin_tarifa      UInt64,
    asumida         UInt64,
    con_tokens      UInt64,
    primero         DateTime64(6, 'UTC'),
    ultimo          DateTime64(6, 'UTC'),
    traza_min       String,
    traza_max       String,
    trazas          AggregateFunction(uniqExact, String),
    trazas_error    AggregateFunction(uniqExactIf, String, UInt8),
    min_entrada     AggregateFunction(minIf, UInt32, UInt8),
    mediana_dur     AggregateFunction(quantileExactIf(0.5), Float64, UInt8),
    mediana_sal     AggregateFunction(quantileExactIf(0.5), UInt32, UInt8),
    calculado       DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(calculado)
PARTITION BY toYYYYMM(minuto)
ORDER BY (project_id, minuto, es_eval, span_type, step_key, name, step_label, step_site,
          step_hint, request_model, prompt_name, prompt_version);

-- Una fila por traza y minuto: la duración de una ejecución es de su primer span a su
-- último, y eso no sale de sumar minutos.
CREATE TABLE IF NOT EXISTS pre_trazas
(
    project_id  String,
    minuto      DateTime('UTC'),
    trace_id    String,
    inicio      DateTime64(6, 'UTC'),
    fin         DateTime64(6, 'UTC'),
    calculado   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(calculado)
PARTITION BY toYYYYMM(minuto)
ORDER BY (project_id, minuto, trace_id);

-- Las parejas (traza, entrada) que pueden llegar a repetirse: las que se repiten dentro
-- de su hora y las de trazas que cruzan el borde de la hora. Una pareja con un solo
-- span en su hora y sin nada fuera no llega a repetición en ninguna ventana.
CREATE TABLE IF NOT EXISTS pre_repes
(
    project_id  String,
    minuto      DateTime('UTC'),
    es_eval     UInt8,
    trace_id    String,
    dedup_hash  String,
    etiqueta    String,
    pista       String,
    sitio       String,
    tipo        String,
    modelo      String,
    paso        String,
    ultima      DateTime64(6, 'UTC'),
    n           UInt64,
    coste       Float64,
    duracion    Float64,
    tok_in      UInt64,
    tok_out     UInt64,
    sin_tarifa  UInt64,
    asumida     UInt64,
    primera     AggregateFunction(argMin, Tuple(Float64, Float64, UInt32, UInt32, UInt8, UInt8),
                                  Tuple(DateTime64(6, 'UTC'), String)),
    calculado   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(calculado)
PARTITION BY toYYYYMM(minuto)
ORDER BY (project_id, minuto, trace_id, dedup_hash);

-- Lo mismo para las vueltas de un bucle, por su entrada sin números.
CREATE TABLE IF NOT EXISTS pre_bucles
(
    project_id  String,
    minuto      DateTime('UTC'),
    es_eval     UInt8,
    trace_id    String,
    loop_hash   String,
    etiqueta    String,
    pista       String,
    sitio       String,
    tipo        String,
    modelo      String,
    paso        String,
    ultima      DateTime64(6, 'UTC'),
    n           UInt64,
    entradas    AggregateFunction(uniqExact, String),
    salidas     AggregateFunction(uniqExact, String),
    coste       Float64,
    coste_min   Float64,
    duracion    Float64,
    duracion_min Float64,
    tok_in      UInt64,
    tok_in_min  UInt32,
    tok_out     UInt64,
    tok_out_min UInt32,
    sin_tarifa  UInt64,
    calculado   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(calculado)
PARTITION BY toYYYYMM(minuto)
ORDER BY (project_id, minuto, trace_id, loop_hash);

-- Cuándo se calculó por última vez cada hora. Se escribe al final del cálculo: hasta
-- entonces se sigue leyendo el anterior, entero.
CREATE TABLE IF NOT EXISTS pre_horas
(
    project_id  String,
    hora        DateTime('UTC'),
    calculado   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(calculado)
ORDER BY (project_id, hora);

-- Cada escritura en `spans` apunta aquí sus horas, con la hora del servidor de después
-- de escribir. Una hora cuyo último apunte es posterior a su cálculo está sucia y se lee
-- en crudo hasta que se recalcule.
CREATE TABLE IF NOT EXISTS pre_sucias
(
    project_id  String,
    hora        DateTime('UTC'),
    marca       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(marca)
ORDER BY (project_id, hora);
