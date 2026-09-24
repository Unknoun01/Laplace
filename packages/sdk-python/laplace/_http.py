"""El cliente HTTP mínimo del SDK contra la API de Laplace.

Vive aparte porque ya hay dos cosas que hablan con el backend desde el proceso del
usuario —las tiradas de evaluación y los prompts gestionados— y tener dos clientes
distintos sería tener dos formas distintas de fallar contra el mismo servidor.

Sin dependencias nuevas: `urllib`, como el resto del SDK. Alguien que instrumenta un
agente no tiene por qué instalarse `requests` para hacerlo.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ._tracer import get_config

logger = logging.getLogger("laplace")


class LaplaceHTTPError(RuntimeError):
    """No se ha podido hablar con Laplace, o Laplace ha dicho que no."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def endpoint(explicit: str | None = None) -> str:
    """La raíz de la API: la explícita, o la de `laplace.init()`."""
    if explicit:
        return explicit.rstrip("/")
    config = get_config()
    base = getattr(config, "endpoint", "") if config else ""
    if not base:
        raise LaplaceHTTPError(
            "no sé a qué Laplace hablar: llama antes a laplace.init(endpoint=...) o "
            "pasa endpoint= a esta función"
        )
    return str(base).rstrip("/")


def _cabeceras(con_cuerpo: bool) -> dict[str, str]:
    """Las mismas que el exportador de spans: la clave de `laplace.init()` y las propias.

    Antes no se mandaba ninguna. En local no se nota —no se pide clave—, pero en la nube
    `get_prompt()` recibía siempre un 401 y servía **en silencio** el texto de reserva:
    lo que se desplegaba desde la pestaña Prompts no llegaba nunca al agente.
    """
    config = get_config()
    cabeceras = dict(config.export_headers()) if config is not None else {}
    if con_cuerpo:
        cabeceras["Content-Type"] = "application/json"
    return cabeceras


def request(
    url: str, *, method: str = "GET", payload: Any = None, timeout: float = 30.0
) -> Any:
    """Una petición JSON. Distingue «ha dicho que no» de «no está», por el status."""
    datos = json.dumps(payload).encode("utf-8") if payload is not None else None
    peticion = urllib.request.Request(  # noqa: S310 - la URL la da quien instrumenta
        url,
        data=datos,
        headers=_cabeceras(datos is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return json.loads(respuesta.read())
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:400]
        raise LaplaceHTTPError(f"{exc.code} en {url}: {detalle}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise LaplaceHTTPError(f"no se puede hablar con Laplace en {url}: {exc}") from exc


def quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")
