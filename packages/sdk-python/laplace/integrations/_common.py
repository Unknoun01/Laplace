"""Piezas compartidas por las integraciones con proveedores de LLM."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry.trace import Span as OtelSpan

from .. import semconv
from .._tracer import get_config
from ..serialization import dumps

logger = logging.getLogger("laplace")

#: Parámetros de petición que mapean a atributos estándar de GenAI.
_PARAM_ATTRS = {
    "temperature": semconv.GEN_AI_REQUEST_TEMPERATURE,
    "top_p": semconv.GEN_AI_REQUEST_TOP_P,
    "top_k": semconv.GEN_AI_REQUEST_TOP_K,
    "max_tokens": semconv.GEN_AI_REQUEST_MAX_TOKENS,
    "max_completion_tokens": semconv.GEN_AI_REQUEST_MAX_TOKENS,
    "frequency_penalty": semconv.GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "presence_penalty": semconv.GEN_AI_REQUEST_PRESENCE_PENALTY,
    "seed": semconv.GEN_AI_REQUEST_SEED,
}


def set_attr(span: OtelSpan, key: str, value: Any) -> None:
    if value is None or value == "":
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 - nunca romper la llamada del usuario
        logger.debug("laplace: no se pudo fijar %s", key, exc_info=True)


def payload(value: Any) -> str | None:
    cfg = get_config()
    if not cfg.capture_content:
        return None
    return dumps(value, cfg.max_payload_bytes)


def span_name(operation: str, model: str | None) -> str:
    """Convención GenAI: `<operación> <modelo>` (ej. `chat gpt-4o-mini`)."""
    return f"{operation} {model}" if model else operation


def record_request(
    span: OtelSpan,
    *,
    system: str,
    model: str | None,
    messages: Any,
    kwargs: dict[str, Any],
    operation: str = semconv.OPERATION_CHAT,
) -> None:
    set_attr(span, semconv.LAPLACE_SPAN_TYPE, semconv.SPAN_TYPE_LLM)
    set_attr(span, semconv.GEN_AI_SYSTEM, system)
    set_attr(span, semconv.GEN_AI_OPERATION_NAME, operation)
    set_attr(span, semconv.GEN_AI_REQUEST_MODEL, model)

    for key, attr in _PARAM_ATTRS.items():
        if key in kwargs and kwargs[key] is not None:
            set_attr(span, attr, kwargs[key])

    stop = kwargs.get("stop") or kwargs.get("stop_sequences")
    if stop:
        set_attr(span, semconv.GEN_AI_REQUEST_STOP_SEQUENCES, [str(s) for s in _as_list(stop)])

    body = payload(messages)
    if body is not None:
        set_attr(span, semconv.GEN_AI_INPUT_MESSAGES, body)


def record_usage(
    span: OtelSpan,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> None:
    """Tokens de entrada y de salida, siempre por separado (contrato §2)."""
    if input_tokens is not None:
        set_attr(span, semconv.GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
    if output_tokens is not None:
        set_attr(span, semconv.GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
    if cached_input_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS, int(cached_input_tokens))
    if reasoning_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_REASONING_TOKENS, int(reasoning_tokens))


def record_response(
    span: OtelSpan,
    *,
    model: str | None,
    response_id: str | None,
    finish_reasons: list[str] | None,
    messages: Any,
) -> None:
    set_attr(span, semconv.GEN_AI_RESPONSE_MODEL, model)
    set_attr(span, semconv.GEN_AI_RESPONSE_ID, response_id)
    if finish_reasons:
        set_attr(span, semconv.GEN_AI_RESPONSE_FINISH_REASONS, [str(r) for r in finish_reasons])
    body = payload(messages)
    if body is not None:
        set_attr(span, semconv.GEN_AI_OUTPUT_MESSAGES, body)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def getattr_path(obj: Any, path: str, default: Any = None) -> Any:
    """`getattr_path(r, "usage.prompt_tokens_details.cached_tokens")`, tolerante a None."""
    current = obj
    for part in path.split("."):
        if current is None:
            return default
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return default if current is None else current


def dump_model(obj: Any) -> Any:
    """Convierte la respuesta del proveedor a algo plano, sin perder información."""
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:  # noqa: BLE001
                continue
    return obj
