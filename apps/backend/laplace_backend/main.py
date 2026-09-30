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
from .idioma import MiddlewareIdioma
from .limites import CabecerasSeguridad, LimiteCuerpo
from .storage.base import SpanStore
from .textos import t

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


#: Identificador del candado consultivo de Postgres para el bucle de alertas. Arbitrario,
#: pero fijo: todos los procesos de una instalación tienen que pedir el mismo.
CANDADO_ALERTAS = 0x4C41504C  # «LAPL»


def build_alerts(settings: Settings, store, metadata=None):
    """El evaluador de alertas.

    Es el mismo objeto en local y en la nube: cambia dónde se guarda el estado, igual
    que cambia dónde se guardan las trazas. `laplace ui` levanta las alertas con las
    mismas variables de entorno que un despliegue (D-075).

    Se construye siempre desde D-123: los canales también se ponen en la interfaz, por
    proyecto. Sin ningún canal puesto no manda nada, que es lo que ya pasaba.
    """
    from .alerts import (
        AlertConfig,
        AlertRunner,
        EmailNotifier,
        WebhookNotifier,
        build_alert_state,
    )

    config = AlertConfig(settings)
    if settings.alerts_enabled and not config.base_url:
        logger.warning(
            "las alertas van sin enlace: falta LAPLACE_ALERTS_BASE_URL con la raíz "
            "pública de la interfaz"
        )
    # En la nube puede haber varios procesos: el turno lo reparte un candado de Postgres
    # para que cada vuelta la haga uno. En local hay uno solo y no hace falta.
    turno = None
    if settings.store != "sqlite" and settings.postgres_enabled:
        from functools import partial

        from .storage._pg import turno_exclusivo

        turno = partial(turno_exclusivo, settings.postgres_dsn, CANDADO_ALERTAS)
    runner = AlertRunner(
        store,
        config,
        build_alert_state(settings),
        turno=turno,
        metadata=metadata,
        webhook=WebhookNotifier(permitir_local=settings.store == "sqlite"),
        email=EmailNotifier(settings),
    )
    runner.retencion_instalacion = settings.retention_days
    return runner


class PreparacionMetadatos:
    """Migraciones de metadatos, tarifas propias y cuentas, hasta que salgan.

    Antes se hacía una vez al arrancar y, si Postgres no estaba todavía —un despliegue en
    el que la base arranca después que la API—, o tumbaba el arranque o dejaba la
    instalación a medias hasta reiniciar: sin tarifas propias y, en una instalación
    nueva, sin código de configuración. Ahora se intenta al arrancar y, si no sale, se
    sigue intentando en segundo plano; cada paso hecho no se repite.
    """

    def __init__(self, app: FastAPI, settings: Settings) -> None:
        self._app = app
        self._settings = settings
        self._pasos = [
            ("migrar", lambda: app.state.metadata.migrate() if settings.auto_migrate else None),
            ("tarifas", lambda: load_custom_prices(app.state.metadata)),
            ("cuentas", lambda: preparar_cuentas(app, settings)),
        ]

    def intentar(self) -> bool:
        """Da los pasos que falten. `True` si ya no queda ninguno."""
        while self._pasos:
            nombre, paso = self._pasos[0]
            try:
                paso()
            except Exception:  # noqa: BLE001
                logger.warning("los metadatos no están listos (%s); se reintentará", nombre)
                return False
            self._pasos.pop(0)
        return True

    async def hasta_que_salga(self, espera: float = 15.0) -> None:
        while not await asyncio.to_thread(self.intentar):
            await asyncio.sleep(espera)
        logger.info("metadatos listos")


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

    from .cuentas import FrenosEnBase
    from .cuentas import build as build_cuentas

    app.state.setup_token = ""
    app.state.cuentas = build_cuentas(settings) if settings.auth_enforced else None
    if app.state.cuentas is None:
        return
    try:
        app.state.cuentas.migrate()
        # Con base de cuentas, los intentos se cuentan en ella: todos los procesos ven
        # los mismos (D-130). Hasta aquí seguía el freno en memoria del arranque.
        app.state.frenos = FrenosEnBase(app.state.cuentas)
        if app.state.cuentas.hay_usuarios():
            return
    except Exception:  # noqa: BLE001
        logger.exception("no se pudo preparar la base de cuentas")
        # El que llama lo reintenta: sin esto, una instalación nueva cuyo Postgres
        # tardara en arrancar se quedaba sin código de configuración hasta reiniciar.
        raise
    app.state.setup_token = settings.setup_token or secrets.token_urlsafe(18)
    logger.warning(
        "no hay ninguna cuenta todavía. Crea la primera en /configurar con este código "
        "(sólo vale una vez): %s",
        app.state.setup_token,
    )


def load_custom_prices(metadata) -> None:
    """Las tarifas puestas desde la interfaz, al arrancar (D-123)."""
    from .pricing import set_custom_prices

    # Si no se pueden leer, se levanta: `PreparacionMetadatos` lo reintenta. Tragárselo
    # dejaba la instalación con las tarifas de fábrica en silencio hasta reiniciar.
    guardadas = metadata.get_setting("*", "prices") or {}
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

    # Lo que depende de Postgres se intenta ya; si no está, sigue en segundo plano y la
    # API arranca igual: la ingesta y la lectura de trazas no dependen de Postgres.
    from .cuentas import Frenos

    app.state.frenos = Frenos()
    app.state.cuentas = None
    app.state.setup_token = ""
    preparacion = PreparacionMetadatos(app, settings)
    listos = preparacion.intentar()

    app.state.alerts = build_alerts(settings, app.state.store, app.state.metadata)
    from .alerts import alert_loop

    tareas = [
        asyncio.create_task(alert_loop(app.state.alerts, settings.alerts_interval_seconds))
    ]
    if not listos:
        tareas.append(asyncio.create_task(preparacion.hasta_que_salga()))
    # El Diagnóstico que alguien está mirando se recalcula antes de caducar, para que
    # abrirlo no espere nunca los segundos que tarda con volumen (D-143).
    app.state.renovador_diagnostico = None
    if settings.cache_diagnostico_s > 0:
        from .api import CACHE_DIAGNOSTICO
        from .cache_diagnostico import renovar_siempre

        app.state.renovador_diagnostico = asyncio.create_task(
            renovar_siempre(CACHE_DIAGNOSTICO, settings.cache_diagnostico_s)
        )
        tareas.append(app.state.renovador_diagnostico)
    if settings.alerts_enabled:
        logger.info(
            "alertas a Slack activas — repaso cada %ds, umbral %s$, calma %sh",
            settings.alerts_interval_seconds,
            settings.alerts_min_usd,
            settings.alerts_quiet_hours,
        )
    # En ClickHouse, la retención es un TTL de la tabla (lo aplica el propio motor en sus
    # fusiones); en SQLite, un borrado diario. Se aplica también con 0, para quitar un
    # TTL puesto antes: la variable manda.
    if hasattr(app.state.store, "apply_retention"):
        try:
            await asyncio.to_thread(app.state.store.apply_retention, settings.retention_days)
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo aplicar la retención a la tabla de spans")
    elif settings.retention_days > 0:
        tareas.append(
            asyncio.create_task(retention_loop(app.state.store, settings.retention_days))
        )
    if settings.retention_days > 0:
        logger.info("retención activa — se guardan %d días", settings.retention_days)

    try:
        yield
    finally:
        for tarea in tareas:
            tarea.cancel()
        if settings.store != "sqlite":
            from .storage._pg import cerrar_todos

            cerrar_todos()


# `/docs` y `/openapi.json` sólo en modo local (D-131). En la nube quedaban fuera del
# middleware —no cuelgan de /api— y enseñaban a cualquiera el mapa entero de la API: cada
# ruta, cada parámetro y cada modelo. Quien la opera la tiene en el código y en local.
_documentar = not get_settings().auth_enforced
app = FastAPI(
    title="Laplace",
    description="Observabilidad y optimización de agentes de IA: ingesta OTLP y lectura de trazas.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _documentar else None,
    redoc_url="/redoc" if _documentar else None,
    openapi_url="/openapi.json" if _documentar else None,
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

# El último que se añade es el más externo: el tope al cuerpo corta antes de que la
# autenticación ni nadie lea una petición enorme.
app.add_middleware(LimiteCuerpo, maximo=get_settings().max_body_bytes)
app.add_middleware(CabecerasSeguridad)
# El idioma, lo primero: hasta el 413 del tope al cuerpo se dice en el idioma pedido.
app.add_middleware(MiddlewareIdioma)


@app.middleware("http")
async def _olvidar_diagnostico(request, call_next):
    """Un cambio por la API —marcar un hallazgo, una tarifa, la demo— se ve en el
    Diagnóstico en el momento, no al minuto. La ingesta va por /v1/traces y no borra: ese
    minuto de retraso es justo lo que la caché compra (D-142)."""
    respuesta = await call_next(request)
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.url.path.startswith("/api")
        and respuesta.status_code < 400
    ):
        from .api import CACHE_DIAGNOSTICO

        CACHE_DIAGNOSTICO.olvidar()
    return respuesta

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


def _segmento_next(ruta: str) -> str:
    """Dónde deja el export de Next 16 el fichero de un segmento que se pide con puntos.

    Al precargar una página, el navegador pide `trazas/__next.trazas.__PAGE__.txt`, pero
    el export lo escribe en `trazas/__next.trazas/__PAGE__.txt`: los puntos tras el
    prefijo son directorios. Next sirviéndose a sí mismo lo resuelve; un servidor de
    ficheros no, y cada enlace visible dejaba un 404. Para lo demás devuelve la ruta tal
    cual, que ya se ha probado arriba.
    """
    carpeta, _, nombre = ruta.rpartition("/")
    if not (nombre.startswith("__next.") and nombre.endswith(".txt")):
        return ruta
    partes = nombre[len("__next.") : -len(".txt")].split(".")
    if len(partes) < 2:
        return ruta
    anidado = f"__next.{partes[0]}/" + "/".join(partes[1:]) + ".txt"
    return f"{carpeta}/{anidado}" if carpeta else anidado


# `HEAD` además de `GET`: Next 16 comprueba con `HEAD` las páginas que va a precargar al
# pasar por un enlace, y sin él cada enlace visible dejaba un 405 en la consola.
@app.api_route("/{ruta:path}", methods=["GET", "HEAD"], include_in_schema=False)
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
                "detail": t("error.sin_interfaz")
            },
        )

    limpia = ruta.strip("/")
    # `/traza` -> `traza/index.html`; `/` -> `index.html`; ficheros tal cual.
    for candidato in (
        raiz / limpia if limpia else raiz / "index.html",
        raiz / limpia / "index.html" if limpia else raiz / "index.html",
        raiz / f"{limpia}.html" if limpia else raiz / "index.html",
        raiz / _segmento_next(limpia),
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
