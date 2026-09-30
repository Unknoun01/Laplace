"""Instrumentación de los clientes de Anthropic."""

from __future__ import annotations

import functools
import importlib
import logging
from typing import Any

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanKind, Status, StatusCode

from .. import semconv
from .._tracer import get_tracer
from . import _common as c
from . import _streaming as st

logger = logging.getLogger("laplace")

_patched = False


#: Los métodos que llaman al modelo. `stream()` y `parse()` van directos a la red sin
#: pasar por `create`, así que cada uno necesita su parche y ninguno cuenta dos veces.
#: `count_tokens` no está: no genera nada ni se factura como una llamada.
_METODOS = ("create", "parse", "stream")

#: Dónde viven las clases que llaman al modelo. `client.beta.messages` es otra clase
#: que no hereda de la normal y va directa a la red: lo que sólo se pide por ahí
#: (gestión de contexto, compactación, servidores MCP, el modo rápido) no se veía.
_MODULOS = ("anthropic.resources.messages", "anthropic.resources.beta.messages")


def _clases() -> list[tuple[Any, bool]]:
    """Las clases de mensajes que existen en la versión instalada, con si son async."""
    encontradas = []
    for modulo in _MODULOS:
        try:
            mod = importlib.import_module(modulo)
        except Exception:  # noqa: BLE001 - una versión vieja sin `beta` no es un error
            continue
        for nombre, asincrono in (("Messages", False), ("AsyncMessages", True)):
            cls = getattr(mod, nombre, None)
            if cls is not None:
                encontradas.append((cls, asincrono))
    return encontradas


def instrument() -> bool:
    """Parchea `create`, `parse` y `stream` de `messages` y de `beta.messages` (sync y
    async).

    Idempotente.
    """
    global _patched
    if _patched:
        return True
    try:
        import anthropic  # noqa: F401
    except Exception:  # noqa: BLE001 - anthropic no instalado: no es un error
        return False

    for cls, asincrono in _clases():
        for metodo in _METODOS:
            original = getattr(cls, metodo, None)
            if original is None or getattr(original, "_laplace_patched", False):
                continue
            if metodo == "stream":
                envolver = _wrap_stream_async if asincrono else _wrap_stream_sync
            else:
                envolver = _wrap_async if asincrono else _wrap_sync
            setattr(cls, metodo, envolver(original))

    _patched = True
    logger.debug("laplace: anthropic instrumentado")
    return True


def uninstrument() -> None:
    """Deshace el parcheo. Pensado para tests."""
    global _patched
    for cls, _asincrono in _clases():
        for metodo in _METODOS:
            original = getattr(getattr(cls, metodo, None), "_laplace_original", None)
            if original is not None:
                setattr(cls, metodo, original)
    _patched = False


# ---------------------------------------------------------------------------------


def _input_messages(kwargs: dict[str, Any]) -> Any:
    """El system prompt de Anthropic va fuera de `messages`; se une para no perderlo."""
    messages = list(kwargs.get("messages") or [])
    system = kwargs.get("system")
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _billing(kwargs: dict[str, Any]) -> tuple[str, str]:
    """Metro y enrutado que pide la petición.

    `speed="fast"` es el modo rápido, que tiene tarifa propia. `inference_geo="us"`
    fija la residencia de datos y lleva un 1,1x. Lo que no aparezca es el defecto real
    del proveedor (estándar y global), no una suposición nuestra.
    """
    tier = "fast" if kwargs.get("speed") == "fast" else "standard"
    geo = kwargs.get("inference_geo")
    region = "regional" if geo and geo != "global" else "global"
    return tier, region


def _usage_tokens(source: Any) -> dict[str, int]:
    """Normaliza el uso de Anthropic al criterio del contrato.

    Anthropic devuelve `input_tokens` **sin** los tokens de caché: las lecturas van en
    `cache_read_input_tokens` y las escrituras en `cache_creation_input_tokens`. El
    contrato guarda el total facturable en `input_tokens` (D-050), así que aquí se
    suman. Sin esto, un agente con caché parecería costar mucho menos de lo que le
    facturan, que es justo el error que el producto no se puede permitir.
    """
    base = int(c.getattr_path(source, "usage.input_tokens", 0) or 0)
    leidos = int(c.getattr_path(source, "usage.cache_read_input_tokens", 0) or 0)
    corta, larga = c.cache_write_split(
        c.getattr_path(source, "usage.cache_creation.ephemeral_5m_input_tokens"),
        c.getattr_path(source, "usage.cache_creation.ephemeral_1h_input_tokens"),
        c.getattr_path(source, "usage.cache_creation_input_tokens"),
    )
    return {
        "input_tokens": base + leidos + corta + larga,
        "cached_input_tokens": leidos,
        "cache_write_tokens": corta,
        "cache_write_1h_tokens": larga,
    }


def _start_span(kwargs: dict[str, Any]) -> OtelSpan:
    model = kwargs.get("model")
    # Antes de abrir el span: después, el activo ya sería éste y no su padre.
    envolvente = c.enclosing_step()
    span = get_tracer().start_span(
        c.span_name(semconv.OPERATION_CHAT, model), kind=SpanKind.CLIENT
    )
    c.record_request(
        span,
        system=semconv.SYSTEM_ANTHROPIC,
        model=model,
        messages=_input_messages(kwargs),
        kwargs=kwargs,
        enclosing=envolvente,
    )
    if kwargs.get("tools"):
        c.set_attr(span, "laplace.request.tools", c.payload(kwargs["tools"]) or "")
    return span


def _contenido(bloques: Any) -> Any:
    """El texto, si todos los bloques son de texto; si no, los bloques enteros.

    Es la forma que ya salía en streaming y con OpenAI: antes, la misma llamada se
    guardaba como lista de bloques sin streaming y como texto con él, y lo que busca en
    el contenido (reglas, búsqueda, el juez) veía dos cosas distintas.
    """
    lista = list(bloques or [])
    if lista and all(getattr(b, "type", None) == "text" for b in lista):
        return "".join(getattr(b, "text", "") or "" for b in lista)
    return [c.dump_model(b) for b in lista]


def _finish(span: OtelSpan, kwargs: dict[str, Any], response: Any) -> None:
    try:
        content = getattr(response, "content", None) or []
        stop_reason = getattr(response, "stop_reason", None)
        c.record_response(
            span,
            model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            finish_reasons=[str(stop_reason)] if stop_reason else [],
            messages=[
                {
                    "role": getattr(response, "role", "assistant"),
                    "content": _contenido(content),
                }
            ],
        )
        tier, region = _billing(kwargs)
        c.record_usage(
            span,
            output_tokens=c.getattr_path(response, "usage.output_tokens"),
            **_usage_tokens(response),
        )
        c.record_billing(span, tier=tier, region=region)
        st.record_missing_usage(span, response, _input_messages(kwargs))
        span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al leer la respuesta de anthropic", exc_info=True)


class _AnthropicStream:
    """Junta los eventos de un `messages.create(stream=True)`.

    Anthropic sí manda los recuentos: `message_start` trae los tokens de entrada y
    `message_delta` los de salida, así que aquí el coste es medido, no estimado.
    """

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self._text: list[str] = []
        self._finish: list[str] = []
        self._model: str | None = kwargs.get("model")
        self._id: str | None = None
        self._role = "assistant"
        self._input: int | None = None
        self._output: int | None = None
        self._cached: int | None = None
        self._write: int = 0
        self._write_1h: int = 0
        self._tier, self._region = _billing(kwargs)
        self._fallback_input = st.estimate_messages_tokens(_input_messages(kwargs))

    def feed(self, event: Any) -> None:
        tipo = getattr(event, "type", None)

        if tipo == "message_start":
            message = getattr(event, "message", None)
            self._model = getattr(message, "model", None) or self._model
            self._id = getattr(message, "id", None) or self._id
            self._role = getattr(message, "role", None) or self._role
            tokens = _usage_tokens(message)
            # Un `message_start` sin bloque de uso no es un recuento de cero: es que no
            # hay recuento. Dejarlo en None hace que el span salga marcado como estimado
            # en lugar de decir que la llamada no gastó nada.
            if tokens["input_tokens"]:
                self._input = tokens["input_tokens"]
                self._cached = tokens["cached_input_tokens"]
                self._write = tokens["cache_write_tokens"]
                self._write_1h = tokens["cache_write_1h_tokens"]
            # Algunos modelos ya adelantan tokens de salida aquí.
            self._output = c.getattr_path(message, "usage.output_tokens", self._output)

        elif tipo == "content_block_delta":
            texto = c.getattr_path(event, "delta.text")
            if texto:
                self._text.append(str(texto))

        elif tipo == "message_delta":
            self._output = c.getattr_path(event, "usage.output_tokens", self._output)
            razon = c.getattr_path(event, "delta.stop_reason")
            if razon:
                self._finish.append(str(razon))

    def finish(self, span: OtelSpan) -> None:
        st.record_stream_result(
            span,
            system=semconv.SYSTEM_ANTHROPIC,
            text="".join(self._text),
            role=self._role,
            model=self._model,
            response_id=self._id,
            finish_reasons=self._finish,
            input_tokens=self._input,
            output_tokens=self._output,
            cached_input_tokens=self._cached,
            cache_write_tokens=self._write,
            cache_write_1h_tokens=self._write_1h,
            fallback_input_tokens=self._fallback_input,
            tier=self._tier,
            region=self._region,
        )


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if c.other_instrumentor():
            return original(self, *args, **kwargs)
        span = _start_span(kwargs)
        try:
            response = original(self, *args, **kwargs)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            span.end()
            raise
        if kwargs.get("stream"):
            # El span lo cierra el propio stream cuando termine de consumirse.
            return st.wrap(response, span, _AnthropicStream(kwargs), is_async=False)
        _finish(span, kwargs, c.readable(response, kwargs))
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if c.other_instrumentor():
            return await original(self, *args, **kwargs)
        span = _start_span(kwargs)
        try:
            response = await original(self, *args, **kwargs)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            span.end()
            raise
        if kwargs.get("stream"):
            return st.wrap(response, span, _AnthropicStream(kwargs), is_async=True)
        _finish(span, kwargs, await c.readable_async(response, kwargs))
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper


# ---------------------------------------------------------------------------------
# `messages.stream()`: el gestor de contexto
# ---------------------------------------------------------------------------------
#
# `stream()` no llama a nada: prepara la petición y devuelve un gestor que la lanza al
# entrar en el `with`. Por eso no se abre el span aquí —un gestor que nunca se abre no
# ha llamado al modelo— sino en la propia petición, que se sustituye por una que abre
# el span y devuelve el flujo crudo envuelto. `MessageStream` lee de ese flujo para
# todo (iterar, `text_stream`, `get_final_message()`), así que todos los caminos pasan
# por nuestro acumulador, y al cerrarlo se cierra el span.
#
# La petición vive en un atributo privado del gestor, con el nombre deformado por la
# clase que lo define (`_MessageStreamManager__api_request`,
# `_BetaAsyncMessageStreamManager__api_request`...): se busca por la jerarquía del
# gestor en vez de fijar los nombres, porque la beta tiene los suyos. Si una versión
# futura lo mueve, no se rompe nada del usuario: se avisa en el log y esa llamada no se
# ve. Las pruebas contra el SDK real lo detectan al actualizarlo.


def _atributo_peticion(gestor: Any) -> str | None:
    for cls in type(gestor).__mro__:
        atributo = f"_{cls.__name__.lstrip('_')}__api_request"
        if hasattr(gestor, atributo):
            return atributo
    return None


def _sustituir_peticion(gestor: Any, nueva: Any) -> None:
    atributo = _atributo_peticion(gestor)
    if atributo is None:
        logger.warning(
            "laplace: esta versión de anthropic ha cambiado messages.stream(); "
            "esas llamadas no se van a ver"
        )
        return
    original = getattr(gestor, atributo)
    setattr(gestor, atributo, nueva(original))


def _wrap_stream_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        gestor = original(self, *args, **kwargs)
        if c.other_instrumentor():
            return gestor

        def envolver(peticion: Any) -> Any:
            def lanzar() -> Any:
                span = _start_span(kwargs)
                try:
                    crudo = peticion()
                except BaseException as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
                    span.end()
                    raise
                return st.wrap(crudo, span, _AnthropicStream(kwargs), is_async=False)

            return lanzar

        try:
            _sustituir_peticion(gestor, envolver)
        except Exception:  # noqa: BLE001 - nunca romper la llamada del usuario
            logger.debug("laplace: no se pudo envolver messages.stream()", exc_info=True)
        return gestor

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper


def _wrap_stream_async(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        gestor = original(self, *args, **kwargs)
        if c.other_instrumentor():
            return gestor

        def envolver(peticion: Any) -> Any:
            # En el cliente asíncrono la petición es una corrutina ya creada que el
            # gestor espera al entrar: se sustituye por otra que la espera dentro.
            async def lanzar() -> Any:
                span = _start_span(kwargs)
                try:
                    crudo = await peticion
                except BaseException as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
                    span.end()
                    raise
                return st.wrap(crudo, span, _AnthropicStream(kwargs), is_async=True)

            return lanzar()

        try:
            _sustituir_peticion(gestor, envolver)
        except Exception:  # noqa: BLE001
            logger.debug("laplace: no se pudo envolver messages.stream()", exc_info=True)
        return gestor

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper
