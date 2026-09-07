"""Punto de entrada del backend de Laplace.

    uvicorn laplace_backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .api import router
from .config import Settings, get_settings
from .storage.base import SpanStore

logger = logging.getLogger("laplace")


def build_store(settings: Settings) -> SpanStore:
    """El almacén que toque, importado sólo cuando toca.

    El import es perezoso a propósito: `clickhouse-connect` y `psycopg` son
    dependencias opcionales del paquete (extra `cloud`), porque el modo local no las
    necesita y arrastrarlas a un `pip install` para ver una traza en el portátil sería
    cobrarle al usuario diez megas por nada (D-068).
    """
    if settings.store == "sqlite":
        from .storage.sqlite import SQLiteStore

        return SQLiteStore(settings.sqlite_path)

    from .storage.clickhouse import ClickHouseStore

    return ClickHouseStore(settings)


def build_metadata(settings: Settings):
    """Postgres sólo guarda metadatos y los huecos de las Fases 3-4.

    En local no hay Postgres y no hace falta: se devuelve el almacén nulo, que es el
    mismo que ya se usaba cuando Postgres no estaba disponible.
    """
    from .storage.postgres import build_metadata_store

    return build_metadata_store(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )

    app.state.settings = settings
    app.state.store = build_store(settings)
    app.state.metadata = build_metadata(settings)

    if settings.auto_migrate:
        app.state.store.migrate()
        app.state.metadata.migrate()

    destino = (
        settings.sqlite_path
        if settings.store == "sqlite"
        else f"{settings.clickhouse_host}:{settings.clickhouse_port}/{settings.clickhouse_database}"
    )
    logger.info("laplace backend listo — almacén=%s (%s)", settings.store, destino)
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

# El router va ANTES del comodín de la interfaz: FastAPI resuelve por orden de
# registro, y un `/{ruta:path}` declarado primero se comería `/api` y `/health`.
app.include_router(router)


def _ui_dir() -> Path | None:
    """Dónde están los ficheros de la interfaz, si están.

    Se busca primero dentro del paquete del SDK —que es donde los mete el build de
    distribución— y luego en el árbol del repositorio, para que `laplace ui` funcione
    en desarrollo sin instalar nada. Si no hay ninguno, la API sigue en pie y sólo
    falta la interfaz: se dice cómo construirla en lugar de servir un 404 mudo.
    """
    candidatos = []
    try:
        import laplace

        candidatos.append(Path(laplace.__file__).parent / "ui")
    except Exception:  # noqa: BLE001
        pass
    candidatos.append(Path(__file__).resolve().parents[3] / "apps" / "web" / "out")
    return next((c for c in candidatos if (c / "index.html").exists()), None)


@app.get("/{ruta:path}", include_in_schema=False)
def interfaz(ruta: str) -> Response:
    """Sirve la interfaz estática desde el mismo origen que la API.

    Es lo que permite que `laplace ui` sea un solo proceso de Python: la misma
    aplicación de Next que se despliega en la nube, exportada a HTML, servida aquí. Sin
    esto, el modo local necesitaría Node y dejaría de caber en un `pip install` (D-069).
    """
    raiz = _ui_dir()
    if raiz is None:
        return JSONResponse(
            status_code=501,
            content={
                "detail": (
                    "La API está en pie pero no encuentro la interfaz. Constrúyela con "
                    "`LAPLACE_EXPORT=1 npm run build` en apps/web, o instala una versión "
                    "publicada de laplace-trace[ui]."
                )
            },
        )

    limpia = ruta.strip("/")
    # `/traza` -> `traza/index.html`; `/` -> `index.html`; ficheros tal cual.
    for candidato in (
        raiz / limpia if limpia else raiz / "index.html",
        raiz / limpia / "index.html" if limpia else raiz / "index.html",
        raiz / f"{limpia}.html" if limpia else raiz / "index.html",
    ):
        # Nunca salir de la carpeta de la interfaz, pase lo que pase con la ruta.
        try:
            resuelto = candidato.resolve()
            resuelto.relative_to(raiz.resolve())
        except (ValueError, OSError):
            continue
        if resuelto.is_file():
            return FileResponse(resuelto)

    return FileResponse(raiz / "404.html", status_code=404)
