"""Estado global del SDK: la configuración, el proveedor de OpenTelemetry y su envío.

Es el fondo del grafo de imports: lo leen casi todos y no importa a ninguno de ellos.
`init()`, que necesita conocerlos a todos, está en `_arranque` (D-202).
"""

from __future__ import annotations

import logging
import threading

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from ._filtro import FiltroProcessor, Muestreo, Redactor, clave_de_redaccion
from .config import LaplaceConfig
from .version import __version__

logger = logging.getLogger("laplace")

_config: LaplaceConfig | None = None
_provider: TracerProvider | None = None
_INSTRUMENTATION_SCOPE = "laplace"


def _build_processor(cfg: LaplaceConfig, *, batch: bool):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporter = OTLPSpanExporter(endpoint=cfg.traces_url, headers=cfg.export_headers())
    procesador = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    return filtro(cfg, procesador)


def filtro(cfg: LaplaceConfig, procesador):
    """El procesador que exporta, con la redacción y el muestreo delante si hacen falta.

    Va sólo en el camino de Laplace: si la aplicación tenía su propio proveedor con
    otros exportadores, lo que mandan ellos no cambia (D-181)."""
    redactor = Redactor.of(cfg.redact, clave_de_redaccion(cfg.api_key))
    if redactor is None and cfg.sample_rate >= 1:
        return procesador
    muestreo = Muestreo(
        rate=cfg.sample_rate, keep_tokens=cfg.sample_keep_tokens, keep_ms=cfg.sample_keep_ms
    )
    return FiltroProcessor(procesador, redactor=redactor, muestreo=muestreo)


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
