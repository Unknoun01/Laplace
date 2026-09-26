"""Metadatos en Postgres: proyectos, anotaciones, conjuntos de casos y tiradas.

Las trazas viven en ClickHouse (inmutables, analíticas). Aquí vive lo mutable y
relacional. Hasta la Fase 5 casi todo esto devolvía vacío; ahora lo llena la pestaña de
Evaluaciones. La contraparte local está en `metadata.py` y se comprueba que las dos
dicen lo mismo, igual que con el almacén de trazas (D-084).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from laplace.schema import (
    Annotation,
    Dataset,
    DatasetItem,
    Diagnosis,
    EvalRun,
    EvalRunItem,
    Project,
    Prompt,
    PromptDeploy,
    PromptVersion,
)

from ..config import Settings
from .metadata import (
    ANNOTATION_COLUMNS,
    ANNOTATION_READ,
    NullMetadataStore,
    annotation_from_row,
    annotation_values,
    new_id,
    prompt_deploy_from_row,
    prompt_from_row,
    prompt_version_from_row,
)

logger = logging.getLogger("laplace.storage.postgres")

_SCHEMA = Path(__file__).with_name("postgres_schema.sql")


class _Row:
    """Acceso por nombre a una tupla de psycopg, para reutilizar las traducciones.

    `psycopg` devuelve tuplas por defecto. En vez de cambiar el `row_factory` global
    —que afectaría a consultas que ya funcionan— se envuelve la fila aquí: así
    `annotation_from_row` es literalmente la misma función en los dos almacenes y no
    puede haber una anotación que se lea distinto en local que en la nube.
    """

    __slots__ = ("_data",)

    def __init__(self, columnas: list[str], fila: tuple) -> None:
        self._data = dict(zip(columnas, fila, strict=False))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


def _rows(columnas: str, filas: list[tuple]) -> list[_Row]:
    nombres = [c.strip() for c in columnas.replace("\n", " ").split(",")]
    return [_Row(nombres, f) for f in filas]


#: Los nombres con los que `prompt_from_row` y compañía leen una fila. Son el mismo
#: contrato que `ANNOTATION_READ`: la traducción de fila a modelo es literalmente la
#: misma función en los dos almacenes, así que los alias tienen que coincidir (D-066).
PROMPT_READ = (
    "p.id, p.project_id, p.name, p.description, p.created_at, p.updated_at, "
    "p.production_version"
)
_PROMPT_ALIASES = (
    "id, project_id, name, description, created_at, updated_at, production_version, n"
)
_VERSION_ALIASES = "id, prompt_id, version, text, notes, author, created_at"
_DEPLOY_ALIASES = "id, prompt_id, version, at, actor, note, rollback"


#: Lo que se borra al borrar un proyecto, hijos primero (D-123).
_BORRAR_PROYECTO = (
    "DELETE FROM dataset_items WHERE dataset_id IN "
    "(SELECT id FROM datasets WHERE project_id = %s)",
    "DELETE FROM eval_run_items WHERE run_id IN "
    "(SELECT id FROM eval_runs WHERE project_id = %s)",
    "DELETE FROM prompt_versions WHERE prompt_id IN "
    "(SELECT id FROM prompts WHERE project_id = %s)",
    "DELETE FROM prompt_deploys WHERE prompt_id IN "
    "(SELECT id FROM prompts WHERE project_id = %s)",
    "DELETE FROM annotations WHERE project_id = %s",
    "DELETE FROM datasets WHERE project_id = %s",
    "DELETE FROM eval_runs WHERE project_id = %s",
    "DELETE FROM prompts WHERE project_id = %s",
    "DELETE FROM settings WHERE project_id = %s",
    "DELETE FROM api_keys WHERE project_id = %s",
)
#: Lo que sólo existe en la nube.
_BORRAR_PROYECTO_PG = (
    "DELETE FROM trace_diagnoses WHERE project_id = %s",
    "DELETE FROM alert_state WHERE project_id = %s",
    "DELETE FROM projects WHERE id = %s",
)


class PostgresMetadataStore:
    """Acceso a Postgres. Una conexión por operación: se usa poco y así no hay estado."""

    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.postgres_dsn
        #: Proyectos ya registrados por este proceso. Cada lote de la ingesta pregunta,
        #: y sin esto era una escritura en Postgres por lote, siempre la misma (D-142).
        self._registrados: set[str] = set()

    def _connect(self) -> Any:
        # Import perezoso: `psycopg` es una dependencia opcional (extra `cloud`). En
        # modo local no hay Postgres, y exigir el driver para abrir la interfaz en el
        # portátil sería cobrar por algo que no se usa (D-068).
        # Del pool del proceso (`_pg.py`). «No conecto» sale como `MetadataUnavailable`,
        # que las rutas traducen a 503 con su motivo en vez de un 500 con la traza.
        from ._pg import conexion

        return conexion(self._dsn)

    def migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(_SCHEMA.read_text(encoding="utf-8"))
        logger.info("esquema de postgres aplicado")

    def health(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("postgres no responde", exc_info=True)
            return False

    # -- proyectos -----------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM projects ORDER BY created_at"
            ).fetchall()
        return [Project(id=r[0], name=r[1], created_at=r[2]) for r in rows]

    def ensure_project(self, project_id: str) -> None:
        """Registra el proyecto la primera vez que llegan spans suyos."""
        if project_id in self._registrados:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (project_id, project_id),
            )
        self._registrados.add(project_id)

    # -- hueco reservado de la Fase 3 ------------------------------------------------

    def get_diagnosis(self, trace_id: str) -> Diagnosis | None:
        """Diagnóstico de la traza (Fase 3). Hoy siempre `None`."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT trace_id, project_id, created_at, model, cause, explanation,
                       suggestion, categories, confidence, estimated_savings_usd
                FROM trace_diagnoses WHERE trace_id = %s
                """,
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return Diagnosis(
            trace_id=row[0],
            project_id=row[1],
            created_at=row[2],
            model=row[3],
            cause=row[4],
            explanation=row[5] or "",
            suggestion=row[6] or "",
            categories=list(row[7] or []),
            confidence=row[8],
            estimated_savings_usd=row[9],
        )

    # -- anotaciones -----------------------------------------------------------------

    def list_annotations(
        self, trace_id: str, project_id: str | None = None
    ) -> list[Annotation]:
        where, args = "trace_id = %s", [trace_id]
        if project_id:
            where, args = "trace_id = %s AND project_id = %s", [trace_id, project_id]
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations WHERE {where} "
                f"ORDER BY created_at",
                args,
            ).fetchall()
        return [annotation_from_row(r) for r in _rows(ANNOTATION_READ, filas)]

    def annotations_for(
        self, trace_ids: list[str], project_id: str | None = None
    ) -> dict[str, list[Annotation]]:
        if not trace_ids:
            return {}
        where, args = "trace_id = ANY(%s)", [list(trace_ids)]
        if project_id:
            where += " AND project_id = %s"
            args.append(project_id)
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE {where} ORDER BY created_at",
                args,
            ).fetchall()
        salida: dict[str, list[Annotation]] = {}
        for fila in _rows(ANNOTATION_READ, filas):
            salida.setdefault(fila["trace_id"], []).append(annotation_from_row(fila))
        return salida

    def save_annotation(self, project_id: str, annotation: Annotation) -> Annotation:
        marcas = ", ".join(["%s"] * 17)
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO annotations ({ANNOTATION_COLUMNS}) VALUES ({marcas})
                ON CONFLICT (trace_id, COALESCE(span_id, ''), source, COALESCE(author, ''))
                DO UPDATE SET
                    verdict = EXCLUDED.verdict, score = EXCLUDED.score,
                    label = EXCLUDED.label, comment = EXCLUDED.comment,
                    created_at = EXCLUDED.created_at,
                    judge_model = EXCLUDED.judge_model,
                    judge_input_tokens = EXCLUDED.judge_input_tokens,
                    judge_output_tokens = EXCLUDED.judge_output_tokens,
                    judge_cost_usd = EXCLUDED.judge_cost_usd,
                    judge_cost_unknown = EXCLUDED.judge_cost_unknown,
                    judge_prompt_version = EXCLUDED.judge_prompt_version
                """,
                annotation_values(project_id, annotation),
            )
            fila = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE trace_id = %s AND COALESCE(span_id, '') = %s AND source = %s "
                f"AND COALESCE(author, '') = %s",
                (
                    annotation.trace_id,
                    annotation.span_id or "",
                    annotation.source,
                    annotation.author or "",
                ),
            ).fetchone()
        return annotation_from_row(_rows(ANNOTATION_READ, [fila])[0])

    def delete_annotation(self, annotation_id: str, project_id: str | None = None) -> bool:
        """Acotado al proyecto, igual que en SQLite (D-121): un id ajeno no se encuentra.

        Las rutas llaman a todos los almacenes con el alcance; una firma que no lo
        aceptara reventaba con `TypeError` sólo en la nube, que es donde importa.
        """
        where, args = "id = %s", [annotation_id]
        if project_id:
            where, args = "id = %s AND project_id = %s", [annotation_id, project_id]
        with self._connect() as conn:
            cur = conn.execute(f"DELETE FROM annotations WHERE {where}", args)
            return bool(cur.rowcount)

    # -- conjuntos de casos ------------------------------------------------------------

    def create_dataset(self, dataset: Dataset, items: list[DatasetItem]) -> Dataset:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO datasets (id, project_id, name, description, created_at, "
                "source_filter) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    dataset.id,
                    dataset.project_id,
                    dataset.name,
                    dataset.description,
                    dataset.created_at,
                    json.dumps(dataset.source_filter, ensure_ascii=False),
                ),
            )
            for item in items:
                conn.execute(
                    "INSERT INTO dataset_items (id, dataset_id, trace_id, span_id, "
                    "input, expected, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        item.id,
                        dataset.id,
                        item.trace_id,
                        item.span_id,
                        json.dumps(item.input, ensure_ascii=False),
                        json.dumps(item.expected, ensure_ascii=False),
                        item.created_at,
                    ),
                )
        return dataset.model_copy(update={"item_count": len(items)})

    _DATASET_SQL = """
        SELECT d.id, d.project_id, d.name, d.description, d.created_at,
               d.source_filter, COUNT(i.id) AS n
        FROM datasets d LEFT JOIN dataset_items i ON i.dataset_id = d.id
    """

    def list_datasets(self, project_id: str) -> list[Dataset]:
        with self._connect() as conn:
            filas = conn.execute(
                self._DATASET_SQL
                + " WHERE d.project_id = %s GROUP BY d.id ORDER BY d.created_at DESC",
                (project_id,),
            ).fetchall()
        return [_dataset(f) for f in filas]

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        with self._connect() as conn:
            fila = conn.execute(
                self._DATASET_SQL + " WHERE d.id = %s GROUP BY d.id", (dataset_id,)
            ).fetchone()
        return _dataset(fila) if fila else None

    def list_dataset_items(self, dataset_id: str) -> list[DatasetItem]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT id, dataset_id, trace_id, span_id, input, expected, created_at "
                "FROM dataset_items WHERE dataset_id = %s ORDER BY created_at, id",
                (dataset_id,),
            ).fetchall()
        return [_item(f) for f in filas]

    def delete_dataset(self, dataset_id: str, project_id: str | None = None) -> bool:
        # Los casos se van con él por la clave foránea con `ON DELETE CASCADE`.
        where, args = "id = %s", [dataset_id]
        if project_id:
            where, args = "id = %s AND project_id = %s", [dataset_id, project_id]
        with self._connect() as conn:
            cur = conn.execute(f"DELETE FROM datasets WHERE {where}", args)
            return bool(cur.rowcount)

    # -- tiradas -------------------------------------------------------------------------

    def create_run(self, run: EvalRun) -> EvalRun:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_runs (id, project_id, dataset_id, variant, notes, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (run.id, run.project_id, run.dataset_id, run.variant, run.notes, run.created_at),
            )
            for item in run.items:
                conn.execute(
                    "INSERT INTO eval_run_items (run_id, case_id, trace_id, failed, error) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (run_id, case_id) DO UPDATE SET "
                    "trace_id = EXCLUDED.trace_id, failed = EXCLUDED.failed, "
                    "error = EXCLUDED.error",
                    (run.id, item.case_id, item.trace_id, item.failed, item.error),
                )
        return run

    def list_runs(self, project_id: str, dataset_id: str | None = None) -> list[EvalRun]:
        sql = (
            "SELECT id, project_id, dataset_id, variant, notes, created_at "
            "FROM eval_runs WHERE project_id = %s"
        )
        params: list[Any] = [project_id]
        if dataset_id:
            sql += " AND dataset_id = %s"
            params.append(dataset_id)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            filas = conn.execute(sql, tuple(params)).fetchall()
            runs = [_run(f) for f in filas]
            for corrida in runs:
                corrida.items = self._items(conn, corrida.id)
        return runs

    def get_run(self, run_id: str) -> EvalRun | None:
        with self._connect() as conn:
            fila = conn.execute(
                "SELECT id, project_id, dataset_id, variant, notes, created_at "
                "FROM eval_runs WHERE id = %s",
                (run_id,),
            ).fetchone()
            if fila is None:
                return None
            corrida = _run(fila)
            corrida.items = self._items(conn, run_id)
        return corrida

    @staticmethod
    def _items(conn: Any, run_id: str) -> list[EvalRunItem]:
        filas = conn.execute(
            "SELECT case_id, trace_id, failed, error FROM eval_run_items WHERE run_id = %s",
            (run_id,),
        ).fetchall()
        return [
            EvalRunItem(case_id=f[0], trace_id=f[1], failed=bool(f[2]), error=f[3] or "")
            for f in filas
        ]


    # -- anotaciones de un proyecto ----------------------------------------------------

    def annotations_since(self, project_id: str, since: Any) -> list[Annotation]:
        """Las anotaciones del proyecto desde una fecha, para el acierto por versión."""
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {ANNOTATION_READ} FROM annotations "
                f"WHERE project_id = %s AND created_at >= %s ORDER BY created_at",
                (project_id, since),
            ).fetchall()
        return [annotation_from_row(r) for r in _rows(ANNOTATION_READ, filas)]

    # -- prompts gestionados -----------------------------------------------------------

    def create_prompt(self, prompt: Prompt) -> Prompt:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO prompts (id, project_id, name, description, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    prompt.id,
                    prompt.project_id,
                    prompt.name,
                    prompt.description,
                    prompt.created_at,
                    prompt.created_at,
                ),
            )
        return prompt

    def list_prompts(self, project_id: str) -> list[Prompt]:
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {PROMPT_READ}, COUNT(v.id) AS n "
                f"FROM prompts p LEFT JOIN prompt_versions v ON v.prompt_id = p.id "
                f"WHERE p.project_id = %s GROUP BY p.id ORDER BY p.name",
                (project_id,),
            ).fetchall()
        return [prompt_from_row(r) for r in _rows(_PROMPT_ALIASES, filas)]

    def get_prompt(self, prompt_id: str, project_id: str | None = None) -> Prompt | None:
        where, args = "p.id = %s", [prompt_id]
        if project_id:
            where, args = "p.id = %s AND p.project_id = %s", [prompt_id, project_id]
        with self._connect() as conn:
            fila = conn.execute(
                f"SELECT {PROMPT_READ}, COUNT(v.id) AS n "
                f"FROM prompts p LEFT JOIN prompt_versions v ON v.prompt_id = p.id "
                f"WHERE {where} GROUP BY p.id",
                args,
            ).fetchone()
        if fila is None:
            return None
        return prompt_from_row(_rows(_PROMPT_ALIASES, [fila])[0])

    def add_prompt_version(
        self,
        prompt_id: str,
        text: str,
        *,
        notes: str = "",
        author: str = "",
        at: datetime | None = None,
    ) -> PromptVersion:
        """La siguiente versión, con el número calculado dentro de la escritura.

        `INSERT ... SELECT MAX(version) + 1` en una sola sentencia, y no leer y luego
        escribir: dos guardados a la vez escribirían la misma versión con textos
        distintos, y a partir de ahí el tráfico de una versión sería de dos textos.
        El índice único es la red debajo.
        """
        with self._connect() as conn:
            fila = conn.execute(
                "INSERT INTO prompt_versions "
                "(id, prompt_id, version, text, notes, author, created_at) "
                "SELECT %s, %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, "
                "COALESCE(%s, now()) "
                "FROM prompt_versions WHERE prompt_id = %s "
                "RETURNING id, prompt_id, version, text, notes, author, created_at",
                (new_id("pv"), prompt_id, text, notes, author, at, prompt_id),
            ).fetchone()
            conn.execute(
                "UPDATE prompts SET updated_at = now() WHERE id = %s", (prompt_id,)
            )
        return prompt_version_from_row(_rows(_VERSION_ALIASES, [fila])[0])

    def list_prompt_versions(self, prompt_id: str) -> list[PromptVersion]:
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {_VERSION_ALIASES} FROM prompt_versions "
                f"WHERE prompt_id = %s ORDER BY version DESC",
                (prompt_id,),
            ).fetchall()
        return [prompt_version_from_row(r) for r in _rows(_VERSION_ALIASES, filas)]

    def get_prompt_version(self, prompt_id: str, version: int) -> PromptVersion | None:
        with self._connect() as conn:
            fila = conn.execute(
                f"SELECT {_VERSION_ALIASES} FROM prompt_versions "
                f"WHERE prompt_id = %s AND version = %s",
                (prompt_id, int(version)),
            ).fetchone()
        if fila is None:
            return None
        return prompt_version_from_row(_rows(_VERSION_ALIASES, [fila])[0])

    def set_prompt_production(
        self,
        prompt_id: str,
        version: int,
        *,
        actor: str = "",
        note: str = "",
        at: datetime | None = None,
    ) -> PromptDeploy:
        with self._connect() as conn:
            previa = conn.execute(
                "SELECT production_version FROM prompts WHERE id = %s FOR UPDATE",
                (prompt_id,),
            ).fetchone()
            anterior = int(previa[0]) if previa and previa[0] else 0
            fila = conn.execute(
                "INSERT INTO prompt_deploys "
                "(id, prompt_id, version, actor, note, rollback, at) "
                "VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now())) "
                "RETURNING id, prompt_id, version, at, actor, note, rollback",
                (
                    new_id("dep"),
                    prompt_id,
                    int(version),
                    actor,
                    note,
                    bool(anterior and version < anterior),
                    at,
                ),
            ).fetchone()
            conn.execute(
                "UPDATE prompts SET production_version = %s, updated_at = now() WHERE id = %s",
                (int(version), prompt_id),
            )
        return prompt_deploy_from_row(_rows(_DEPLOY_ALIASES, [fila])[0])

    def list_prompt_deploys(self, prompt_id: str) -> list[PromptDeploy]:
        with self._connect() as conn:
            filas = conn.execute(
                f"SELECT {_DEPLOY_ALIASES} FROM prompt_deploys "
                f"WHERE prompt_id = %s ORDER BY at DESC",
                (prompt_id,),
            ).fetchall()
        return [prompt_deploy_from_row(r) for r in _rows(_DEPLOY_ALIASES, filas)]

    def delete_prompt(self, prompt_id: str, project_id: str | None = None) -> bool:
        where, args = "id = %s", [prompt_id]
        if project_id:
            where, args = "id = %s AND project_id = %s", [prompt_id, project_id]
        with self._connect() as conn:
            cur = conn.execute(f"DELETE FROM prompts WHERE {where}", args)
            return bool(cur.rowcount)

    # -- claves de API -------------------------------------------------------------------

    def api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            fila = conn.execute(
                "SELECT id, project_id, name, created_at, revoked_at, expires_at FROM api_keys "
                "WHERE key_hash = %s",
                (key_hash,),
            ).fetchone()
        if fila is None:
            return None
        return {
            "id": fila[0],
            "project_id": fila[1],
            "name": fila[2],
            "created_at": fila[3],
            "revoked_at": fila[4],
            "expires_at": fila[5],
        }

    def create_api_key(self, key_id: str, project_id: str, key_hash: str, name: str) -> None:
        with self._connect() as conn:
            # El proyecto tiene que existir por la clave ajena de la tabla, y una clave
            # para un proyecto que todavía no ha mandado nada es el caso normal: se crea
            # la clave ANTES de instrumentar, no después.
            conn.execute(
                "INSERT INTO projects (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (project_id, project_id),
            )
            conn.execute(
                "INSERT INTO api_keys (id, project_id, key_hash, name) "
                "VALUES (%s, %s, %s, %s)",
                (key_id, project_id, key_hash, name),
            )

    def list_api_keys(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, project_id, name, created_at, revoked_at FROM api_keys"
        params: tuple = ()
        if project_id:
            sql += " WHERE project_id = %s"
            params = (project_id,)
        with self._connect() as conn:
            filas = conn.execute(sql + " ORDER BY created_at", params).fetchall()
        return [
            {"id": f[0], "project_id": f[1], "name": f[2], "created_at": f[3], "revoked_at": f[4]}
            for f in filas
        ]

    def revoke_api_key(self, key_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
                (key_id,),
            )
            return bool(cur.rowcount)

    # -- ajustes por proyecto -------------------------------------------------------------

    def get_setting(self, project_id: str, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            fila = conn.execute(
                "SELECT value FROM settings WHERE project_id = %s AND key = %s",
                (project_id, key),
            ).fetchone()
        return _json(fila[0]) if fila else None

    def set_setting(self, project_id: str, key: str, value: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (project_id, key, value, updated_at) "
                "VALUES (%s, %s, %s::jsonb, now()) ON CONFLICT (project_id, key) "
                "DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (project_id, key, json.dumps(value, ensure_ascii=False)),
            )

    def list_settings(self, project_id: str, prefix: str = "") -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT key, value FROM settings WHERE project_id = %s "
                "AND left(key, %s) = %s ORDER BY key",
                (project_id, len(prefix), prefix),
            ).fetchall()
        return {f[0]: _json(f[1]) for f in filas}

    def delete_setting(self, project_id: str, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM settings WHERE project_id = %s AND key = %s", (project_id, key)
            )
            return bool(cur.rowcount)

    def delete_project_data(self, project_id: str) -> None:
        """Todo lo mutable de un proyecto, en una transacción. Hijos primero."""
        self._registrados.discard(project_id)
        with self._connect() as conn, conn.transaction():
            for sql in (*_BORRAR_PROYECTO, *_BORRAR_PROYECTO_PG):
                conn.execute(sql, (project_id,))


def _dataset(f: tuple) -> Dataset:
    bruto = f[5]
    return Dataset(
        id=f[0],
        project_id=f[1],
        name=f[2],
        description=f[3] or "",
        created_at=f[4],
        source_filter=bruto if isinstance(bruto, dict) else json.loads(bruto or "{}"),
        item_count=int(f[6] or 0),
    )


def _item(f: tuple) -> DatasetItem:
    return DatasetItem(
        id=f[0],
        dataset_id=f[1],
        trace_id=f[2] or "",
        span_id=f[3],
        input=f[4],
        expected=f[5],
        created_at=f[6],
    )


def _run(f: tuple) -> EvalRun:
    return EvalRun(
        id=f[0], project_id=f[1], dataset_id=f[2], variant=f[3], notes=f[4] or "", created_at=f[5]
    )


def build_metadata_store(settings: Settings) -> Any:
    """El almacén de metadatos que toque, por el mismo criterio que el de trazas.

    En local, el mismo fichero SQLite que guarda los spans: anotar es el gesto más
    básico de la pestaña de Evaluaciones y un modo local que no pudiera anotar sería
    una versión recortada (D-084). En la nube, Postgres. Si Postgres no responde, el
    nulo: se pierden las anotaciones, no la ingesta ni la lectura de trazas.
    """
    if settings.store == "sqlite":
        from .metadata import SQLiteMetadataStore

        return SQLiteMetadataStore(settings.sqlite_path)

    if not settings.postgres_enabled:
        return NullMetadataStore()
    # Postgres aunque no responda ahora mismo. Antes, un fallo en el arranque instalaba
    # el almacén nulo **para siempre**: su `api_key_by_hash` levanta, así que toda
    # petición con clave —la ingesta incluida— recibía 503 hasta que alguien reiniciara,
    # aunque Postgres volviera a los diez segundos. Cada operación abre su conexión, así
    # que en cuanto vuelve, se recupera solo.
    store = PostgresMetadataStore(settings)
    if not store.health():
        logger.warning(
            "postgres no responde al arrancar; se sigue intentando en cada operación "
            "(mientras tanto, lo que necesita metadatos contesta 503)"
        )
    return store


#: Nombre anterior, que usaban el `main` y las pruebas antes de que hubiera dos
#: implementaciones de verdad. Se mantiene para no romper importaciones.
MetadataStore = PostgresMetadataStore


def _json(value: Any) -> Any:
    """`jsonb` llega ya decodificado con psycopg; texto, si alguien lo guardó así."""
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value) if value else None
