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


def _start_span(kwargs: dict[str, Any]) -> OtelSpan:
    model = kwargs.get("model")
    span = get_tracer().start_span(
        c.span_name(semconv.OPERATION_CHAT, model), kind=SpanKind.CLIENT
    )
    c.record_request(
        span,
        system=semconv.SYSTEM_OPENAI,
        model=model,
        messages=kwargs.get("messages"),
        kwargs=kwargs,
    )
    if kwargs.get("tools"):
        c.set_attr(span, "laplace.request.tools", c.payload(kwargs["tools"]) or "")
    return span


def _finish(span: OtelSpan, response: Any) -> None:
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
        span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al leer la respuesta de openai", exc_info=True)


def _mark_stream(span: OtelSpan) -> None:
    # La captura de respuestas en streaming aún no está implementada (ver DECISIONS D-016):
    # se registra la llamada, sin tokens ni contenido de salida.
    c.set_attr(span, "laplace.streaming", True)
    c.set_attr(span, "laplace.streaming.captured", False)
    span.set_status(Status(StatusCode.OK))


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
            _mark_stream(span)
        else:
            _finish(span, response)
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
            _mark_stream(span)
        else:
            _finish(span, response)
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper
