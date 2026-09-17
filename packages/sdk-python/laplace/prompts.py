"""Prompts gestionados: pedirlos a Laplace y dejar escrito cuál se usó.

    import laplace

    laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")
    sistema = laplace.get_prompt("resumen", fallback=SISTEMA_EN_EL_CODIGO)

    respuesta = cliente.messages.create(
        model="claude-haiku-4-5",
        system=sistema.render(idioma="es"),
        messages=[...],
    )

A partir de ahí, esa llamada queda marcada en la traza con el prompt y la versión que
la produjo, y la pestaña de Prompts puede decir lo que cuesta y lo que acierta **cada
versión** sobre el tráfico real que la usó.

Tres decisiones gobiernan este módulo:

1. **Laplace no ejecuta nada tuyo, y tampoco puede tumbarte.** Pedir el prompt aquí
   mete a Laplace en el camino caliente del agente, que es exactamente lo que D-086
   evita en la otra dirección. Por eso hay caché con caducidad, se sirve caché vencida
   antes que fallar, y existe `fallback=` para que un Laplace caído no deje al agente
   sin instrucciones (D-091).
2. **La versión que se registra es la que se usó, comprobada.** El SDK sólo marca la
   llamada si el texto de esa versión aparece de verdad en los mensajes enviados. Si
   alguien coge el texto y lo reescribe, no se marca nada: una versión mal atribuida
   ensucia las métricas de todas las demás y nadie lo notaría.
3. **El texto de reserva se cuenta como reserva.** Se registra con versión 0, no con la
   que estuviera en producción. Es tráfico real que no salió de ninguna versión
   guardada, y sumarlo a la de producción falsearía justo la cifra que se mira.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from ._http import LaplaceHTTPError, endpoint, quote, request
from ._tracer import get_config

logger = logging.getLogger("laplace")

#: Cuánto vale una copia en caché antes de volver a preguntar. Un minuto es el
#: compromiso: un rollback tarda como mucho eso en llegar a un proceso vivo, y un
#: agente con tráfico no castiga al backend con una petición por llamada.
DEFAULT_TTL_SECONDS = 60.0

#: Cuántos textos servidos se recuerdan para atribuir una llamada. Son los últimos de
#: este contexto: un agente que use más de ocho prompts distintos en un mismo paso está
#: haciendo otra cosa, y la lista no puede crecer sin fin dentro de un proceso vivo.
MAX_TRACKED = 8

#: `{{ variable }}`. Dobles llaves y no simples porque un prompt lleva JSON dentro más
#: veces de las que lleva variables, y `str.format` revienta con la primera llave.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PromptError(RuntimeError):
    """No hay prompt que servir, y tampoco texto de reserva."""


# ---------------------------------------------------------------------------------
# Lo que se devuelve
# ---------------------------------------------------------------------------------


@dataclass
class ServedPrompt:
    """Una versión de un prompt, servida y lista para usar.

    `source` dice de dónde salió el texto, y no es decorativo: `fallback` significa que
    Laplace no respondió y esto es el texto del código, así que las métricas de la
    versión de producción no deben llevárselo.
    """

    name: str
    version: int
    text: str
    prompt_id: str = ""
    #: `laplace` (recién pedido), `cache` (copia guardada) o `fallback` (el del código).
    source: str = "laplace"

    def render(self, **valores: Any) -> str:
        """Sustituye `{{ variable }}` por su valor y registra el texto resultante.

        Una variable sin valor **levanta un error**. Mandar al modelo un prompt con un
        `{{ nombre }}` literal dentro cuesta dinero y devuelve basura, y de los dos
        fallos posibles, el que se ve en el acto es infinitamente más barato.
        """
        faltan = {
            m.group(1)
            for m in _PLACEHOLDER.finditer(self.text)
            if m.group(1) not in valores
        }
        if faltan:
            raise PromptError(
                f"al prompt «{self.name}» v{self.version} le faltan variables: "
                f"{', '.join(sorted(faltan))}"
            )
        texto = _PLACEHOLDER.sub(lambda m: str(valores[m.group(1)]), self.text)
        track(self.name, self.version, texto)
        return texto

    def __str__(self) -> str:
        """El texto tal cual, para el que no tiene variables. También se registra."""
        track(self.name, self.version, self.text)
        return self.text


# ---------------------------------------------------------------------------------
# Atribución: qué versión produjo esta llamada
# ---------------------------------------------------------------------------------

#: Los textos servidos en este contexto, del más reciente al más antiguo. Es un
#: `contextvar` y no un global para que dos peticiones concurrentes del mismo proceso
#: no se atribuyan la versión la una a la otra.
_served: contextvars.ContextVar[tuple[tuple[str, int, str], ...] | None] = (
    contextvars.ContextVar("laplace_prompts_servidos", default=None)
)


def track(name: str, version: int, text: str) -> None:
    """Deja constancia de que este texto se ha servido, para poder atribuirlo luego."""
    if not text:
        return
    actual = _served.get() or ()
    entrada = (name, version, _normalize(text))
    restantes = tuple(e for e in actual if e != entrada)[: MAX_TRACKED - 1]
    _served.set((entrada, *restantes))


def attribution_for(messages: Any) -> tuple[str, int] | None:
    """Qué prompt gestionado hay dentro de estos mensajes, si hay alguno.

    Se comprueba por contenido, no por cercanía en el tiempo: el texto de la versión
    tiene que **aparecer** en lo que se ha enviado. Es más lento que fiarse del último
    `get_prompt()` y es lo correcto: un agente que pide un prompt y llama al modelo con
    otro dejaría, si nos fiásemos, una versión marcada con tráfico que no era suyo.
    """
    servidos = _served.get()
    if not servidos:
        return None
    pajar = _normalize(_text_of(messages))
    if not pajar:
        return None
    for nombre, version, texto in servidos:
        if texto and texto in pajar:
            return nombre, version
    return None


def _text_of(messages: Any) -> str:
    """Todo el texto de unos mensajes, sea cual sea la forma que traigan.

    Los dos proveedores admiten contenido como string y como lista de bloques, y el
    `system` de Anthropic llega ya unido a la lista por su integración. Se aplana todo
    en bruto: aquí sólo hace falta buscar dentro, no entender la estructura.
    """
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, dict):
        return " ".join(_text_of(v) for v in messages.values())
    if isinstance(messages, (list, tuple)):
        return " ".join(_text_of(m) for m in messages)
    return ""


def _normalize(text: str) -> str:
    """Espacios colapsados. Un prompt reindentado sigue siendo el mismo prompt."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------------
# La caché
# ---------------------------------------------------------------------------------


@dataclass
class _Entry:
    prompt: ServedPrompt
    fetched_at: float


_cache: dict[tuple[str, str], _Entry] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Vacía la caché. La usan las pruebas y quien quiera forzar un refresco."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------------
# La función pública
# ---------------------------------------------------------------------------------


def get_prompt(
    name: str,
    *,
    version: int | None = None,
    fallback: str | None = None,
    project: str | None = None,
    endpoint_url: str | None = None,
    ttl_seconds: float | None = None,
) -> ServedPrompt:
    """El prompt `name` que está en producción, o la versión concreta que se pida.

    Qué pasa cuando Laplace no responde, por orden:

    1. Si hay una copia en caché —aunque esté vencida— se sirve ésa y se avisa por el
       log. Un prompt de hace cinco minutos es infinitamente mejor que una excepción en
       mitad de la petición de un usuario final.
    2. Si no la hay pero se pasó `fallback=`, se sirve el texto del código con versión
       0, para que se vea en la pestaña que ese tráfico corrió con la reserva.
    3. Si no hay ni lo uno ni lo otro, se levanta `PromptError`. No hay una cuarta
       opción razonable: devolver un prompt vacío haría que el agente llamase al modelo
       sin instrucciones, que cuesta dinero y no lo dice nadie.
    """
    config = get_config()
    project_id = project or (getattr(config, "project", "") if config else "")
    if not project_id:
        raise PromptError(
            "no sé de qué proyecto es este prompt: pasa project= o llama a laplace.init()"
        )

    clave = (project_id, f"{name}@{version}" if version else name)
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds

    with _cache_lock:
        guardado = _cache.get(clave)
    if guardado is not None and (time.monotonic() - guardado.fetched_at) < ttl:
        return _served_copy(guardado.prompt, "cache")

    try:
        base = endpoint(endpoint_url)
        consulta = f"project_id={quote(project_id)}&name={quote(name)}"
        if version:
            consulta += f"&version={int(version)}"
        datos = request(f"{base}/api/prompts/resolve?{consulta}")
    except LaplaceHTTPError as exc:
        return _degrade(clave, name, fallback, exc)

    servido = ServedPrompt(
        name=datos["name"],
        version=int(datos["version"]),
        text=datos["text"],
        prompt_id=datos.get("prompt_id", ""),
    )
    with _cache_lock:
        _cache[clave] = _Entry(prompt=servido, fetched_at=time.monotonic())
    return _served_copy(servido, "laplace")


def _served_copy(prompt: ServedPrompt, source: str) -> ServedPrompt:
    """Una copia con su procedencia.

    Copia y no el mismo objeto: el de la caché lo comparten todas las llamadas y no
    puede llevar el `source` de la última.
    """
    copia = ServedPrompt(
        name=prompt.name,
        version=prompt.version,
        text=prompt.text,
        prompt_id=prompt.prompt_id,
        source=source,
    )
    track(copia.name, copia.version, copia.text)
    return copia


def _degrade(
    clave: tuple[str, str], name: str, fallback: str | None, exc: LaplaceHTTPError
) -> ServedPrompt:
    """Qué se sirve cuando Laplace no responde. Nunca una excepción si hay alternativa."""
    with _cache_lock:
        guardado = _cache.get(clave)
    if guardado is not None:
        logger.warning(
            "laplace: no se ha podido refrescar el prompt «%s» (%s); se sirve la copia "
            "guardada, versión %s",
            name,
            exc,
            guardado.prompt.version,
        )
        return _served_copy(guardado.prompt, "cache")

    if fallback is not None:
        logger.warning(
            "laplace: no se ha podido pedir el prompt «%s» (%s); se sirve el texto de "
            "reserva del código. Ese tráfico se registrará como reserva, no como la "
            "versión de producción",
            name,
            exc,
        )
        servido = ServedPrompt(name=name, version=0, text=fallback, source="fallback")
        track(servido.name, servido.version, servido.text)
        return servido

    raise PromptError(
        f"no se ha podido servir el prompt «{name}» y no hay texto de reserva: {exc}. "
        f"Pasa fallback= con el texto del código para que un Laplace caído no deje a "
        f"tu agente sin instrucciones."
    ) from exc


__all__ = [
    "PromptError",
    "ServedPrompt",
    "attribution_for",
    "clear_cache",
    "get_prompt",
    "track",
]
