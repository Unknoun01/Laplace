"""API pública de instrumentación manual: `@observe` y `laplace.span(...)`."""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import _pasos, semconv
from ._tracer import get_config, get_tracer
from .serialization import dumps

logger = logging.getLogger("laplace")

F = TypeVar("F", bound=Callable[..., Any])

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "laplace_session_id", default=None
)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "laplace_user_id", default=None
)


def set_context(
    *, session_id: str | None = None, user_id: str | None = None
) -> None:
    """Fija sesión y usuario final para todos los spans que se abran a partir de aquí.

    Se propaga por `contextvars`, así que funciona igual en async y en hilos creados
    después de la llamada.
    """
    if session_id is not None:
        _session_id.set(session_id)
    if user_id is not None:
        _user_id.set(user_id)


def get_current_trace_id() -> str | None:
    """`trace_id` en hexadecimal de la traza en curso, o `None` si no hay ninguna."""
    ctx = otel_trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


# ---------------------------------------------------------------------------------
# Atributos
# ---------------------------------------------------------------------------------


def _payload(value: Any) -> str | None:
    cfg = get_config()
    if not cfg.capture_content:
        return None
    return dumps(value, cfg.max_payload_bytes)


def _set(span: OtelSpan, key: str, value: Any) -> None:
    if value is None or value == "":
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        logger.debug("laplace: no se pudo fijar el atributo %s", key, exc_info=True)


def _apply_common(
    span: OtelSpan,
    *,
    span_type: str,
    session_id: str | None,
    user_id: str | None,
    tags: list[str] | None,
    metadata: dict[str, Any] | None,
) -> None:
    _set(span, semconv.LAPLACE_SPAN_TYPE, span_type)
    _set(span, semconv.LAPLACE_SESSION_ID, session_id or _session_id.get())
    _set(span, semconv.LAPLACE_USER_ID, user_id or _user_id.get())
    if tags:
        _set(span, semconv.LAPLACE_TAGS, dumps(list(tags)))
    if metadata:
        _set(span, semconv.LAPLACE_METADATA, dumps(metadata))


def _record_input(span: OtelSpan, span_type: str, value: Any) -> None:
    payload = _payload(value)
    if payload is None:
        return
    if span_type == semconv.SPAN_TYPE_TOOL:
        _set(span, semconv.LAPLACE_TOOL_ARGUMENTS, payload)
    elif span_type == semconv.SPAN_TYPE_RETRIEVAL:
        _set(span, semconv.LAPLACE_RETRIEVAL_QUERY, payload)
    else:
        _set(span, semconv.LAPLACE_INPUT, payload)


def _record_output(span: OtelSpan, span_type: str, value: Any) -> None:
    payload = _payload(value)
    if payload is None:
        return
    if span_type == semconv.SPAN_TYPE_TOOL:
        _set(span, semconv.LAPLACE_TOOL_OUTPUT, payload)
    elif span_type == semconv.SPAN_TYPE_RETRIEVAL:
        _set(span, semconv.LAPLACE_RETRIEVAL_DOCUMENTS, payload)
    else:
        _set(span, semconv.LAPLACE_OUTPUT, payload)


def _record_error(span: OtelSpan, exc: BaseException) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))


