-- Metadatos y huecos reservados. Idempotente.
--
-- ClickHouse guarda las trazas (inmutables, analíticas). Postgres guarda lo mutable y
-- relacional: proyectos, claves, y los dos huecos que el contrato reserva para las
-- fases 3 y 4. Se crean vacíos ahora para no migrar el esquema entero después.

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- Nunca se guarda la clave en claro.
    key_hash    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS api_keys_project_idx ON api_keys (project_id);

-- ---------------------------------------------------------------------------
-- Hueco reservado: diagnóstico automático (Fase 3, Norte A).
-- Un diagnóstico por traza: causa detectada + sugerencia de arreglo.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trace_diagnoses (
    trace_id              TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    model                 TEXT NOT NULL,
    cause                 TEXT NOT NULL,
    explanation           TEXT NOT NULL DEFAULT '',
    suggestion            TEXT NOT NULL DEFAULT '',
    categories            TEXT[] NOT NULL DEFAULT '{}',
    confidence            DOUBLE PRECISION,
    estimated_savings_usd DOUBLE PRECISION,
    raw                   JSONB
);

CREATE INDEX IF NOT EXISTS trace_diagnoses_project_idx ON trace_diagnoses (project_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Hueco reservado: evaluación (Fase 4).
-- Una anotación cuelga de una traza o de un span concreto: un veredicto sobre
-- "el paso 3 alucinó" es tan necesario como uno sobre la ejecución entera.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS annotations (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    trace_id    TEXT NOT NULL,
    span_id     TEXT,
    source      TEXT NOT NULL DEFAULT 'human',   -- human | llm_judge
    verdict     TEXT NOT NULL DEFAULT 'unknown', -- pass | fail | unknown
    score       DOUBLE PRECISION,
    label       TEXT,
    comment     TEXT,
    author      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lo que costo emitir un veredicto de maquina. NULL en toda anotacion humana: la
-- separacion entre persona y modelo es estructural, no una etiqueta que se pueda
-- olvidar de pintar (D-083). Y el coste del juez es coste real: si no se registra,
-- sabriamos menos de nuestro propio gasto que del del usuario.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_model          TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_input_tokens   INTEGER;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_output_tokens  INTEGER;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_cost_usd       DOUBLE PRECISION;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_cost_unknown   BOOLEAN;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS judge_prompt_version TEXT;

CREATE INDEX IF NOT EXISTS annotations_trace_idx ON annotations (trace_id);
CREATE INDEX IF NOT EXISTS annotations_project_idx ON annotations (project_id, created_at DESC);

-- Un veredicto por (traza, span, fuente, autor): volver a juzgar sustituye en vez de
-- acumular. Sin esto, correr el juez tres veces contaria tres veces en el acierto.
CREATE UNIQUE INDEX IF NOT EXISTS annotations_unicas
    ON annotations (trace_id, COALESCE(span_id, ''), source, COALESCE(author, ''));

-- Datasets de regresión construidos a partir de trazas reales (Fase 4).
CREATE TABLE IF NOT EXISTS datasets (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- El filtro del explorador con el que se materializo, tal cual se ejecuto: un conjunto
-- del que no se sabe como se formo no se puede discutir (D-067).
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS source_filter JSONB NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS dataset_items (
    id           TEXT PRIMARY KEY,
    dataset_id   TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    -- Procedencia: de qué traza real salió este caso.
    trace_id     TEXT,
    span_id      TEXT,
    input        JSONB,
    expected     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dataset_items_dataset_idx ON dataset_items (dataset_id);

-- ---------------------------------------------------------------------------
-- Tiradas de evaluacion (Fase 5).
-- Laplace NO ejecuta el agente de nadie: la tirada la corre el SDK dentro del
-- proceso del usuario y aqui llega el parte de lo que paso (D-086). Cada caso
-- apunta a la traza que produjo, que entra por la via normal de ingesta.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eval_runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    dataset_id  TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    -- Como llama el usuario a esta version del agente: main, prompt-v3, gpt-luna.
    variant     TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_run_items (
    run_id    TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id   TEXT NOT NULL,
    trace_id  TEXT NOT NULL,
    -- Un caso que revienta cuenta como fallo, no se descarta: descartarlo subiria el
    -- acierto por romperse mas.
    failed    BOOLEAN NOT NULL DEFAULT FALSE,
    error     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS eval_runs_project_idx ON eval_runs (project_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Prompts gestionados (Fase 6).
-- El texto sale del codigo del usuario y vive aqui; la version que se uso en
-- cada llamada vive en la traza. Eso es lo que permite que cada version lleve
-- pegado lo que costo y lo que acerto sobre el trafico que la uso (D-090).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompts (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ,
    -- La que sirve `laplace.get_prompt(nombre)`. NULL = ninguna, que es un estado
    -- legitimo: un prompt recien creado no tiene por que estar servido.
    production_version  INTEGER
);

-- Un nombre por proyecto: `get_prompt("resumen")` tiene que ser inequivoco.
CREATE UNIQUE INDEX IF NOT EXISTS prompts_nombre ON prompts (project_id, name);

-- Las versiones son INMUTABLES: editar crea la siguiente, nunca reescribe una. Si el
-- texto pudiera cambiar debajo, las metricas medidas sobre el trafico que lo uso
-- dejarian de querer decir nada sin que nadie se enterase.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS prompt_versions_unicas
    ON prompt_versions (prompt_id, version);

-- Cuando cada version paso a produccion. Sirve para contar la historia, no para
-- atribuir picos: eso lo hace la version que aparece en las trazas (D-092).
CREATE TABLE IF NOT EXISTS prompt_deploys (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    rollback    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS prompt_deploys_idx ON prompt_deploys (prompt_id, at DESC);

-- ---------------------------------------------------------------------------
-- Estado de las alertas (Fase 2, punto 3).
-- Lo unico que hace falta recordar para no repetirse: cuando se aviso de cada
-- hallazgo. `notified_at` abre el periodo de calma; `seen_at` es lo que impide
-- que un problema que sigue vivo se olvide y vuelva a contar como nuevo (D-074).
-- En modo local esta misma tabla vive en el fichero SQLite.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_state (
    project_id  TEXT NOT NULL,
    finding_id  TEXT NOT NULL,
    notified_at TIMESTAMPTZ NOT NULL,
    seen_at     TIMESTAMPTZ NOT NULL,
    amount_usd  DOUBLE PRECISION NOT NULL DEFAULT 0,
    times       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, finding_id)
);

INSERT INTO projects (id, name)
VALUES ('default', 'default')
ON CONFLICT (id) DO NOTHING;

-- El proyecto comodin de las claves de instalacion. Existe como fila para que la clave
-- ajena de api_keys se cumpla sin aflojarla: '*' no es un proyecto de verdad y no
-- recibe spans, pero una clave puede apuntar a el para verlos todos.
INSERT INTO projects (id, name)
VALUES ('*', 'todos los proyectos')
ON CONFLICT (id) DO NOTHING;
