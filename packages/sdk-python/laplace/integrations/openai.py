"""Instrumentación de los clientes de OpenAI.

Se parchea el método de la clase, no la instancia: así funciona con cualquier cliente
que el usuario cree después de `laplace.init()`, incluidos los que crean librerías de
terceros por dentro.
"""

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

#: Las puertas por las que se llama al modelo: `(módulo, clases, método, forma)`.
#:
#: Los ayudantes `chat.completions.stream()` y `responses.stream()` no están porque
#: llaman por dentro a `create(stream=True)`: parchearlos contaría la misma llamada dos
#: veces. `parse()` sí, porque va directo a la red sin pasar por `create`.
_PUERTAS = (
    ("openai.resources.chat.completions", ("Completions", "AsyncCompletions"), "create", "chat"),
    ("openai.resources.chat.completions", ("Completions", "AsyncCompletions"), "parse", "chat"),
    ("openai.resources.responses", ("Responses", "AsyncResponses"), "create", "responses"),
    ("openai.resources.responses", ("Responses", "AsyncResponses"), "parse", "responses"),
)


def _clases(modulo: str, nombres: tuple[str, ...]) -> list[tuple[Any, bool]]:
    """Las clases de una puerta que existen en la versión instalada, con si son async."""
    try:
        mod = importlib.import_module(modulo)
    except Exception:  # noqa: BLE001 - una versión vieja sin esa API no es un error
        return []
    encontradas = []
    for nombre in nombres:
        cls = getattr(mod, nombre, None)
        if cls is not None:
            encontradas.append((cls, nombre.startswith("Async")))
    return encontradas


def instrument() -> bool:
    """Parchea Chat Completions y la Responses API (`create` y `parse`, sync y async).

    Idempotente.
    """
    global _patched
    if _patched:
        return True
    try:
        import openai  # noqa: F401
    except Exception:  # noqa: BLE001 - openai no instalado: no es un error
        return False

    for modulo, nombres, metodo, forma in _PUERTAS:
        for cls, asincrono in _clases(modulo, nombres):
            original = getattr(cls, metodo, None)
            if original is None or getattr(original, "_laplace_patched", False):
                continue
            envolver = _wrap_async if asincrono else _wrap_sync
            setattr(cls, metodo, envolver(original, _FORMAS[forma]))

    _patched = True
    logger.debug("laplace: openai instrumentado")
    return True


def uninstrument() -> None:
    """Deshace el parcheo. Pensado para tests."""
    global _patched
    for modulo, nombres, metodo, _forma in _PUERTAS:
        for cls, _asincrono in _clases(modulo, nombres):
            original = getattr(getattr(cls, metodo, None), "_laplace_original", None)
            if original is not None:
                setattr(cls, metodo, original)
    _patched = False


# ---------------------------------------------------------------------------------


def _tier(kwargs: dict[str, Any], response: Any = None) -> str:
    """El nivel de servicio pedido, tal y como lo llama OpenAI.

    `priority` es el modo rápido, que se factura aparte. `auto` y `default` son el
    estándar. Cualquier otro (`flex`, `scale`…) se guarda tal cual: el motor de precios
    dirá que no tiene tarifa para él en lugar de cobrarlo como si fuera el normal.
    """
    valor = c.getattr_path(response, "service_tier") if response is not None else None
    valor = valor or kwargs.get("service_tier")
    if not valor or valor in ("auto", "default"):
        return "standard"
    if valor == "priority":
        return "fast"
    return str(valor)


def _start_span(kwargs: dict[str, Any]) -> OtelSpan:
    model = kwargs.get("model")
    # Antes de abrir el span: después, el activo ya sería éste y no su padre.
    envolvente = c.enclosing_step()
    span = get_tracer().start_span(
        c.span_name(semconv.OPERATION_CHAT, model), kind=SpanKind.CLIENT
    )
    c.record_request(
        span,
        system=semconv.SYSTEM_OPENAI,
        model=model,
        messages=kwargs.get("messages"),
        kwargs=kwargs,
        enclosing=envolvente,
    )
    if kwargs.get("tools"):
        c.set_attr(span, "laplace.request.tools", c.payload(kwargs["tools"]) or "")
    return span


def _finish(span: OtelSpan, kwargs: dict[str, Any], response: Any) -> None:
    """Vuelca la respuesta al span. Los tokens de entrada y salida van separados."""
    try:
        choices = getattr(response, "choices", None) or []
        c.record_response(
            span,
            model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            finish_reasons=[
                str(getattr(ch, "finish_reason", ""))
                for ch in choices
                if getattr(ch, "finish_reason", None)
            ],
            messages=[c.dump_model(getattr(ch, "message", ch)) for ch in choices],
        )
        # `prompt_tokens` de OpenAI ya incluye los tokens servidos desde caché, que es
        # justo el criterio del contrato: no hay que sumar nada aquí.
        c.record_usage(
            span,
            input_tokens=c.getattr_path(response, "usage.prompt_tokens"),
            output_tokens=c.getattr_path(response, "usage.completion_tokens"),
            cached_input_tokens=c.getattr_path(
                response, "usage.prompt_tokens_details.cached_tokens"
            ),
            # Escribir en caché se cobra por encima de la entrada —1,25x desde la
            # familia GPT-5.6— y hasta que se leyó este campo esos tokens caían en el
            # montón de «entrada normal» y se cobraban a tarifa entera, lo que dejaba
            # nuestro coste de OpenAI por DEBAJO del real. Es el tipo de error que este
            # producto no puede permitirse en su dirección: un suelo que no era suelo.
            cache_write_tokens=c.getattr_path(
                response, "usage.prompt_tokens_details.cache_write_tokens"
            ),
            reasoning_tokens=c.getattr_path(
                response, "usage.completion_tokens_details.reasoning_tokens"
            ),
        )
        c.record_billing(span, tier=_tier(kwargs, response), region=None)
        st.record_missing_usage(span, response, kwargs.get("messages"))
        span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al leer la respuesta de openai", exc_info=True)


class _OpenAIStream:
    """Junta los trozos de un `chat.completions` en streaming.

    OpenAI sólo manda el recuento de tokens si la petición lleva
    `stream_options={"include_usage": True}`. No se inyecta por nuestra cuenta: añadir
    un chunk final que el código del usuario no espera es justo el tipo de cosa que un
    SDK de observabilidad no puede permitirse. Sin ese recuento, se estima y se marca.
    """

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self._text: list[str] = []
        self._finish: list[str] = []
        self._model: str | None = kwargs.get("model")
        self._id: str | None = None
        self._input: int | None = None
        self._output: int | None = None
        self._cached: int | None = None
        self._cache_write: int | None = None
        self._tier = _tier(kwargs)
        self._fallback_input = st.estimate_messages_tokens(kwargs.get("messages"))

    def feed(self, chunk: Any) -> None:
        self._model = getattr(chunk, "model", None) or self._model
        self._id = getattr(chunk, "id", None) or self._id

        for choice in getattr(chunk, "choices", None) or []:
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if content:
                self._text.append(content)
            reason = getattr(choice, "finish_reason", None)
            if reason:
                self._finish.append(str(reason))

        tier = getattr(chunk, "service_tier", None)
        if tier:
            self._tier = _tier({"service_tier": tier})

        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self._input = c.getattr_path(usage, "prompt_tokens", self._input)
            self._output = c.getattr_path(usage, "completion_tokens", self._output)
            self._cached = c.getattr_path(
                usage, "prompt_tokens_details.cached_tokens", self._cached
            )
            self._cache_write = c.getattr_path(
                usage, "prompt_tokens_details.cache_write_tokens", self._cache_write
            )

    def finish(self, span: OtelSpan) -> None:
        st.record_stream_result(
            span,
            system=semconv.SYSTEM_OPENAI,
            text="".join(self._text),
            role="assistant",
            model=self._model,
            response_id=self._id,
            finish_reasons=self._finish,
            input_tokens=self._input,
            output_tokens=self._output,
            cached_input_tokens=self._cached,
            cache_write_tokens=self._cache_write,
            fallback_input_tokens=self._fallback_input,
            tier=self._tier,
        )


