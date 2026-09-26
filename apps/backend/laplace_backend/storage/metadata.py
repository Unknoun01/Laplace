"""Lo mutable y relacional: proyectos, anotaciones, conjuntos de casos y tiradas.

Las trazas son inmutables y analíticas, y viven en ClickHouse o en SQLite según el
modo. Lo de aquí es lo contrario: se edita, se borra y se relaciona. Hasta la Fase 5
sólo hacía falta en la nube —los huecos de las fases 3 y 4 devolvían vacío—, así que en
local se usaba un almacén nulo y no se notaba.

Con las evaluaciones eso deja de valer. Anotar una traza es el gesto más básico de la
pestaña, y un modo local que no pudiera anotar sería una versión recortada del producto,
que es justo lo que este proyecto lleva evitando desde D-015. Así que el almacén de
metadatos pasa a tener dos implementaciones de verdad, igual que el de trazas: Postgres
en la nube, SQLite en local, el mismo fichero que ya guarda los spans (D-084).

El nulo sigue existiendo, pero sólo para lo que de verdad es opcional: si en la nube se
cae Postgres, la ingesta y la lectura de trazas siguen en pie y lo que se pierde son las
anotaciones, no el producto entero.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from laplace.schema import (
    Annotation,
    Dataset,
    DatasetItem,
    Diagnosis,
    EvalRun,
    EvalRunItem,
    JudgeRun,
    Project,
    Prompt,
    PromptDeploy,
    PromptVersion,
)

logger = logging.getLogger("laplace.storage.metadata")


def new_id(prefix: str) -> str:
    """Identificador legible en un log y en una URL: `ds_3f2a…`."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MetadataStore(Protocol):
    """Lo que el backend necesita de la base de lo mutable."""

    def migrate(self) -> None: ...

    def health(self) -> bool: ...

    def list_projects(self) -> list[Project]: ...

    def ensure_project(self, project_id: str) -> None: ...

    def get_diagnosis(self, trace_id: str) -> Diagnosis | None: ...

    # -- anotaciones ------------------------------------------------------------------

    def list_annotations(
        self, trace_id: str, project_id: str | None = None
    ) -> list[Annotation]: ...

    def annotations_for(
        self, trace_ids: list[str], project_id: str | None = None
    ) -> dict[str, list[Annotation]]: ...

    def save_annotation(self, project_id: str, annotation: Annotation) -> Annotation: ...

    def delete_annotation(self, annotation_id: str, project_id: str | None = None) -> bool: ...

    # -- conjuntos de casos -----------------------------------------------------------

    def create_dataset(self, dataset: Dataset, items: list[DatasetItem]) -> Dataset: ...

    def list_datasets(self, project_id: str) -> list[Dataset]: ...

    def get_dataset(self, dataset_id: str) -> Dataset | None: ...

    def list_dataset_items(self, dataset_id: str) -> list[DatasetItem]: ...

    def delete_dataset(self, dataset_id: str, project_id: str | None = None) -> bool: ...

    # -- tiradas ----------------------------------------------------------------------

    def create_run(self, run: EvalRun) -> EvalRun: ...

    def list_runs(self, project_id: str, dataset_id: str | None = None) -> list[EvalRun]: ...

    def get_run(self, run_id: str) -> EvalRun | None: ...

    # -- anotaciones de un proyecto, para el acierto por versión de prompt ------------

    def annotations_since(self, project_id: str, since: Any) -> list[Annotation]: ...

    # -- prompts gestionados (Fase 6) -------------------------------------------------

    def create_prompt(self, prompt: Prompt) -> Prompt: ...

    def list_prompts(self, project_id: str) -> list[Prompt]: ...

    def get_prompt(self, prompt_id: str, project_id: str | None = None) -> Prompt | None: ...

    def add_prompt_version(
        self,
        prompt_id: str,
        text: str,
        *,
        notes: str = "",
        author: str = "",
        at: datetime | None = None,
    ) -> PromptVersion: ...

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersion]: ...

    def get_prompt_version(self, prompt_id: str, version: int) -> PromptVersion | None: ...

    def set_prompt_production(
        self,
        prompt_id: str,
        version: int,
        *,
        actor: str = "",
        note: str = "",
        at: datetime | None = None,
    ) -> PromptDeploy: ...

    def list_prompt_deploys(self, prompt_id: str) -> list[PromptDeploy]: ...

    def delete_prompt(self, prompt_id: str, project_id: str | None = None) -> bool: ...

    # -- claves de API (autenticación) -------------------------------------------------

    def api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None: ...

    def create_api_key(
        self, key_id: str, project_id: str, key_hash: str, name: str
    ) -> None: ...

    def list_api_keys(self, project_id: str | None = None) -> list[dict[str, Any]]: ...

    def revoke_api_key(self, key_id: str) -> bool: ...

    # -- ajustes por proyecto -----------------------------------------------------------

    def get_setting(self, project_id: str, key: str) -> dict[str, Any] | None: ...

    def set_setting(self, project_id: str, key: str, value: dict[str, Any]) -> None: ...

    def list_settings(self, project_id: str, prefix: str = "") -> dict[str, dict[str, Any]]: ...

    def delete_setting(self, project_id: str, key: str) -> bool: ...

    def delete_project_data(self, project_id: str) -> None: ...


# ---------------------------------------------------------------------------------
# Traducción de filas, compartida por los dos almacenes
# ---------------------------------------------------------------------------------


def _loads(value: Any) -> Any:
    """JSON guardado como texto, o ya decodificado por el driver."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def annotation_from_row(r: Any) -> Annotation:
    """Una anotación desde una fila.

    El bloque del juez se arma **sólo** si la fuente es de máquina. Si una fila humana
    llegase con coste de juez —por una migración a medias o por un bug— se ignora en
    vez de pintarse: una anotación humana con coste de modelo es una contradicción, y
    ante una contradicción se cree a la etiqueta de la fuente.
    """
    fuente = r["source"]
    juez = None
    if fuente == "llm_judge" and r["judge_model"]:
        juez = JudgeRun(
            model=r["judge_model"],
            input_tokens=int(r["judge_input_tokens"] or 0),
            output_tokens=int(r["judge_output_tokens"] or 0),
            cost_usd=float(r["judge_cost_usd"] or 0.0),
            cost_unknown=bool(r["judge_cost_unknown"]),
            prompt_version=r["judge_prompt_version"] or "",
        )
    return Annotation(
        id=r["id"],
        trace_id=r["trace_id"],
        span_id=r["span_id"],
        source=fuente,
        verdict=r["verdict"],
        score=r["score"],
        label=r["label"],
        comment=r["comment"],
        author=r["author"],
        created_at=_utc(r["created_at"]),
        judge=juez,
    )


def annotation_values(project_id: str, a: Annotation) -> tuple:
    """Los valores de una anotación en el orden de las columnas, para los dos almacenes.

    Una anotación humana escribe `NULL` en todas las columnas del juez. No es una
    optimización: es lo que hace imposible que un veredicto de persona acabe contando
    como coste de máquina.
    """
    juez = a.judge if a.source == "llm_judge" else None
    return (
        a.id,
        project_id,
        a.trace_id,
        a.span_id,
        a.source,
        a.verdict,
        a.score,
        a.label,
        a.comment,
        a.author,
        a.created_at,
        juez.model if juez else None,
        juez.input_tokens if juez else None,
        juez.output_tokens if juez else None,
        juez.cost_usd if juez else None,
        bool(juez.cost_unknown) if juez else None,
        juez.prompt_version if juez else None,
    )


ANNOTATION_COLUMNS = (
    "id, project_id, trace_id, span_id, source, verdict, score, label, comment, "
    "author, created_at, judge_model, judge_input_tokens, judge_output_tokens, "
    "judge_cost_usd, judge_cost_unknown, judge_prompt_version"
)


def prompt_from_row(r: Any) -> Prompt:
    """Un prompt desde una fila. La misma función en los dos almacenes (D-066)."""
    produccion = r["production_version"]
    return Prompt(
        id=r["id"],
        project_id=r["project_id"],
        name=r["name"],
        description=r["description"] or "",
        created_at=_utc(r["created_at"]),
        updated_at=_utc(r["updated_at"]) if r["updated_at"] else None,
        production_version=int(produccion) if produccion else None,
        version_count=int(r["n"] or 0),
    )


def prompt_version_from_row(r: Any) -> PromptVersion:
    return PromptVersion(
        id=r["id"],
        prompt_id=r["prompt_id"],
        version=int(r["version"]),
        text=r["text"] or "",
        notes=r["notes"] or "",
        author=r["author"] or "",
        created_at=_utc(r["created_at"]),
    )


def prompt_deploy_from_row(r: Any) -> PromptDeploy:
    return PromptDeploy(
        id=r["id"],
        prompt_id=r["prompt_id"],
        version=int(r["version"]),
        at=_utc(r["at"]),
        actor=r["actor"] or "",
        note=r["note"] or "",
        rollback=bool(r["rollback"]),
    )


ANNOTATION_READ = (
    "id, trace_id, span_id, source, verdict, score, label, comment, author, "
    "created_at, judge_model, judge_input_tokens, judge_output_tokens, "
    "judge_cost_usd, judge_cost_unknown, judge_prompt_version"
)


# ---------------------------------------------------------------------------------
# SQLite: el modo local
# ---------------------------------------------------------------------------------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
    id                    TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL,
    trace_id              TEXT NOT NULL,
    span_id               TEXT,
    -- human | llm_judge. Lo que decide si esta fila puede llevar coste de juez.
    source                TEXT NOT NULL DEFAULT 'human',
    verdict               TEXT NOT NULL DEFAULT 'unknown',
    score                 REAL,
    label                 TEXT,
    comment               TEXT,
    author                TEXT,
    created_at            TEXT NOT NULL,
    -- Columnas del juez: NULL en toda anotación humana.
    judge_model           TEXT,
    judge_input_tokens    INTEGER,
    judge_output_tokens   INTEGER,
    judge_cost_usd        REAL,
    judge_cost_unknown    INTEGER,
    judge_prompt_version  TEXT
);

CREATE INDEX IF NOT EXISTS annotations_trace_idx   ON annotations (trace_id);
CREATE INDEX IF NOT EXISTS annotations_project_idx ON annotations (project_id, created_at DESC);
-- Un veredicto por (traza, fuente): volver a juzgar sustituye, no acumula.
CREATE UNIQUE INDEX IF NOT EXISTS annotations_unicas
    ON annotations (trace_id, COALESCE(span_id, ''), source, COALESCE(author, ''));

CREATE TABLE IF NOT EXISTS datasets (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    source_filter  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS dataset_items (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    trace_id    TEXT,
    span_id     TEXT,
    input       TEXT,
    expected    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS dataset_items_idx ON dataset_items (dataset_id);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    variant     TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_run_items (
    run_id    TEXT NOT NULL,
    case_id   TEXT NOT NULL,
    trace_id  TEXT NOT NULL,
    failed    INTEGER NOT NULL DEFAULT 0,
    error     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS eval_runs_idx ON eval_runs (project_id, created_at DESC);

-- Prompts gestionados (Fase 6). El texto vive aquí y no en el código del usuario, y
-- la versión que se usó en cada llamada vive en la traza: así cada versión lleva
-- pegado lo que costó y lo que acertó sobre el tráfico que la usó (D-090).
CREATE TABLE IF NOT EXISTS prompts (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    -- La que sirve `laplace.get_prompt(nombre)`. NULL = ninguna, que es un estado
    -- legítimo: un prompt recién creado no tiene por qué estar servido.
    production_version  INTEGER
);

-- Un nombre por proyecto: `get_prompt("resumen")` tiene que ser inequívoco.
CREATE UNIQUE INDEX IF NOT EXISTS prompts_nombre ON prompts (project_id, name);

-- Las versiones son INMUTABLES. Editar crea la siguiente, nunca reescribe una: si el
-- texto pudiera cambiar debajo, las métricas medidas sobre el tráfico que lo usó
-- dejarían de querer decir nada sin que nadie se enterase.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS prompt_versions_unicas
    ON prompt_versions (prompt_id, version);

-- Cuándo cada versión pasó a producción. Sirve para contar la historia, no para
-- atribuir picos: eso lo hace la versión que aparece en las trazas (D-092).
CREATE TABLE IF NOT EXISTS prompt_deploys (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    rollback    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS prompt_deploys_idx ON prompt_deploys (prompt_id, at DESC);

-- Claves de API. La clave en claro NO se guarda nunca: sólo su SHA-256, que es lo que
-- se compara. `project_id` puede ser '*', que es la clave de instalación del operador.
-- Esta tabla existe también en local, donde no se pide credencial: tenerla aquí es lo
-- que permite probar la autenticación entera sin levantar Postgres.
CREATE TABLE IF NOT EXISTS api_keys (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked_at  TEXT,
    -- Caducidad, último uso y autor (D-127). NULL en las claves de antes, que no caducan.
    expires_at  TEXT,
    last_used_at TEXT,
    created_by  TEXT
);

CREATE INDEX IF NOT EXISTS api_keys_project_idx ON api_keys (project_id);

-- Ajustes por proyecto, clave → JSON: el estado de cada hallazgo, el presupuesto, las
-- alertas configuradas desde la interfaz y las tarifas propias (D-123). Una tabla y no
-- cinco porque ninguno de ellos se consulta por nada que no sea su clave, y cada tabla
-- nueva son tres implementaciones que mantener.
-- `project_id` es '*' para lo que vale para toda la instalación.
CREATE TABLE IF NOT EXISTS settings (
    project_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);
"""


