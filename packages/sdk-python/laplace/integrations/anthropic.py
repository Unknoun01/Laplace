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
from . import _streaming as st

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
                    "content": [c.dump_model(block) for block in content],
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
            return st.wrap(response, span, _AnthropicStream(kwargs), is_async=True)
        _finish(span, kwargs, response)
        span.end()
        return response

    wrapper._laplace_patched = True  # type: ignore[attr-defined]
    wrapper._laplace_original = original  # type: ignore[attr-defined]
    return wrapper
