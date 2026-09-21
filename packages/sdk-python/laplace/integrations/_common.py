"""Piezas compartidas por las integraciones con proveedores de LLM."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span as OtelSpan

from .. import _pasos, semconv
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


def enclosing_step() -> str | None:
    """Nombre del span activo, que será el padre de la llamada al modelo.

    Hay que leerlo **antes** de abrir el span de LLM: después, el span activo ya es el
    nuevo. Es la mitad de la identidad de un paso; la otra mitad son las instrucciones
    (D-060). Sin esto, todas las llamadas del agente al mismo modelo se agrupan juntas
    aunque sean pasos distintos, porque el nombre por defecto es `chat <modelo>`.
    """
    try:
        actual = trace.get_current_span()
        if actual is None or not actual.get_span_context().is_valid:
            return None
        nombre = getattr(actual, "name", None)
        return str(nombre) if nombre else None
    except Exception:  # noqa: BLE001 - nunca romper la llamada del usuario
        logger.debug("laplace: no se pudo leer el paso que envuelve", exc_info=True)
        return None


def span_name(operation: str, model: str | None) -> str:
    """Convención GenAI: `<operación> <modelo>` (ej. `chat gpt-5.6-luna`)."""
    return f"{operation} {model}" if model else operation


def record_request(
    span: OtelSpan,
    *,
    system: str,
    model: str | None,
    messages: Any,
    kwargs: dict[str, Any],
    operation: str = semconv.OPERATION_CHAT,
    enclosing: str | None = None,
) -> None:
    set_attr(span, semconv.LAPLACE_SPAN_TYPE, semconv.SPAN_TYPE_LLM)
    set_attr(span, semconv.LAPLACE_STEP_PARENT, enclosing)
    # El camino entero, no sólo el padre. Se lee de la pila de `@observe`, que este span
    # todavía no ha tocado: abrir el span de LLM no apila nada (D-106).
    set_attr(span, semconv.LAPLACE_STEP_SITE, _pasos.camino() or enclosing)
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

    record_prompt(span, messages)


def record_prompt(span: OtelSpan, messages: Any) -> None:
    """Marca la llamada con el prompt gestionado que la produjo, si lo hubo.

    Se comprueba que el texto de la versión **esté** en los mensajes enviados. No se
    deduce del último `get_prompt()`: un agente que pide un prompt y llama al modelo con
    otro texto le colgaría tráfico ajeno a esa versión, y las métricas de la pestaña de
    Prompts son justamente por versión (D-090).

    Va aquí, en el único sitio por el que pasan las dos integraciones, y no en cada una:
    un proveedor que se marcase y otro que no daría una pestaña que miente según con qué
    modelo hables.
    """
    try:
        from ..prompts import attribution_for

        atribucion = attribution_for(messages)
    except Exception:  # noqa: BLE001 - nunca romper la llamada del usuario
        logger.debug("laplace: no se pudo atribuir el prompt", exc_info=True)
        return
    if atribucion is None:
        return
    nombre, version = atribucion
    set_attr(span, semconv.LAPLACE_PROMPT_NAME, nombre)
    # La versión se escribe siempre que haya nombre, cero incluido: el cero es «se
    # sirvió el texto de reserva», que es un dato, no la ausencia de uno.
    try:
        span.set_attribute(semconv.LAPLACE_PROMPT_VERSION, int(version))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: no se pudo fijar la versión del prompt", exc_info=True)


def record_usage(
    span: OtelSpan,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cache_write_1h_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> None:
    """Tokens de entrada y de salida, siempre por separado (contrato §2).

    `input_tokens` es el total facturable de entrada, con los tokens de caché dentro
    (D-050). Cada integración normaliza a ese criterio antes de llamar aquí: OpenAI ya
    los incluye en `prompt_tokens`, Anthropic los devuelve aparte y hay que sumarlos.
    Sin esa normalización, el mismo agente costaría distinto según el proveedor.
    """
    if input_tokens is not None:
        set_attr(span, semconv.GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
    if output_tokens is not None:
        set_attr(span, semconv.GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
    if cached_input_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS, int(cached_input_tokens))
    if cache_write_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS, int(cache_write_tokens))
    if cache_write_1h_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_CACHE_WRITE_1H_TOKENS, int(cache_write_1h_tokens))
    if reasoning_tokens:
        set_attr(span, semconv.LAPLACE_USAGE_REASONING_TOKENS, int(reasoning_tokens))


def record_billing(span: OtelSpan, *, tier: str | None, region: str | None) -> None:
    """El metro de facturación que pidió la llamada, cuando la petición lo dice.

    Sólo se anota lo que se puede leer de la petición. No anotar nada significa
    estándar y global, que es lo que los dos proveedores facturan por defecto: es un
    dato, no una suposición. Lo que sí es una suposición —un extremo regional que no
    se ve desde aquí— lo marca el motor de precios como tarifa asumida.
    """
    if tier and tier != "standard":
        set_attr(span, semconv.LAPLACE_BILLING_TIER, str(tier))
    if region and region != "global":
        set_attr(span, semconv.LAPLACE_BILLING_REGION, str(region))


def cache_write_split(short: Any, long: Any, total: Any) -> tuple[int, int]:
    """Reparte los tokens escritos en caché entre duración corta y larga.

    Si el proveedor da el desglose, se usa. Si sólo da el total, va entero a la corta,
    que es la tarifa más barata de las dos: cobrar de más por una suposición nuestra
    engordaría la factura del usuario y, con ella, el ahorro que le prometemos.
    """
    corta = int(short or 0)
    larga = int(long or 0)
    if corta or larga:
        return corta, larga
    return int(total or 0), 0


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