#: Lo que se borra al borrar un proyecto, hijos primero (D-123).
_BORRAR_PROYECTO_SQLITE = (
    "DELETE FROM dataset_items WHERE dataset_id IN "
    "(SELECT id FROM datasets WHERE project_id = ?)",
    "DELETE FROM eval_run_items WHERE run_id IN "
    "(SELECT id FROM eval_runs WHERE project_id = ?)",
    "DELETE FROM prompt_versions WHERE prompt_id IN "
    "(SELECT id FROM prompts WHERE project_id = ?)",
    "DELETE FROM prompt_deploys WHERE prompt_id IN "
    "(SELECT id FROM prompts WHERE project_id = ?)",
    "DELETE FROM annotations WHERE project_id = ?",
    "DELETE FROM datasets WHERE project_id = ?",
    "DELETE FROM eval_runs WHERE project_id = ?",
    "DELETE FROM prompts WHERE project_id = ?",
    "DELETE FROM settings WHERE project_id = ?",
    "DELETE FROM api_keys WHERE project_id = ?",
)


class SQLiteMetadataStore:
    """Metadatos en el mismo fichero que las trazas. Es el modo local.

    Conexión propia y no la del `SQLiteStore`: son dos responsabilidades distintas y el
    protocolo del almacén de trazas no tiene por qué crecer con anotaciones. SQLite en
    WAL aguanta de sobra varias conexiones al mismo fichero.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Una conexión que se cierra al salir del `with` (D-131).

        `with sqlite3.connect(...)` confirma la transacción pero **no cierra**: la
        conexión quedaba viva hasta que el recolector pasara, que en Windows es tener el
        fichero abierto y desde Python 3.13 un aviso por cada una.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SQLITE_SCHEMA)
            # Ficheros de antes de D-127: `CREATE TABLE IF NOT EXISTS` no añade columnas.
            for columna in ("expires_at", "last_used_at", "created_by"):
                try:
                    conn.execute(f"ALTER TABLE api_keys ADD COLUMN {columna} TEXT")
                except sqlite3.OperationalError:
                    pass

    def health(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- proyectos ---------------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT id, name, created_at FROM projects ORDER BY created_at"
            ).fetchall()
        return [
            Project(id=f["id"], name=f["name"], created_at=_utc(f["created_at"]))
            for f in filas
        ]

    def ensure_project(self, project_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO projects (id, name, created_at) VALUES (?, ?, ?)",
                (project_id, project_id, _now().isoformat()),
            )

    def get_diagnosis(self, trace_id: str) -> Diagnosis | None:
        """Hueco de la Fase 3. En local todavía no hay diagnóstico automático."""
        return None

    # -- anotaciones -------------------------------------------------------------------

    def list_annotations(
        self, trace_id: str, project_id: str | None = None
    ) -> list[Annotation]:
        """Las anotaciones de una traza, acotadas al proyecto si se dice cuál.

        Un `trace_id` no es un secreto: viaja en cabeceras `traceparent` y en los logs de
        cualquiera. Sin acotar, la lectura por id devolvería las de otro proyecto.
        """
        where, args = "trace_id = ?", [trace_id]
        if project_id:
            where, args = "trace_id = ? AND project_id = ?", [trace_id, project_id]
        with self._conn() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations WHERE {where} "
                f"ORDER BY created_at",
                args,
            ).fetchall()
        return [annotation_from_row(f) for f in filas]

    def annotations_for(
        self, trace_ids: list[str], project_id: str | None = None
    ) -> dict[str, list[Annotation]]:
        if not trace_ids:
            return {}
        marcas = ", ".join("?" for _ in trace_ids)
        where, args = f"trace_id IN ({marcas})", list(trace_ids)
        if project_id:
            where += " AND project_id = ?"
            args.append(project_id)
        with self._conn() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE {where} ORDER BY created_at",
                args,
            ).fetchall()
        salida: dict[str, list[Annotation]] = {}
        for f in filas:
            salida.setdefault(f["trace_id"], []).append(annotation_from_row(f))
        return salida

    def save_annotation(self, project_id: str, annotation: Annotation) -> Annotation:
        valores = annotation_values(project_id, annotation)
        valores = tuple(v.isoformat() if isinstance(v, datetime) else v for v in valores)
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO annotations ({ANNOTATION_COLUMNS}) "
                f"VALUES ({', '.join('?' * 17)}) "
                f"ON CONFLICT (trace_id, COALESCE(span_id, ''), source, COALESCE(author, '')) "
                f"DO UPDATE SET verdict=excluded.verdict, score=excluded.score, "
                f"label=excluded.label, comment=excluded.comment, "
                f"created_at=excluded.created_at, judge_model=excluded.judge_model, "
                f"judge_input_tokens=excluded.judge_input_tokens, "
                f"judge_output_tokens=excluded.judge_output_tokens, "
                f"judge_cost_usd=excluded.judge_cost_usd, "
                f"judge_cost_unknown=excluded.judge_cost_unknown, "
                f"judge_prompt_version=excluded.judge_prompt_version",
                valores,
            )
            fila = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE trace_id = ? AND COALESCE(span_id, '') = ? AND source = ? "
                f"AND COALESCE(author, '') = ?",
                (
                    annotation.trace_id,
                    annotation.span_id or "",
                    annotation.source,
                    annotation.author or "",
                ),
            ).fetchone()
        return annotation_from_row(fila)

    def delete_annotation(self, annotation_id: str, project_id: str | None = None) -> bool:
        """Borra una anotación, **acotada al proyecto de quien la pide**.

        El `project_id` no es opcional por comodidad: es `None` sólo para la clave de
        instalación, que ve todo. Para cualquier otra, `Identity.scope()` lo rellena, y
        entonces el borrado de un id ajeno no encuentra nada y sale 404. Es el mismo
        patrón con el que D-097 cerró las lecturas, aplicado a las escrituras: acotar la
        consulta en vez de comprobar antes, para que no se pueda olvidar (D-121).
        """
        where, args = "id = ?", [annotation_id]
        if project_id:
            where, args = "id = ? AND project_id = ?", [annotation_id, project_id]
        with self._conn() as conn:
            cur = conn.execute(f"DELETE FROM annotations WHERE {where}", args)
            return cur.rowcount > 0

    # -- conjuntos ---------------------------------------------------------------------

    def create_dataset(self, dataset: Dataset, items: list[DatasetItem]) -> Dataset:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO datasets (id, project_id, name, description, created_at, "
                "source_filter) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dataset.id,
                    dataset.project_id,
                    dataset.name,
                    dataset.description,
                    dataset.created_at.isoformat(),
                    json.dumps(dataset.source_filter, ensure_ascii=False),
                ),
            )
            conn.executemany(
                "INSERT INTO dataset_items (id, dataset_id, trace_id, span_id, input, "
                "expected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        i.id,
                        dataset.id,
                        i.trace_id,
                        i.span_id,
                        json.dumps(i.input, ensure_ascii=False),
                        json.dumps(i.expected, ensure_ascii=False),
                        i.created_at.isoformat(),
                    )
                    for i in items
                ],
            )
        return dataset.model_copy(update={"item_count": len(items)})

    def list_datasets(self, project_id: str) -> list[Dataset]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT d.id, d.project_id, d.name, d.description, d.created_at, "
                "       d.source_filter, COUNT(i.id) AS n "
                "FROM datasets d LEFT JOIN dataset_items i ON i.dataset_id = d.id "
                "WHERE d.project_id = ? GROUP BY d.id ORDER BY d.created_at DESC",
                (project_id,),
            ).fetchall()
        return [_dataset_from_row(f) for f in filas]

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT d.id, d.project_id, d.name, d.description, d.created_at, "
                "       d.source_filter, COUNT(i.id) AS n "
                "FROM datasets d LEFT JOIN dataset_items i ON i.dataset_id = d.id "
                "WHERE d.id = ? GROUP BY d.id",
                (dataset_id,),
            ).fetchone()
        return _dataset_from_row(fila) if fila and fila["id"] else None

    def list_dataset_items(self, dataset_id: str) -> list[DatasetItem]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT id, dataset_id, trace_id, span_id, input, expected, created_at "
                "FROM dataset_items WHERE dataset_id = ? ORDER BY created_at, id",
                (dataset_id,),
            ).fetchall()
        return [_item_from_row(f) for f in filas]

    def delete_dataset(self, dataset_id: str, project_id: str | None = None) -> bool:
        """Igual que `delete_annotation`: el borrado va acotado, no comprobado (D-121)."""
        where, args = "id = ?", [dataset_id]
        if project_id:
            where, args = "id = ? AND project_id = ?", [dataset_id, project_id]
        with self._conn() as conn:
            cur = conn.execute(f"DELETE FROM datasets WHERE {where}", args)
            borrado = cur.rowcount > 0
            if borrado:
                conn.execute(
                    "DELETE FROM dataset_items WHERE dataset_id = ?", (dataset_id,)
                )
            return borrado

    # -- tiradas -----------------------------------------------------------------------

    def create_run(self, run: EvalRun) -> EvalRun:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO eval_runs (id, project_id, dataset_id, variant, notes, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.project_id,
                    run.dataset_id,
                    run.variant,
                    run.notes,
                    run.created_at.isoformat(),
                ),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO eval_run_items (run_id, case_id, trace_id, "
                "failed, error) VALUES (?, ?, ?, ?, ?)",
                [(run.id, i.case_id, i.trace_id, int(i.failed), i.error) for i in run.items],
            )
        return run

    def list_runs(self, project_id: str, dataset_id: str | None = None) -> list[EvalRun]:
        sql = (
            "SELECT id, project_id, dataset_id, variant, notes, created_at "
            "FROM eval_runs WHERE project_id = ?"
        )
        params: list[Any] = [project_id]
        if dataset_id:
            sql += " AND dataset_id = ?"
            params.append(dataset_id)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            filas = conn.execute(sql, tuple(params)).fetchall()
            runs = [_run_from_row(f) for f in filas]
            for run in runs:
                run.items = self._items_of(conn, run.id)
        return runs

    def get_run(self, run_id: str) -> EvalRun | None:
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT id, project_id, dataset_id, variant, notes, created_at "
                "FROM eval_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if fila is None:
                return None
            run = _run_from_row(fila)
            run.items = self._items_of(conn, run_id)
        return run

    @staticmethod
    def _items_of(conn: sqlite3.Connection, run_id: str) -> list[EvalRunItem]:
        filas = conn.execute(
            "SELECT case_id, trace_id, failed, error FROM eval_run_items WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return [
            EvalRunItem(
                case_id=f["case_id"],
                trace_id=f["trace_id"],
                failed=bool(f["failed"]),
                error=f["error"] or "",
            )
            for f in filas
        ]


    # -- anotaciones de un proyecto -----------------------------------------------------

    def annotations_since(self, project_id: str, since: Any) -> list[Annotation]:
        """Las anotaciones del proyecto desde una fecha.

        Es lo que cruza la pestaña de Prompts con las trazas para sacar el acierto por
        versión. Se pide por anotaciones y no por trazas porque las anotadas son unas
        decenas y el tráfico de una versión puede ser todo el del proyecto.
        """
        with self._conn() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE project_id = ? AND created_at >= ? ORDER BY created_at",
                (project_id, _iso(since)),
            ).fetchall()
        return [annotation_from_row(f) for f in filas]

    # -- prompts gestionados -------------------------------------------------------------

    def create_prompt(self, prompt: Prompt) -> Prompt:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO prompts (id, project_id, name, description, created_at, "
                "updated_at, production_version) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    prompt.id,
                    prompt.project_id,
                    prompt.name,
                    prompt.description,
                    prompt.created_at.isoformat(),
                    prompt.created_at.isoformat(),
                ),
            )
        return prompt

    def list_prompts(self, project_id: str) -> list[Prompt]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT p.id, p.project_id, p.name, p.description, p.created_at, "
                "       p.updated_at, p.production_version, COUNT(v.id) AS n "
                "FROM prompts p LEFT JOIN prompt_versions v ON v.prompt_id = p.id "
                "WHERE p.project_id = ? GROUP BY p.id ORDER BY p.name",
                (project_id,),
            ).fetchall()
        return [prompt_from_row(f) for f in filas]

    def get_prompt(self, prompt_id: str, project_id: str | None = None) -> Prompt | None:
        """Un prompt por su id, acotado al proyecto de quien lo pide (D-121).

        Lo usan las rutas que escriben —añadir versión, desplegar, borrar— para saber de
        quién es antes de tocarlo. Acotado, un id ajeno no existe.
        """
        where, args = "p.id = ?", [prompt_id]
        if project_id:
            where, args = "p.id = ? AND p.project_id = ?", [prompt_id, project_id]
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT p.id, p.project_id, p.name, p.description, p.created_at, "
                "       p.updated_at, p.production_version, COUNT(v.id) AS n "
                f"FROM prompts p LEFT JOIN prompt_versions v ON v.prompt_id = p.id "
                f"WHERE {where} GROUP BY p.id",
                args,
            ).fetchone()
        return prompt_from_row(fila) if fila and fila["id"] else None

    def add_prompt_version(
        self,
        prompt_id: str,
        text: str,
        *,
        notes: str = "",
        author: str = "",
        at: datetime | None = None,
    ) -> PromptVersion:
        """La siguiente versión. El número se calcula **dentro** de la escritura.

        Calcularlo fuera y pasarlo sería dejar que dos guardados a la vez escriban la
        misma versión con textos distintos, y a partir de ahí el tráfico de una versión
        sería de dos textos y nadie lo sabría.

        `at` fecha la versión en el pasado: sirve para importar un historial (y para la
        demo, que cuenta un mes). Sin él, es ahora.
        """
        ahora = at or _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            fila = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS ultima FROM prompt_versions "
                "WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()
            numero = int(fila["ultima"]) + 1
            version = PromptVersion(
                id=new_id("pv"),
                prompt_id=prompt_id,
                version=numero,
                text=text,
                notes=notes,
                author=author,
                created_at=ahora,
            )
            conn.execute(
                "INSERT INTO prompt_versions (id, prompt_id, version, text, notes, "
                "author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (version.id, prompt_id, numero, text, notes, author, ahora.isoformat()),
            )
            conn.execute(
                "UPDATE prompts SET updated_at = ? WHERE id = ?",
                (ahora.isoformat(), prompt_id),
            )
            conn.execute("COMMIT")
        return version

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersion]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT id, prompt_id, version, text, notes, author, created_at "
                "FROM prompt_versions WHERE prompt_id = ? ORDER BY version DESC",
                (prompt_id,),
            ).fetchall()
        return [prompt_version_from_row(f) for f in filas]

    def get_prompt_version(self, prompt_id: str, version: int) -> PromptVersion | None:
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT id, prompt_id, version, text, notes, author, created_at "
                "FROM prompt_versions WHERE prompt_id = ? AND version = ?",
                (prompt_id, int(version)),
            ).fetchone()
        return prompt_version_from_row(fila) if fila else None

    def set_prompt_production(
        self,
        prompt_id: str,
        version: int,
        *,
        actor: str = "",
        note: str = "",
        at: datetime | None = None,
    ) -> PromptDeploy:
        ahora = at or _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            fila = conn.execute(
                "SELECT production_version FROM prompts WHERE id = ?", (prompt_id,)
            ).fetchone()
            previa = fila["production_version"] if fila else None
            anterior = int(previa) if previa else 0
            # Volver a una versión anterior es un rollback y se marca como tal: la lista
            # de despliegues es la historia, y una vuelta atrás es justo el suceso que
            # alguien va a buscar en ella tres semanas después.
            despliegue = PromptDeploy(
                id=new_id("dep"),
                prompt_id=prompt_id,
                version=int(version),
                at=ahora,
                actor=actor,
                note=note,
                rollback=bool(anterior and version < anterior),
            )
            conn.execute(
                "INSERT INTO prompt_deploys (id, prompt_id, version, at, actor, note, "
                "rollback) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    despliegue.id,
                    prompt_id,
                    despliegue.version,
                    ahora.isoformat(),
                    actor,
                    note,
                    int(despliegue.rollback),
                ),
            )
            conn.execute(
                "UPDATE prompts SET production_version = ?, updated_at = ? WHERE id = ?",
                (int(version), ahora.isoformat(), prompt_id),
            )
            conn.execute("COMMIT")
        return despliegue

    def list_prompt_deploys(self, prompt_id: str) -> list[PromptDeploy]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT id, prompt_id, version, at, actor, note, rollback "
                "FROM prompt_deploys WHERE prompt_id = ? ORDER BY at DESC",
                (prompt_id,),
            ).fetchall()
        return [prompt_deploy_from_row(f) for f in filas]

    def delete_prompt(self, prompt_id: str, project_id: str | None = None) -> bool:
        """Igual que `delete_annotation`: el borrado va acotado, no comprobado (D-121).

        El orden importa: primero el prompt, y su histórico sólo si de verdad se borró.
        Antes se borraban las versiones y los despliegues primero, así que un intento
        ajeno dejaba el prompt en pie y su histórico vacío, que es peor que no borrar
        nada.
        """
        where, args = "id = ?", [prompt_id]
        if project_id:
            where, args = "id = ? AND project_id = ?", [prompt_id, project_id]
        with self._conn() as conn:
            cur = conn.execute(f"DELETE FROM prompts WHERE {where}", args)
            borrado = cur.rowcount > 0
            if borrado:
                conn.execute(
                    "DELETE FROM prompt_versions WHERE prompt_id = ?", (prompt_id,)
                )
                conn.execute(
                    "DELETE FROM prompt_deploys WHERE prompt_id = ?", (prompt_id,)
                )
            return borrado

    # -- claves de API -------------------------------------------------------------------

    def api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT id, project_id, name, created_at, revoked_at, expires_at FROM api_keys "
                "WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        return dict(fila) if fila else None

    def create_api_key(
        self, key_id: str, project_id: str, key_hash: str, name: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, project_id, key_hash, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, project_id, key_hash, name, _now().isoformat()),
            )

    def list_api_keys(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, project_id, name, created_at, revoked_at FROM api_keys"
        params: tuple = ()
        if project_id:
            sql += " WHERE project_id = ?"
            params = (project_id,)
        with self._conn() as conn:
            return [dict(f) for f in conn.execute(sql + " ORDER BY created_at", params)]

    def revoke_api_key(self, key_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now().isoformat(), key_id),
            )
            return cur.rowcount > 0

    # -- ajustes por proyecto -------------------------------------------------------------

    def get_setting(self, project_id: str, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            fila = conn.execute(
                "SELECT value FROM settings WHERE project_id = ? AND key = ?",
                (project_id, key),
            ).fetchone()
        return _loads(fila["value"]) if fila else None

    def set_setting(self, project_id: str, key: str, value: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (project_id, key, value, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (project_id, key) "
                "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (project_id, key, json.dumps(value, ensure_ascii=False), _now().isoformat()),
            )

    def list_settings(self, project_id: str, prefix: str = "") -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT key, value FROM settings WHERE project_id = ? AND substr(key, 1, ?) = ? "
                "ORDER BY key",
                (project_id, len(prefix), prefix),
            ).fetchall()
        return {f["key"]: _loads(f["value"]) for f in filas}

    def delete_setting(self, project_id: str, key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM settings WHERE project_id = ? AND key = ?", (project_id, key)
            )
            return cur.rowcount > 0

    def delete_project_data(self, project_id: str) -> None:
        """Todo lo mutable de un proyecto. Los hijos antes que los padres."""
        with self._conn() as conn:
            conn.execute("BEGIN")
            for sql in _BORRAR_PROYECTO_SQLITE:
                conn.execute(sql, (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.execute("COMMIT")


def _dataset_from_row(r: Any) -> Dataset:
    return Dataset(
        id=r["id"],
        project_id=r["project_id"],
        name=r["name"],
        description=r["description"] or "",
        created_at=_utc(r["created_at"]),
        item_count=int(r["n"] or 0),
        source_filter=_loads(r["source_filter"]) or {},
    )


def _item_from_row(r: Any) -> DatasetItem:
    return DatasetItem(
        id=r["id"],
        dataset_id=r["dataset_id"],
        trace_id=r["trace_id"] or "",
        span_id=r["span_id"],
        input=_loads(r["input"]),
        expected=_loads(r["expected"]),
        created_at=_utc(r["created_at"]),
    )


def _iso(value: Any) -> str:
    """Un instante como texto ISO, venga ya como texto o como `datetime`."""
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _run_from_row(r: Any) -> EvalRun:
    return EvalRun(
        id=r["id"],
        project_id=r["project_id"],
        dataset_id=r["dataset_id"],
        variant=r["variant"],
        notes=r["notes"] or "",
        created_at=_utc(r["created_at"]),
    )


# ---------------------------------------------------------------------------------
# El nulo: sólo para cuando en la nube se cae Postgres
# ---------------------------------------------------------------------------------


class NullMetadataStore:
    """Sustituto cuando no hay base de metadatos.

    La ingesta y la vista de trazas no pueden caerse porque falte Postgres: lo que se
    pierde son las anotaciones y las evaluaciones, no el producto. Escribir devuelve un
    error explícito en vez de fingir que se ha guardado, que es la forma de que alguien
    anote veinte trazas y las pierda todas sin enterarse.
    """

    def migrate(self) -> None:
        return None

    def health(self) -> bool:
        return False

    def list_projects(self) -> list[Project]:
        return []

    def ensure_project(self, project_id: str) -> None:
        return None

    def get_diagnosis(self, trace_id: str) -> Diagnosis | None:
        return None

    def list_annotations(
        self, trace_id: str, project_id: str | None = None
    ) -> list[Annotation]:
        return []

    def annotations_for(
        self, trace_ids: list[str], project_id: str | None = None
    ) -> dict[str, list[Annotation]]:
        return {}

    def save_annotation(self, project_id: str, annotation: Annotation) -> Annotation:
        raise MetadataUnavailable("no hay base de metadatos: la anotación no se ha guardado")

    def delete_annotation(self, annotation_id: str, project_id: str | None = None) -> bool:
        raise MetadataUnavailable("no hay base de metadatos")

    def create_dataset(self, dataset: Dataset, items: list[DatasetItem]) -> Dataset:
        raise MetadataUnavailable("no hay base de metadatos: el conjunto no se ha creado")

    def list_datasets(self, project_id: str) -> list[Dataset]:
        return []

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        return None

    def list_dataset_items(self, dataset_id: str) -> list[DatasetItem]:
        return []

    def delete_dataset(self, dataset_id: str, project_id: str | None = None) -> bool:
        raise MetadataUnavailable("no hay base de metadatos")

    def create_run(self, run: EvalRun) -> EvalRun:
        raise MetadataUnavailable("no hay base de metadatos: la tirada no se ha guardado")

    def list_runs(self, project_id: str, dataset_id: str | None = None) -> list[EvalRun]:
        return []

    def get_run(self, run_id: str) -> EvalRun | None:
        return None

    def annotations_since(self, project_id: str, since: Any) -> list[Annotation]:
        return []

    # -- prompts: leer devuelve vacío, escribir dice que no hay dónde ------------------

    def create_prompt(self, prompt: Prompt) -> Prompt:
        raise MetadataUnavailable("no hay base de metadatos: el prompt no se ha creado")

    def list_prompts(self, project_id: str) -> list[Prompt]:
        return []

    def get_prompt(self, prompt_id: str, project_id: str | None = None) -> Prompt | None:
        return None

    def add_prompt_version(
        self,
        prompt_id: str,
        text: str,
        *,
        notes: str = "",
        author: str = "",
        at: datetime | None = None,
    ) -> PromptVersion:
        raise MetadataUnavailable("no hay base de metadatos: la versión no se ha guardado")

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersion]:
        return []

    def get_prompt_version(self, prompt_id: str, version: int) -> PromptVersion | None:
        return None

    def set_prompt_production(
        self,
        prompt_id: str,
        version: int,
        *,
        actor: str = "",
        note: str = "",
        at: datetime | None = None,
    ) -> PromptDeploy:
        raise MetadataUnavailable(
            "no hay base de metadatos: el cambio de producción no se ha guardado"
        )

    def list_prompt_deploys(self, prompt_id: str) -> list[PromptDeploy]:
        return []

    def delete_prompt(self, prompt_id: str, project_id: str | None = None) -> bool:
        raise MetadataUnavailable("no hay base de metadatos")

    def api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        # Levanta en vez de devolver `None`. `None` significaría «esa clave no existe»
        # y aquí lo cierto es «no puedo comprobarlo», que la autenticación traduce a un
        # 503. Si devolviera `None`, un Postgres caído convertiría todas las claves
        # buenas en inválidas y el usuario buscaría el problema en el sitio equivocado.
        raise MetadataUnavailable("no hay base de metadatos: no se puede verificar la clave")

    def create_api_key(self, key_id: str, project_id: str, key_hash: str, name: str) -> None:
        raise MetadataUnavailable("no hay base de metadatos: la clave no se ha creado")

    def list_api_keys(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return []

    def revoke_api_key(self, key_id: str) -> bool:
        raise MetadataUnavailable("no hay base de metadatos")

    def get_setting(self, project_id: str, key: str) -> dict[str, Any] | None:
        return None

    def set_setting(self, project_id: str, key: str, value: dict[str, Any]) -> None:
        raise MetadataUnavailable("no hay base de metadatos: el ajuste no se ha guardado")

    def list_settings(self, project_id: str, prefix: str = "") -> dict[str, dict[str, Any]]:
        return {}

    def delete_setting(self, project_id: str, key: str) -> bool:
        raise MetadataUnavailable("no hay base de metadatos")

    def delete_project_data(self, project_id: str) -> None:
        return None


class MetadataUnavailable(RuntimeError):
    """No hay dónde guardar. La API lo traduce a un 503 con su explicación."""
