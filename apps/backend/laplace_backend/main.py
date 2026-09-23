"""Punto de entrada del backend de Laplace.

    uvicorn laplace_backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .api import router
from .api_ajustes import router as ajustes_router
from .api_cuentas import router as cuentas_router
from .api_evals import router as evals_router
from .api_prompts import router as prompts_router
from .auth import AuthMiddleware
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
    """Lo mutable —proyectos, anotaciones, conjuntos y tiradas— va donde toque.

    En local, el mismo fichero SQLite que guarda los spans; en la nube, Postgres. Antes
    en local se devolvía el almacén nulo, porque los huecos de las fases 3 y 4 estaban
    vacíos y no se notaba. Con las evaluaciones sí se nota: anotar una traza es el gesto
    más básico de la pestaña, y un modo local que no pudiera anotar sería una versión
    recortada del producto (D-084).
    """
    from .storage.postgres import build_metadata_store

    return build_metadata_store(settings)


def build_alerts(settings: Settings, store, metadata=None):
    """El evaluador de alertas.

    Es el mismo objeto en local y en la nube: cambia dónde se guarda el estado, igual
    que cambia dónde se guardan las trazas. `laplace ui` levanta las alertas con las
    mismas variables de entorno que un despliegue (D-075).

    Se construye siempre desde D-123: los canales también se ponen en la interfaz, por
    proyecto. Sin ningún canal puesto no manda nada, que es lo que ya pasaba.
    """
    from .alerts import AlertConfig, AlertRunner, EmailNotifier, build_alert_state

    config = AlertConfig(settings)
    if settings.alerts_enabled and not config.base_url:
        logger.warning(
            "las alertas van sin enlace: falta LAPLACE_ALERTS_BASE_URL con la raíz "
            "pública de la interfaz"
        )
    return AlertRunner(
        store,
        config,
        build_alert_state(settings),
        metadata=metadata,
        email=EmailNotifier(settings),
    )


async def retention_loop(store, days: int) -> None:
    """Borra lo que pasa de `days` días, una vez al día (D-123)."""
    from datetime import datetime, timedelta, timezone

    while True:
        try:
            corte = datetime.now(timezone.utc) - timedelta(days=days)
            await asyncio.to_thread(store.delete_before, corte)
            logger.info("retención: borrados los spans anteriores a %s", corte.date())
            await asyncio.sleep(86400)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("fallo en la retención; se reintenta en una hora")
            await asyncio.sleep(3600)


def preparar_cuentas(app: FastAPI, settings: Settings) -> None:
    """Cuentas en la nube (D-127): el almacén, el freno de intentos y, si todavía no hay
    ninguna persona, el código de un solo uso para crear la primera.

    El código va al log y no a la pantalla: quien puede leer el log de un despliegue es
    quien lo administra, y quien llega a la URL no tiene por qué serlo.
    """
    import secrets

    from .cuentas import Frenos
    from .cuentas import build as build_cuentas

    app.state.frenos = Frenos()
    app.state.setup_token = ""
    app.state.cuentas = build_cuentas(settings) if settings.auth_enforced else None
    if app.state.cuentas is None:
        return
    try:
        app.state.cuentas.migrate()
        if app.state.cuentas.hay_usuarios():
            return
    except Exception:  # noqa: BLE001
        logger.exception("no se pudo preparar la base de cuentas")
        return
    app.state.setup_token = settings.setup_token or secrets.token_urlsafe(18)
    logger.warning(
        "no hay ninguna cuenta todavía. Crea la primera en /configurar con este código "
        "(sólo vale una vez): %s",
        app.state.setup_token,
    )


def load_custom_prices(metadata) -> None:
    """Las tarifas puestas desde la interfaz, al arrancar (D-123)."""
    from .pricing import set_custom_prices

    try:
        guardadas = metadata.get_setting("*", "prices") or {}
    except Exception:  # noqa: BLE001
        logger.warning("no se pueden leer las tarifas propias", exc_info=True)
        return
    if guardadas.get("models"):
        set_custom_prices(guardadas["models"])
        logger.info("tarifas propias cargadas — %d modelos", len(guardadas["models"]))


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
    logger.info(
        "metadatos (anotaciones, conjuntos, tiradas) en %s",
        type(app.state.metadata).__name__,
    )
    _log_auth(settings)
    _log_ui_dir()

    # El juez se construye siempre, encendido o no: la pestaña de Evaluaciones
    # pregunta por su estado para decir si está disponible en vez de ofrecer un botón
    # que no hace nada.
    from .judge import JudgeConfig

    app.state.judge = JudgeConfig.of(settings)
    if app.state.judge.enabled:
        logger.info("LLM-as-judge activo — modelo=%s", app.state.judge.model)

    load_custom_prices(app.state.metadata)
    preparar_cuentas(app, settings)

    app.state.alerts = build_alerts(settings, app.state.store, app.state.metadata)
    from .alerts import alert_loop

    tareas = [
        asyncio.create_task(alert_loop(app.state.alerts, settings.alerts_interval_seconds))
    ]
    if settings.alerts_enabled:
        logger.info(
            "alertas a Slack activas — repaso cada %ds, umbral %s$, calma %sh",
            settings.alerts_interval_seconds,
            settings.alerts_min_usd,
            settings.alerts_quiet_hours,
        )
    if settings.retention_days > 0:
        tareas.append(
            asyncio.create_task(retention_loop(app.state.store, settings.retention_days))
        )
        logger.info("retención activa — se guardan %d días", settings.retention_days)

    try:
        yield
    finally:
        for tarea in tareas:
            tarea.cancel()


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

# Autenticación: deniega por defecto todo lo que cuelga de /api y /v1/traces salvo la
# lista blanca de `auth.py`. Va como middleware y no como dependencia de cada ruta a
# propósito: una ruta nueva nace protegida en vez de nacer abierta hasta que alguien se
# acuerde de protegerla (D-097).
app.add_middleware(
    AuthMiddleware,
    required=get_settings().auth_enforced,
    metadata_getter=lambda: app.state.metadata,
    cuentas_getter=lambda: getattr(app.state, "cuentas", None),
)

# Los routers van ANTES del comodín de la interfaz: FastAPI resuelve por orden de
# registro, y un `/{ruta:path}` declarado primero se comería `/api` y `/health`.
app.include_router(router)
app.include_router(evals_router)
app.include_router(prompts_router)
app.include_router(ajustes_router)
app.include_router(cuentas_router)


def _log_auth(settings: Settings) -> None:
    """Deja escrito en qué modo arranca la instalación.

    Un despliegue sin credencial tiene que doler de ver en el log, y el modo local tiene
    que decir que no pide clave **a propósito**. La diferencia entre una exención escrita
    y un descuido es exactamente ésta: que alguien lo lea el día que mueva el modo local
    a una máquina con IP pública.
    """
    if settings.auth_enforced:
        logger.info("autenticación activa: /api y /v1/traces exigen clave de API")
        return
    if settings.store == "sqlite":
        logger.info(
            "modo local sin cuentas (D-010): no se pide clave. Si expones esto fuera de "
            "tu máquina, arranca con LAPLACE_AUTH_REQUIRED=true y crea claves con "
            "`python -m laplace_backend.keys create --project <id>`"
        )
        return
    logger.warning(
        "AUTENTICACIÓN DESACTIVADA en un despliegue de nube: cualquiera que llegue a "
        "esta URL puede leer y escribir todos los proyectos, incluidos los prompts y "
        "las respuestas en crudo. Quita LAPLACE_AUTH_REQUIRED=false para cerrarlo."
    )


def _ui_candidates() -> list[Path]:
    """Los sitios donde puede estar la interfaz, **en orden de preferencia**."""
    candidatos = [Path(__file__).resolve().parents[3] / "apps" / "web" / "out"]
    try:
        import laplace

        candidatos.append(Path(laplace.__file__).parent / "ui")
    except Exception:  # noqa: BLE001
        pass
    return candidatos


def _ui_dir() -> Path | None:
    """Dónde están los ficheros de la interfaz, si están.

    **El árbol del repositorio gana a la copia del paquete**, y ese orden importa. Al
    revés —que era como estaba— cualquier `scripts/build_ui.py` ejecutado alguna vez
    dejaba una copia en `packages/sdk-python/laplace/ui` que **tapaba en silencio** los
    `next build` posteriores: se desarrolla contra una interfaz vieja sin ningún aviso,
    y sólo se nota cuando algo que acabas de escribir no aparece. Ya costó un rato una
    vez.

    Invertirlo no crea conflicto en producción: dentro de un wheel `apps/web/out` no
    existe, así que allí sólo hay un candidato. Y para no dejarlo a la fe, al arrancar
    se dice por el log qué directorio se está usando (`_log_ui_dir`).

    Si no hay ninguno, la API sigue en pie y sólo falta la interfaz: se dice cómo
    construirla en lugar de servir un 404 mudo.
    """
    return next((c for c in _ui_candidates() if (c / "index.html").exists()), None)


def _log_ui_dir() -> None:
    """Deja escrito al arrancar qué build se está sirviendo.

    Saber eso es la diferencia entre depurar un cambio y depurar una copia vieja, y si
    hay dos copias construidas lo dice: la que no se usa es exactamente la que va a
    confundir a alguien dentro de tres semanas.
    """
    candidatos = _ui_candidates()
    elegido = _ui_dir()
    if elegido is None:
        logger.warning(
            "no encuentro la interfaz construida en %s", " ni ".join(map(str, candidatos))
        )
        return
    logger.info("interfaz servida desde %s", elegido)
    otras = [c for c in candidatos if c != elegido and (c / "index.html").exists()]
    if otras:
        logger.warning(
            "hay otra copia construida que NO se está usando: %s",
            ", ".join(str(c) for c in otras),
        )


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
            # Sin `Cache-Control`, el navegador cachea el HTML por heurística a partir
            # de `Last-Modified`, y tras actualizar Laplace se seguía viendo la
            # interfaz anterior sin ningún aviso. El HTML se revalida siempre; lo de
            # `_next/static` lleva hash en el nombre y puede guardarse para siempre.
            cache = (
                "public, max-age=31536000, immutable"
                if "_next/static/" in resuelto.as_posix()
                else "no-cache"
            )
            return FileResponse(resuelto, headers={"Cache-Control": cache})

    return FileResponse(raiz / "404.html", status_code=404)
