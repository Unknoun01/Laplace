"""Instrumentación de los clientes de Anthropic."""

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
    """Parchea `messages.create` (sync y async). Idempotente."""
    global _patched
    if _patched:
        return True
    try:
        from anthropic.resources import messages as messages_module
    except Exception:  # noqa: BLE001 - anthropic no instalado: no es un error
        return False

    sync_cls = getattr(messages_module, "Messages", None)
    async_cls = getattr(messages_module, "AsyncMessages", None)

    if sync_cls is not None and not getattr(sync_cls.create, "_laplace_patched", False):
        sync_cls.create = _wrap_sync(sync_cls.create)
    if async_cls is not None and not getattr(async_cls.create, "_laplace_patched", False):
        async_cls.create = _wrap_async(async_cls.create)

    _patched = True
    logger.debug("laplace: anthropic instrumentado")
    return True


def uninstrument() -> None:
    """Deshace el parcheo. Pensado para tests."""
    global _patched
    try:
        from anthropic.resources import messages as messages_module
    except Exception:  # noqa: BLE001
        return
    for cls_name in ("Messages", "AsyncMessages"):
        cls = getattr(messages_module, cls_name, None)
        original = getattr(getattr(cls, "create", None), "_laplace_original", None)
        if cls is not None and original is not None:
            cls.create = original
    _patched = False


# ---------------------------------------------------------------------------------


def _input_messages(kwargs: dict[str, Any]) -> Any:
    """El system prompt de Anthropic va fuera de `messages`; se une para no perderlo."""
    messages = list(kwargs.get("messages") or [])
    system = kwargs.get("system")
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _start_span(kwargs: dict[str, Any]) -> OtelSpan:
    model = kwargs.get("model")
    span = get_tracer().start_span(
        c.span_name(semconv.OPERATION_CHAT, model), kind=SpanKind.CLIENT
    )
    c.record_request(
        span,
        system=semconv.SYSTEM_ANTHROPIC,
        model=model,
        messages=_input_messages(kwargs),
        kwargs=kwargs,
    )
    if kwargs.get("tools"):
        c.set_attr(span, "laplace.request.tools", c.payload(kwargs["tools"]) or "")
    return span


def _finish(span: OtelSpan, response: Any) -> None:
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
                    "content": [c.dump_model(block) for block in content],
                }
            ],
        )
        c.record_usage(
            span,
            input_tokens=c.getattr_path(response, "usage.input_tokens"),
            output_tokens=c.getattr_path(response, "usage.output_tokens"),
            cached_input_tokens=c.getattr_path(response, "usage.cache_read_input_tokens"),
        )
        span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al leer la respuesta de anthropic", exc_info=True)


def _mark_stream(span: OtelSpan) -> None:
    # Ver DECISIONS D-016: el streaming se registra, pero aún no se acumula.
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
