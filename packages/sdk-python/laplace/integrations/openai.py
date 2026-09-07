"""Instrumentación de los clientes de OpenAI.

Se parchea el método de la clase, no la instancia: así funciona con cualquier cliente
que el usuario cree después de `laplace.init()`, incluidos los que crean librerías de
terceros por dentro.
"""

from __future__ import annotations

import functools
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


def instrument() -> bool:
    """Parchea `chat.completions.create` (sync y async). Idempotente."""
    global _patched
    if _patched:
        return True
    try:
        from openai.resources.chat import completions as chat_completions
    except Exception:  # noqa: BLE001 - openai no instalado: no es un error
        return False

    sync_cls = getattr(chat_completions, "Completions", None)
    async_cls = getattr(chat_completions, "AsyncCompletions", None)

    if sync_cls is not None and not getattr(sync_cls.create, "_laplace_patched", False):
        sync_cls.create = _wrap_sync(sync_cls.create)
    if async_cls is not None and not getattr(async_cls.create, "_laplace_patched", False):
        async_cls.create = _wrap_async(async_cls.create)

    _patched = True
    logger.debug("laplace: openai instrumentado")
    return True


def uninstrument() -> None:
    """Deshace el parcheo. Pensado para tests."""
    global _patched
    try:
        from openai.resources.chat import completions as chat_completions
    except Exception:  # noqa: BLE001
        return
    for cls_name in ("Completions", "AsyncCompletions"):
        cls = getattr(chat_completions, cls_name, None)
        original = getattr(getattr(cls, "create", None), "_laplace_original", None)
        if cls is not None and original is not None:
            cls.create = original
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
            reasoning_tokens=c.getattr_path(
                response, "usage.completion_tokens_details.reasoning_tokens"
            ),
        )
        c.record_billing(span, tier=_tier(kwargs, response), region=None)
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
            fallback_input_tokens=self._fallback_input,
            tier=self._tier,
        )


def _wrap_sync(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
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
            return st.wrap(response, span, _OpenAIStream(kwargs), is_async=False)
        _finish(span, kwargs, response)
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        span = _start_span(kwargs)
        try:
            response = await original(self, *args, **kwargs)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            span.end()
            raise
        if kwargs.get("stream"):
            return st.wrap(response, span, _OpenAIStream(kwargs), is_async=True)
        _finish(span, kwargs, response)
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper
