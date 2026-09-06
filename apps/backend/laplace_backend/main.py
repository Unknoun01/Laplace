"""Punto de entrada del backend de Laplace.

    uvicorn laplace_backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings
from .storage.clickhouse import ClickHouseStore
from .storage.postgres import build_metadata_store

logger = logging.getLogger("laplace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )

    app.state.settings = settings
    app.state.store = ClickHouseStore(settings)
    app.state.metadata = build_metadata_store(settings)

    if settings.auto_migrate:
        app.state.store.migrate()
        app.state.metadata.migrate()

    logger.info(
        "laplace backend listo — clickhouse=%s:%s db=%s",
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
    )
    yield


app = FastAPI(
    title="Laplace",
    description="Observabilidad y optimización de agentes de IA: ingesta OTLP y lectura de trazas.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