def _bind_arguments(func: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    """Empareja args/kwargs con los nombres de los parámetros, sin `self`/`cls`."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
    except Exception:  # noqa: BLE001
        return {"args": list(args), "kwargs": kwargs}


# ---------------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------------


@contextmanager
def span(
    name: str,
    *,
    type: str = semconv.SPAN_TYPE_CHAIN,  # noqa: A002 - es el nombre del contrato
    input: Any = None,  # noqa: A002
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[OtelSpan]:
    """Abre un span manualmente.

        with laplace.span("planificar", type="agent") as s:
            ...
            laplace.update_current_span(output=plan)
    """
    tracer = get_tracer()
    # `_pasos.entrar` apila el nombre mientras dure el bloque: es lo que permite saber
    # desde qué **camino** se llama al modelo, y no sólo desde qué función (D-106).
    with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as otel_span, _pasos.entrar(
        name
    ):
        _apply_common(
            otel_span,
            span_type=type,
            session_id=session_id,
            user_id=user_id,
            tags=tags,
            metadata=metadata,
        )
        if type == semconv.SPAN_TYPE_TOOL:
            _set(otel_span, semconv.GEN_AI_OPERATION_NAME, semconv.OPERATION_EXECUTE_TOOL)
            _set(otel_span, semconv.GEN_AI_TOOL_NAME, name)
        elif type == semconv.SPAN_TYPE_AGENT:
            _set(otel_span, semconv.GEN_AI_OPERATION_NAME, semconv.OPERATION_INVOKE_AGENT)
            _set(otel_span, semconv.GEN_AI_AGENT_NAME, name)
        if input is not None:
            _record_input(otel_span, type, input)
        try:
            yield otel_span
        except BaseException as exc:
            _record_error(otel_span, exc)
            raise
        else:
            if otel_span.is_recording() and not _has_status(otel_span):
                otel_span.set_status(Status(StatusCode.OK))


def _has_status(otel_span: OtelSpan) -> bool:
    status = getattr(otel_span, "status", None)
    return bool(status and status.status_code is not StatusCode.UNSET)


def update_current_span(
    *,
    output: Any = None,
    input: Any = None,  # noqa: A002
    metadata: dict[str, Any] | None = None,
    **attributes: Any,
) -> None:
    """Añade datos al span en curso desde dentro del código instrumentado."""
    current = otel_trace.get_current_span()
    if not current.is_recording():
        return
    span_type = _current_span_type(current)
    if input is not None:
        _record_input(current, span_type, input)
    if output is not None:
        _record_output(current, span_type, output)
    if metadata:
        _set(current, semconv.LAPLACE_METADATA, dumps(metadata))
    for key, value in attributes.items():
        _set(current, key, value)


def _current_span_type(current: OtelSpan) -> str:
    attrs = getattr(current, "attributes", None) or {}
    return str(attrs.get(semconv.LAPLACE_SPAN_TYPE, semconv.SPAN_TYPE_CHAIN))


# ---------------------------------------------------------------------------------
# Decorador
# ---------------------------------------------------------------------------------


def _default_name(fn: Callable[..., Any]) -> str:
    """Nombre visible del span.

    Se usa `__qualname__` para conservar la clase (`AgenteViajes.responder`, que en el
    árbol dice mucho más que `responder`), pero se recorta el prefijo `<locals>` de las
    funciones anidadas: `crear_tools.<locals>.buscar` es ruido en pantalla.
    """
    qualname = getattr(fn, "__qualname__", "") or getattr(fn, "__name__", "span")
    if "<locals>." in qualname:
        qualname = qualname.rsplit("<locals>.", 1)[-1]
    return qualname


def observe(
    func: F | None = None,
    *,
    name: str | None = None,
    type: str = semconv.SPAN_TYPE_CHAIN,  # noqa: A002
    capture_input: bool = True,
    capture_output: bool = True,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F] | F:
    """Convierte una función en un paso de la traza.

        @laplace.observe(type="agent")
        def responder(pregunta: str) -> str: ...

        @laplace.observe(type="tool")
        def buscar(query: str) -> list[str]: ...

    Funciona con funciones síncronas y corrutinas. Si algo falla dentro del SDK, la
    función decorada se ejecuta igualmente: observar nunca puede romper lo observado.
    """

    def decorate(fn: F) -> F:
        span_name = name or _default_name(fn)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with span(
                    span_name,
                    type=type,
                    session_id=session_id,
                    user_id=user_id,
                    tags=tags,
                    metadata=metadata,
                ) as otel_span:
                    if capture_input:
                        _record_input(otel_span, type, _bind_arguments(fn, args, kwargs))
                    result = await fn(*args, **kwargs)
                    if capture_output:
                        _record_output(otel_span, type, result)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(
                span_name,
                type=type,
                session_id=session_id,
                user_id=user_id,
                tags=tags,
                metadata=metadata,
            ) as otel_span:
                if capture_input:
                    _record_input(otel_span, type, _bind_arguments(fn, args, kwargs))
                result = fn(*args, **kwargs)
                if capture_output:
                    _record_output(otel_span, type, result)
                return result

        return sync_wrapper  # type: ignore[return-value]

    if func is not None:  # uso sin paréntesis: @observe
        return decorate(func)
    return decorate
