"""Configuración del SDK.

Todo se puede fijar por variable de entorno para que instrumentar un proyecto no
obligue a tocar código de arranque en despliegues distintos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_ENDPOINT = "http://localhost:4318"

#: Tamaño máximo de un payload (prompt, respuesta, argumentos) por atributo.
#: El contrato pide payloads sin truncar, pero un atributo sin límite rompe el
#: exportador OTLP. El límite es alto y, cuando se aplica, deja una marca explícita:
#: nunca se pierde contenido en silencio. `None` = sin límite.
DEFAULT_MAX_PAYLOAD_BYTES = 1_048_576  # 1 MiB


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in {"", "none", "0"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class LaplaceConfig:
    """Configuración efectiva del SDK."""

    #: Proyecto al que pertenecen las trazas. Es la unidad de agrupación del producto.
    project: str = "default"
    #: Base OTLP/HTTP. El SDK añade `/v1/traces`.
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str | None = None

    #: Si es False no se capturan prompts, respuestas ni argumentos de tools.
    capture_content: bool = True
    max_payload_bytes: int | None = DEFAULT_MAX_PAYLOAD_BYTES

    service_name: str = "laplace-agent"
    service_version: str = ""

    #: Exporta cada span en cuanto termina. Útil en scripts cortos y en tests;
    #: en producción el batch por defecto es mucho más barato.
    flush_on_exit: bool = True
    #: Cuánto se espera, como mucho, al envío final cuando el proceso termina.
    #: Con el backend sano sobra de largo (milisegundos); con el backend caído es lo
    #: máximo que la telemetría puede retrasar la salida del programa del usuario.
    exit_flush_timeout_ms: int = 2_000
    debug: bool = False
    #: Desactiva por completo la emisión sin tocar el código instrumentado.
    disabled: bool = False

    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> LaplaceConfig:
        return cls(
            project=os.getenv("LAPLACE_PROJECT", "default"),
            endpoint=os.getenv("LAPLACE_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/"),
            api_key=os.getenv("LAPLACE_API_KEY") or None,
            capture_content=_env_bool("LAPLACE_CAPTURE_CONTENT", True),
            max_payload_bytes=_env_int(
                "LAPLACE_MAX_PAYLOAD_BYTES", DEFAULT_MAX_PAYLOAD_BYTES
            ),
            service_name=os.getenv("LAPLACE_SERVICE_NAME", "laplace-agent"),
            service_version=os.getenv("LAPLACE_SERVICE_VERSION", ""),
            exit_flush_timeout_ms=_env_int("LAPLACE_EXIT_FLUSH_MS", 2_000) or 2_000,
            debug=_env_bool("LAPLACE_DEBUG", False),
            disabled=_env_bool("LAPLACE_DISABLED", False),
        )

    @property
    def traces_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/v1/traces"

    def export_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers
