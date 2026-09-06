"""Metadatos en Postgres: proyectos, claves y los huecos reservados.

Las trazas viven en ClickHouse (inmutables, analíticas). Aquí vive lo mutable y
relacional. Los lectores de `annotations` y `trace_diagnoses` ya existen y devuelven
vacío: cuando lleguen las fases 3 y 4 sólo hay que escribir en ellas, la API de lectura
y el frontend ya las contemplan.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import psycopg
from laplace.schema import Annotation, Diagnosis, Project

from ..config import Settings

logger = logging.getLogger("laplace.storage.postgres")

_SCHEMA = Path(__file__).with_name("postgres_schema.sql")


class MetadataStore:
    """Acceso a Postgres. Una conexión por operación: se usa poco y así no hay estado."""

    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.postgres_dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, autocommit=True)

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
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (project_id, project_id),
            )

    # -- huecos reservados ----------------------------------------------------------

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

    def list_annotations(self, trace_id: str) -> list[Annotation]:
        """Anotaciones de la traza y de sus spans (Fase 4). Hoy siempre vacío."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, trace_id, span_id, source, verdict, score, label, comment,
                       author, created_at
                FROM annotations WHERE trace_id = %s ORDER BY created_at
                """,
                (trace_id,),
            ).fetchall()
        return [
            Annotation(
                id=r[0],
                trace_id=r[1],
                span_id=r[2],
                source=r[3],
                verdict=r[4],
                score=r[5],
                label=r[6],
                comment=r[7],
                author=r[8],
                created_at=r[9],
            )
            for r in rows
        ]


class NullMetadataStore:
    """Sustituto cuando Postgres no está disponible.

    La ingesta y la vista de trazas no pueden caerse porque falte la base de metadatos:
    lo único que se pierde son los huecos reservados, que hoy están vacíos igualmente.
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

    def list_annotations(self, trace_id: str) -> list[Annotation]:
        return []


def build_metadata_store(settings: Settings) -> Any:
    if not settings.postgres_enabled:
        return NullMetadataStore()
    store = MetadataStore(settings)
    if not store.health():
        logger.warning("postgres no disponible; se sigue sin metadatos")
        return NullMetadataStore()
    return store
