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

CREATE INDEX IF NOT EXISTS annotations_trace_idx ON annotations (trace_id);
CREATE INDEX IF NOT EXISTS annotations_project_idx ON annotations (project_id, created_at DESC);

-- Datasets de regresión construidos a partir de trazas reales (Fase 4).
CREATE TABLE IF NOT EXISTS datasets (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

INSERT INTO projects (id, name)
VALUES ('default', 'default')
ON CONFLICT (id) DO NOTHING;
