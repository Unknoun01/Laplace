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

from . import _guardia, _pasos, semconv
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
_customer_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "laplace_customer_id", default=None
)


def set_context(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    customer_id: str | None = None,
) -> None:
    """Fija sesión, usuario final y cliente para todos los spans que se abran a partir
    de aquí.

    `customer_id` es quien paga por el trabajo del agente: con él, Laplace calcula el
    margen de cada cliente (D-161). Se propaga por `contextvars`, así que funciona igual
    en async y en hilos creados después de la llamada.
    """
    if session_id is not None:
        _session_id.set(session_id)
    if user_id is not None:
        _user_id.set(user_id)
    if customer_id is not None:
        _customer_id.set(customer_id)


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
    _set(span, semconv.LAPLACE_CUSTOMER_ID, _customer_id.get())
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


def _vigilar(span_type: str, name: str, fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    """Si hay un `laplace.guard` abierto, si este paso se puede ejecutar.

    Dentro del span: si se corta, el corte queda como error de este paso en la traza. Los
    argumentos se miran aunque no se capturen, porque el guard no los manda a ninguna
    parte; y sólo con un guard abierto, porque emparejarlos cuesta.
    """
    if not _guardia.hay_alguno():
        return None
    return _guardia.antes(span_type, name, _bind_arguments(fn, args, kwargs))


def _lugar(fn: Callable[..., Any]) -> tuple[str, int, str] | None:
    """Dónde está la función decorada. Se calcula una vez, al decorar: es estático."""
    try:
        from ._sitio import de_funcion

        return de_funcion(fn)
    except Exception:  # noqa: BLE001 - observar nunca rompe lo observado
        logger.debug("laplace: no se pudo ubicar la función", exc_info=True)
        return None


def _anotar_lugar(span: OtelSpan, lugar: tuple[str, int, str] | None) -> None:
    """El fichero y la primera línea de la función del paso (D-190): con eso el bot de PR
    sabe dónde poner un tope de vueltas. Se respeta `capture_code_location`."""
    if lugar is None or not get_config().capture_code_location:
        return
    ruta, linea, funcion = lugar
    _set(span, semconv.CODE_FILE_PATH, ruta)
    _set(span, semconv.CODE_LINE_NUMBER, linea)
    _set(span, semconv.CODE_FUNCTION_NAME, funcion)


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


def _preparar(
    otel_span: OtelSpan,
    name: str,
    *,
    span_type: str,
    input: Any,  # noqa: A002
    session_id: str | None,
    user_id: str | None,
    tags: list[str] | None,
    metadata: dict[str, Any] | None,
) -> None:
    """Los atributos con los que nace un span propio, venga de `span()` o de un generador."""
    _apply_common(
        otel_span,
        span_type=span_type,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
    )
    if span_type == semconv.SPAN_TYPE_TOOL:
        _set(otel_span, semconv.GEN_AI_OPERATION_NAME, semconv.OPERATION_EXECUTE_TOOL)
        _set(otel_span, semconv.GEN_AI_TOOL_NAME, name)
    elif span_type == semconv.SPAN_TYPE_AGENT:
        _set(otel_span, semconv.GEN_AI_OPERATION_NAME, semconv.OPERATION_INVOKE_AGENT)
        _set(otel_span, semconv.GEN_AI_AGENT_NAME, name)
    if input is not None:
        _record_input(otel_span, span_type, input)


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
    # desde qué **camino** se llama al modelo, y no sólo desde qué función (D-106). La
    # raíz es además la ejecución a la que se aplican los topes de Laplace cuando el
    # código no tiene su propio `guard`.
    with _guardia.implicita(_pasos.hoja() is None), tracer.start_as_current_span(
        name, kind=SpanKind.INTERNAL
    ) as otel_span, _pasos.entrar(name):
        _preparar(
            otel_span,
            name,
            span_type=type,
            input=input,
            session_id=session_id,
            user_id=user_id,
            tags=tags,
            metadata=metadata,
        )
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


#: Trozos de salida que se guardan de un generador. Uno que produce miles de tokens de
#: uno en uno no puede convertir su span en una lista de miles de elementos.
MAX_TROZOS_GUARDADOS = 200


class _Generador:
    """Lo común a los dos envoltorios de generador.

    El span no puede ser el «actual» durante toda su vida, como en `span()`: entre dos
    `yield` manda quien itera, y lo que él llame no es hijo del generador. Por eso se
    abre sin activar y se activa sólo mientras corre el cuerpo del generador, trozo a
    trozo. Y se cierra al agotarlo, al cortarlo o al fallar, no al crearlo.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        span_name: str,
        comunes: dict[str, Any],
        capture_input: bool,
        capture_output: bool,
        args: tuple,
        kwargs: dict,
    ) -> None:
        self.nombre = span_name
        self.tipo = comunes["type"]
        self.capture_output = capture_output
        self.trozos: list[Any] = []
        self.cerrado = False
        self.span = get_tracer().start_span(span_name, kind=SpanKind.INTERNAL)
        try:
            _preparar(
                self.span,
                span_name,
                span_type=self.tipo,
                input=_bind_arguments(fn, args, kwargs) if capture_input else None,
                session_id=comunes["session_id"],
                user_id=comunes["user_id"],
                tags=comunes["tags"],
                metadata=comunes["metadata"],
            )
            _anotar_lugar(self.span, _lugar(fn))
        except Exception:  # noqa: BLE001 - observar nunca rompe lo observado
            logger.debug("laplace: no se pudo preparar el span de %s", span_name, exc_info=True)

    @contextmanager
    def activo(self) -> Iterator[None]:
        with otel_trace.use_span(self.span, end_on_exit=False), _pasos.entrar(self.nombre):
            yield

    def trozo(self, valor: Any) -> None:
        if self.capture_output and len(self.trozos) < MAX_TROZOS_GUARDADOS:
            self.trozos.append(valor)

    def cerrar(self, exc: BaseException | None = None) -> None:
        if self.cerrado:
            return
        self.cerrado = True
        try:
            if exc is not None:
                _record_error(self.span, exc)
            else:
                if self.capture_output and self.trozos:
                    _record_output(self.span, self.tipo, self.trozos)
                if not _has_status(self.span):
                    self.span.set_status(Status(StatusCode.OK))
        finally:
            self.span.end()


def _envolver_generador(fn, span_name, comunes, capture_input, capture_output):
    @functools.wraps(fn)
    def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
        estado = _Generador(fn, span_name, comunes, capture_input, capture_output, args, kwargs)
        interno = fn(*args, **kwargs)
        enviado: Any = None
        try:
            while True:
                try:
                    with estado.activo():
                        valor = interno.send(enviado)
                except StopIteration as fin:
                    estado.cerrar()
                    return fin.value
                estado.trozo(valor)
                enviado = yield valor
        except GeneratorExit:
            # Quien itera ha dejado de leer: no es un fallo del paso.
            with estado.activo():
                interno.close()
            estado.cerrar()
            raise
        except BaseException as exc:
            estado.cerrar(exc)
            raise
        finally:
            estado.cerrar()

    return gen_wrapper


def _envolver_generador_async(fn, span_name, comunes, capture_input, capture_output):
    @functools.wraps(fn)
    async def agen_wrapper(*args: Any, **kwargs: Any) -> Any:
        estado = _Generador(fn, span_name, comunes, capture_input, capture_output, args, kwargs)
        interno = fn(*args, **kwargs)
        enviado: Any = None
        try:
            while True:
                try:
                    with estado.activo():
                        valor = await interno.asend(enviado)
                except StopAsyncIteration:
                    estado.cerrar()
                    return
                estado.trozo(valor)
                enviado = yield valor
        except GeneratorExit:
            with estado.activo():
                await interno.aclose()
            estado.cerrar()
            raise
        except BaseException as exc:
            estado.cerrar(exc)
            raise
        finally:
            estado.cerrar()

    return agen_wrapper


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

    Funciona con funciones síncronas, corrutinas y generadores (también asíncronos): el
    span de un generador dura hasta que se termina de iterar. Si algo falla dentro del SDK, la
    función decorada se ejecuta igualmente: observar nunca puede romper lo observado.
    """

    def decorate(fn: F) -> F:
        span_name = name or _default_name(fn)
        lugar = _lugar(fn)
        comunes = {
            "type": type,
            "session_id": session_id,
            "user_id": user_id,
            "tags": tags,
            "metadata": metadata,
        }

        # Los generadores van antes que las corrutinas: una función `async def` con
        # `yield` es un generador asíncrono, no una corrutina, y no se puede `await`.
        if inspect.isasyncgenfunction(fn):
            return _envolver_generador_async(  # type: ignore[return-value]
                fn, span_name, comunes, capture_input, capture_output
            )
        if inspect.isgeneratorfunction(fn):
            return _envolver_generador(  # type: ignore[return-value]
                fn, span_name, comunes, capture_input, capture_output
            )

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
                    _anotar_lugar(otel_span, lugar)
                    if capture_input:
                        _record_input(otel_span, type, _bind_arguments(fn, args, kwargs))
                    vuelta = _vigilar(type, span_name, fn, args, kwargs)
                    result = await fn(*args, **kwargs)
                    _guardia.despues(vuelta, result)
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
                _anotar_lugar(otel_span, lugar)
                if capture_input:
                    _record_input(otel_span, type, _bind_arguments(fn, args, kwargs))
                vuelta = _vigilar(type, span_name, fn, args, kwargs)
                result = fn(*args, **kwargs)
                _guardia.despues(vuelta, result)
                if capture_output:
                    _record_output(otel_span, type, result)
                return result

        return sync_wrapper  # type: ignore[return-value]

    if func is not None:  # uso sin paréntesis: @observe
        return decorate(func)
    return decorate
