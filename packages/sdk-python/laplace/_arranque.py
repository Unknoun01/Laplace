"""`init()`: la única línea que el usuario tiene que escribir.

Va aparte de `_tracer` porque tiene que conocer a todos (el control remoto, las
integraciones), y todos ellos leen de `_tracer` la configuración y el tracer: con el
arranque dentro, `_tracer` y sus lectores se importaban unos a otros (D-202). El estado
sigue siendo uno y vive en `_tracer`; aquí sólo se escribe.
"""

from __future__ import annotations

import atexit
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from . import _control, _tracer, semconv
from ._filtro import Redactor
from ._tracer import logger
from .config import LaplaceConfig
from .integrations import anthropic as anthropic_integration
from .integrations import openai as openai_integration
from .version import __version__


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
    redact: Any = None,
    sample_rate: float | None = None,
    sample_keep_tokens: int | None = -1,
    sample_keep_ms: int | None = -1,
    remote_rules: bool | None = None,
) -> LaplaceConfig:
    """Configura Laplace. Llamar una vez, al arrancar el proceso.

        import laplace
        laplace.init(project="mi-agente")

    Los argumentos omitidos se leen del entorno (`LAPLACE_*`). Es idempotente:
    llamarla dos veces reconfigura, no duplica exportadores.

    `redact=True` cambia correos, teléfonos, tarjetas, IBAN, IP y claves de API por una
    marca antes de que salgan del proceso; también acepta una lista de esos detectores,
    patrones (`re.compile(...)`) o funciones `texto -> texto`. `sample_rate=0.1` guarda
    una de cada diez trazas normales y todas las que fallan, las de evaluaciones y las
    que pasan de `sample_keep_tokens` (20 000) o `sample_keep_ms`. Ver D-181.
    `remote_rules=False` deja de pedir a Laplace el tope y la parada del proyecto (D-187).
    """
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
    if redact is not None:
        cfg.redact = redact
    if sample_rate is not None:
        cfg.sample_rate = sample_rate
    if sample_keep_tokens != -1:
        cfg.sample_keep_tokens = sample_keep_tokens
    if sample_keep_ms != -1:
        cfg.sample_keep_ms = sample_keep_ms
    if remote_rules is not None:
        cfg.remote_rules = remote_rules
    if not 0 < cfg.sample_rate <= 1:
        raise ValueError(f"sample_rate tiene que estar en (0, 1]: {cfg.sample_rate}")
    # Un detector que no existe se dice al arrancar, no al primer span.
    Redactor.of(cfg.redact)

    _tracer._config = cfg

    if cfg.disabled:
        logger.debug("laplace está desactivado (LAPLACE_DISABLED)")
        return cfg

    if _tracer._provider is not None:
        _tracer._shutdown_provider()

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
    provider.add_span_processor(_tracer._build_processor(cfg, batch=batch))
    _tracer._provider = provider

    # No se pisa un proveedor que la aplicación ya haya configurado: en ese caso el
    # usuario tiene su propia canalización OTel y sólo le añadimos la nuestra.
    current = otel_trace.get_tracer_provider()
    if isinstance(current, TracerProvider) and current is not provider:
        current.add_span_processor(_tracer._build_processor(cfg, batch=batch))
        logger.info("laplace: ya existía un TracerProvider; se añade el exportador de Laplace a él")
    else:
        otel_trace.set_tracer_provider(provider)

    if cfg.flush_on_exit:
        atexit.register(_tracer._flush_at_exit)

    _auto_instrument(cfg)

    if cfg.remote_rules:
        # En segundo plano: `init()` no espera a Laplace, y sin respuesta no hay reglas.
        _control.arrancar(project=cfg.project, endpoint_url=cfg.endpoint)
    else:
        _control.reiniciar()

    logger.debug("laplace inicializado: proyecto=%s endpoint=%s", cfg.project, cfg.endpoint)
    return cfg


def _auto_instrument(cfg: LaplaceConfig) -> None:
    """Parchea los clientes de LLM presentes en el entorno.

    Es lo que hace que instrumentar sea de verdad una línea: si el usuario tiene
    `openai` o `anthropic` instalados, sus llamadas empiezan a trazarse solas.
    """
    for module in (openai_integration, anthropic_integration):
        try:
            module.instrument()
        except Exception:  # noqa: BLE001 - una integración rota no tumba el arranque
            logger.debug("laplace: no se pudo instrumentar %s", module.__name__, exc_info=True)
