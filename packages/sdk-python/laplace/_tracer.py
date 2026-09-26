"""Arranque de OpenTelemetry y estado global del SDK.

`init()` es la única línea que el usuario tiene que escribir.
"""

from __future__ import annotations

import atexit
import logging
import threading
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from . import semconv
from .config import LaplaceConfig
from .version import __version__

logger = logging.getLogger("laplace")

_config: LaplaceConfig | None = None
_provider: TracerProvider | None = None
_INSTRUMENTATION_SCOPE = "laplace"


def init(
    project: str | None = None,
    *,
    endpoint: str | None = None,
    api_key: str | None = None,
    capture_content: bool | None = None,
    service_name: str | None = None,
    service_version: str | None = None,
    max_payload_bytes: int | None = -1,
    debug: bool | None = None,
    disabled: bool | None = None,
    batch: bool = True,
    headers: dict[str, str] | None = None,
    defer_to_others: bool | None = None,
) -> LaplaceConfig:
    """Configura Laplace. Llamar una vez, al arrancar el proceso.

        import laplace
        laplace.init(project="mi-agente")

    Los argumentos omitidos se leen del entorno (`LAPLACE_*`). Es idempotente:
    llamarla dos veces reconfigura, no duplica exportadores.
    """
    global _config, _provider

    cfg = LaplaceConfig.from_env()
    if project is not None:
        cfg.project = project
    if endpoint is not None:
        cfg.endpoint = endpoint.rstrip("/")
    if api_key is not None:
        cfg.api_key = api_key
    if capture_content is not None:
        cfg.capture_content = capture_content
    if service_name is not None:
        cfg.service_name = service_name
    if service_version is not None:
        cfg.service_version = service_version
    if max_payload_bytes != -1:  # None es un valor válido (sin límite)
        cfg.max_payload_bytes = max_payload_bytes
    if debug is not None:
        cfg.debug = debug
    if disabled is not None:
        cfg.disabled = disabled
    if headers:
        cfg.headers.update(headers)
    if defer_to_others is not None:
        cfg.defer_to_others = defer_to_others

    _config = cfg

    if cfg.disabled:
        logger.debug("laplace está desactivado (LAPLACE_DISABLED)")
        return cfg

    if _provider is not None:
        _shutdown_provider()

    resource_attrs: dict[str, Any] = {
        semconv.SERVICE_NAME: cfg.service_name,
        semconv.LAPLACE_PROJECT_ID: cfg.project,
        "telemetry.sdk.name": "laplace",
        "telemetry.sdk.version": __version__,
        "telemetry.sdk.language": "python",
    }
    if cfg.service_version:
        resource_attrs[semconv.SERVICE_VERSION] = cfg.service_version

    # `shutdown_on_exit=False` a propósito: el atexit que registra OpenTelemetry por
    # su cuenta llama a `shutdown()`, que espera a que termine un envío con reintentos.
    # Si el backend no responde, eso cuelga la salida del proceso del usuario durante
    # decenas de segundos. Aquí se registra un cierre propio, con tope de tiempo.
    provider = TracerProvider(resource=Resource.create(resource_attrs), shutdown_on_exit=False)
    provider.add_span_processor(_build_processor(cfg, batch=batch))
    _provider = provider

    # No se pisa un proveedor que la aplicación ya haya configurado: en ese caso el
    # usuario tiene su propia canalización OTel y sólo le añadimos la nuestra.
    current = otel_trace.get_tracer_provider()
    if isinstance(current, TracerProvider) and current is not provider:
        current.add_span_processor(_build_processor(cfg, batch=batch))
        logger.info(
            "laplace: ya existía un TracerProvider; se añade el exportador de Laplace a él"
        )
    else:
        otel_trace.set_tracer_provider(provider)

    if cfg.flush_on_exit:
        atexit.register(_flush_at_exit)

    _auto_instrument(cfg)

    logger.debug("laplace inicializado: proyecto=%s endpoint=%s", cfg.project, cfg.endpoint)
    return cfg


def _build_processor(cfg: LaplaceConfig, *, batch: bool):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporter = OTLPSpanExporter(endpoint=cfg.traces_url, headers=cfg.export_headers())
    return BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)


def _auto_instrument(cfg: LaplaceConfig) -> None:
    """Parchea los clientes de LLM presentes en el entorno.

    Es lo que hace que instrumentar sea de verdad una línea: si el usuario tiene
    `openai` o `anthropic` instalados, sus llamadas empiezan a trazarse solas.
    """
    from .integrations import anthropic as anthropic_integration
    from .integrations import openai as openai_integration

    for module in (openai_integration, anthropic_integration):
        try:
            module.instrument()
        except Exception:  # noqa: BLE001 - una integración rota no tumba el arranque
            logger.debug("laplace: no se pudo instrumentar %s", module.__name__, exc_info=True)


def get_config() -> LaplaceConfig:
    """Configuración actual. Si nunca se llamó a `init()`, se lee del entorno."""
    global _config
    if _config is None:
        _config = LaplaceConfig.from_env()
    return _config


def get_tracer() -> otel_trace.Tracer:
    if _provider is not None:
        return _provider.get_tracer(_INSTRUMENTATION_SCOPE, __version__)
    return otel_trace.get_tracer(_INSTRUMENTATION_SCOPE, __version__)


def flush(timeout_millis: int = 10_000) -> bool:
    """Fuerza el envío de los spans pendientes. Llamar antes de salir en scripts.

    El tope de tiempo se respeta de verdad. `force_flush` de OpenTelemetry no aborta un
    envío en curso: el exportador OTLP reintenta con backoff y, contra un endpoint que
    no responde, se come decenas de segundos pasándose por alto el timeout que se le da.
    Por eso el envío ocurre en un hilo demonio y aquí sólo se espera lo pactado: si no
    llega a tiempo, se devuelve False y el proceso puede salir. Observar no puede
    retrasar lo observado, y menos aún cuando el que falla es nuestro backend.
    """
    if _provider is None:
        return True

    outcome: dict[str, bool] = {}

    def _run() -> None:
        try:
            outcome["ok"] = bool(_provider.force_flush(timeout_millis))
        except Exception:  # noqa: BLE001
            logger.debug("laplace: fallo al hacer flush", exc_info=True)
            outcome["ok"] = False

    worker = threading.Thread(target=_run, name="laplace-flush", daemon=True)
    worker.start()
    worker.join(timeout_millis / 1000)

    if worker.is_alive():
        logger.warning(
            "laplace: el envío de spans no terminó en %d ms; se continúa sin esperar",
            timeout_millis,
        )
        return False
    return outcome.get("ok", False)


def _flush_at_exit() -> None:
    """Envío final al terminar el proceso, con tope corto.

    Con el backend sano esto tarda milisegundos. Con el backend caído es lo máximo que
    la telemetría puede retrasar la salida del programa del usuario; se ajusta con
    `LAPLACE_EXIT_FLUSH_MS` si alguien prefiere esperar más para no perder el último lote.
    """
    flush(get_config().exit_flush_timeout_ms)


def shutdown() -> None:
    flush()
    _shutdown_provider()


def _shutdown_provider() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:  # noqa: BLE001
        pass
    _provider = None


def is_enabled() -> bool:
    return not get_config().disabled