# ---------------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------------


def _texto(content: Any) -> Any:
    """Aplana una lista de partes con texto (`input_text`, `output_text`) a una cadena.

    Si alguna parte no es texto —una imagen, una negativa—, se deja como vino: aplanar
    sólo lo que se puede es mejor que perder lo que no.
    """
    if isinstance(content, list) and content:
        partes = [c.dump_model(p) for p in content]
        if all(isinstance(p, dict) and isinstance(p.get("text"), str) for p in partes):
            return "".join(p["text"] for p in partes)
        return partes
    return content


def _responses_input(kwargs: dict[str, Any]) -> list[Any]:
    """`instructions` + `input`, como una lista de mensajes.

    Las instrucciones van fuera de `input`, como el `system` de Anthropic, y son la
    mitad de la identidad del paso: se unen como mensaje de sistema. Los elementos que
    no son mensajes (el resultado de una herramienta, una referencia) se guardan tal
    cual.
    """
    mensajes: list[Any] = []
    instrucciones = kwargs.get("instructions")
    if instrucciones:
        mensajes.append({"role": "system", "content": instrucciones})
    entrada = kwargs.get("input")
    if isinstance(entrada, str):
        mensajes.append({"role": "user", "content": entrada})
    elif isinstance(entrada, (list, tuple)):
        for item in entrada:
            datos = c.dump_model(item)
            if isinstance(datos, dict) and "role" in datos:
                mensajes.append({"role": datos["role"], "content": _texto(datos.get("content"))})
            else:
                mensajes.append(datos)
    return mensajes


def _responses_output(response: Any) -> list[Any]:
    """Los mensajes de la respuesta como texto; las llamadas a herramientas, enteras."""
    salida: list[Any] = []
    for item in c.getattr_path(response, "output") or []:
        datos = c.dump_model(item)
        if isinstance(datos, dict) and datos.get("type") == "message":
            salida.append(
                {"role": datos.get("role") or "assistant", "content": _texto(datos.get("content"))}
            )
        else:
            salida.append(datos)
    return salida


def _responses_finish_reasons(response: Any) -> list[str]:
    """El motivo del corte si lo hay (`max_output_tokens`), y si no, el estado."""
    valor = c.getattr_path(response, "incomplete_details.reason") or c.getattr_path(
        response, "status"
    )
    return [str(valor)] if valor else []


def _responses_usage(response: Any) -> dict[str, Any]:
    """El uso de la Responses API. Como en Chat, `input_tokens` ya incluye lo servido
    desde caché, así que no hay que sumar nada (D-050)."""
    return {
        "input_tokens": c.getattr_path(response, "usage.input_tokens"),
        "output_tokens": c.getattr_path(response, "usage.output_tokens"),
        "cached_input_tokens": c.getattr_path(
            response, "usage.input_tokens_details.cached_tokens"
        ),
        "cache_write_tokens": c.getattr_path(
            response, "usage.input_tokens_details.cache_write_tokens"
        ),
        "reasoning_tokens": c.getattr_path(
            response, "usage.output_tokens_details.reasoning_tokens"
        ),
    }


def _responses_start_span(kwargs: dict[str, Any]) -> OtelSpan:
    model = kwargs.get("model")
    envolvente = c.enclosing_step()
    span = get_tracer().start_span(
        c.span_name(semconv.OPERATION_CHAT, model), kind=SpanKind.CLIENT
    )
    c.record_request(
        span,
        system=semconv.SYSTEM_OPENAI,
        model=model,
        messages=_responses_input(kwargs),
        kwargs=kwargs,
        enclosing=envolvente,
    )
    if kwargs.get("tools"):
        c.set_attr(span, "laplace.request.tools", c.payload(kwargs["tools"]) or "")
    return span


def _responses_finish(span: OtelSpan, kwargs: dict[str, Any], response: Any) -> None:
    try:
        c.record_response(
            span,
            model=c.getattr_path(response, "model"),
            response_id=c.getattr_path(response, "id"),
            finish_reasons=_responses_finish_reasons(response),
            messages=_responses_output(response),
        )
        c.record_usage(span, **_responses_usage(response))
        c.record_billing(span, tier=_tier(kwargs, response), region=None)
        st.record_missing_usage(span, response, _responses_input(kwargs))
        span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al leer la respuesta de openai", exc_info=True)


#: Los eventos que cierran un flujo de la Responses API, con la respuesta entera dentro.
_EVENTOS_FINALES = ("response.completed", "response.incomplete", "response.failed")


class _ResponsesStream:
    """Junta los eventos de un `responses.create(stream=True)`.

    Aquí no hace falta pedir el uso como en Chat: el evento final trae la respuesta
    entera con su `usage`. Sólo si el flujo se corta antes de ese evento, o si llega
    sin uso, se estima y se marca.
    """

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self._text: list[str] = []
        self._model: str | None = kwargs.get("model")
        self._id: str | None = None
        self._final: Any = None
        self._kwargs = kwargs
        self._fallback_input = st.estimate_messages_tokens(_responses_input(kwargs))

    def feed(self, event: Any) -> None:
        tipo = getattr(event, "type", None)
        respuesta = getattr(event, "response", None)
        if respuesta is not None:
            self._model = c.getattr_path(respuesta, "model") or self._model
            self._id = c.getattr_path(respuesta, "id") or self._id
        if tipo == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                self._text.append(str(delta))
        elif tipo in _EVENTOS_FINALES:
            self._final = respuesta

    def finish(self, span: OtelSpan) -> str | None:
        final = self._final
        uso = _responses_usage(final) if final is not None else {}
        st.record_stream_result(
            span,
            system=semconv.SYSTEM_OPENAI,
            text="".join(self._text),
            role="assistant",
            model=self._model,
            response_id=self._id,
            finish_reasons=_responses_finish_reasons(final) if final is not None else [],
            input_tokens=uso.get("input_tokens"),
            output_tokens=uso.get("output_tokens"),
            cached_input_tokens=uso.get("cached_input_tokens"),
            cache_write_tokens=uso.get("cache_write_tokens"),
            reasoning_tokens=uso.get("reasoning_tokens"),
            fallback_input_tokens=self._fallback_input,
            tier=_tier(self._kwargs, final),
            # La respuesta final trae también las llamadas a herramientas, que el texto
            # acumulado no ve.
            messages=_responses_output(final) if c.getattr_path(final, "output") else None,
        )
        if c.getattr_path(final, "status") == "failed":
            return str(c.getattr_path(final, "error.message") or "la respuesta falló")
        return None


class _Forma:
    """Cómo se abre, se cierra y se lee en streaming el span de cada API."""

    def __init__(self, start: Any, finish: Any, stream: Any) -> None:
        self.start = start
        self.finish = finish
        self.stream = stream


_FORMAS = {
    "chat": _Forma(_start_span, _finish, _OpenAIStream),
    "responses": _Forma(_responses_start_span, _responses_finish, _ResponsesStream),
}


# ---------------------------------------------------------------------------------


def _wrap_sync(original: Any, forma: _Forma) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        span = forma.start(kwargs)
        try:
            response = original(self, *args, **kwargs)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            span.end()
            raise
        if kwargs.get("stream"):
            # El span lo cierra el propio stream cuando termine de consumirse.
            return st.wrap(response, span, forma.stream(kwargs), is_async=False)
        forma.finish(span, kwargs, c.readable(response, kwargs))
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper


def _wrap_async(original: Any, forma: _Forma) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        span = forma.start(kwargs)
        try:
            response = await original(self, *args, **kwargs)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            span.end()
            raise
        if kwargs.get("stream"):
            return st.wrap(response, span, forma.stream(kwargs), is_async=True)
        forma.finish(span, kwargs, await c.readable_async(response, kwargs))
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper
