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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_redact() -> object:
    """`LAPLACE_REDACT=true` (todos los detectores) o `email,phone` (sólo esos)."""
    raw = (os.getenv("LAPLACE_REDACT") or "").strip()
    if raw.lower() in {"", "0", "false", "no", "off"}:
        return None
    if raw.lower() in {"1", "true", "yes", "on", "all"}:
        return True
    return [p.strip() for p in raw.split(",") if p.strip()]


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
    #: Si hay otro instrumentador de LLM activo (OpenInference, OpenLLMetry), no se
    #: traza la llamada: ya la traza él, y contarla dos veces duplicaría el gasto (D-141).
    defer_to_others: bool = True
    #: Anota en cada llamada al modelo el fichero y la línea desde donde se hace, para
    #: poder proponer el arreglo en el código (D-185). La ruta nunca es absoluta.
    capture_code_location: bool = True
    #: Pide cada 30 s el tope, los bucles y la parada puestos en Laplace para el proyecto
    #: y los aplica como `laplace.guard` (D-187). `LAPLACE_REMOTE_RULES=false` lo apaga.
    remote_rules: bool = True

    headers: dict[str, str] = field(default_factory=dict)

    #: Redacción de datos personales antes de mandar nada (D-181): `True`, una lista de
    #: detectores (`email`, `phone`, `card`, `iban`, `ip`, `secret`), patrones o funciones.
    redact: object = None
    #: Muestreo por cola (D-181): la fracción de trazas normales que se guarda. Las que
    #: fallan, las de evaluaciones y replays y las que pasan de los topes, siempre.
    sample_rate: float = 1.0
    #: Tokens (entrada + salida de toda la traza) a partir de los que se guarda siempre.
    sample_keep_tokens: int | None = 20_000
    #: Duración en ms a partir de la que se guarda siempre. `None` no la mira.
    sample_keep_ms: int | None = None

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
            defer_to_others=_env_bool("LAPLACE_DEFER_TO_OTHERS", True),
            capture_code_location=_env_bool("LAPLACE_CAPTURE_CODE_LOCATION", True),
            remote_rules=_env_bool("LAPLACE_REMOTE_RULES", True),
            redact=_env_redact(),
            sample_rate=_env_float("LAPLACE_SAMPLE_RATE", 1.0),
            sample_keep_tokens=_env_int("LAPLACE_SAMPLE_KEEP_TOKENS", 20_000),
            sample_keep_ms=_env_int("LAPLACE_SAMPLE_KEEP_MS", None),
        )

    @property
    def traces_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/v1/traces"

    def export_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers
