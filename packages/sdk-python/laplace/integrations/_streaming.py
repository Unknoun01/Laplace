"""Captura de respuestas en streaming.

En un agente real la mayoría de las llamadas van en streaming. Mientras no se contaran,
el coste total estaba mal por defecto en cualquier carga de verdad, y con él el ahorro,
que es el número que vende el producto.

El envoltorio tiene que ser invisible: se delega todo lo que no interceptamos al objeto
original, se soportan las mismas formas de consumo (iterar, `with`, `close`) y el span
se cierra pase lo que pase, incluso si quien lo consume abandona el stream a medias.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from typing import Any, Protocol

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode

from .. import semconv
from . import _common as c

logger = logging.getLogger("laplace")

#: Regla de dedo del propio proveedor: ~4 caracteres por token en inglés. Sólo se usa
#: cuando el proveedor no devuelve el recuento, y el span queda marcado como estimado.
CHARS_PER_TOKEN = 4


class Accumulator(Protocol):
    """Va juntando lo que llega por el stream de un proveedor concreto."""

    def feed(self, chunk: Any) -> None: ...

    def finish(self, span: OtelSpan) -> None: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def estimate_messages_tokens(messages: Any) -> int:
    """Aproxima los tokens de entrada a partir del texto de los mensajes."""
    try:
        return estimate_tokens(str(messages))
    except Exception:  # noqa: BLE001
        return 0


def _end(span: OtelSpan, accumulator: Accumulator | None, error: BaseException | None) -> None:
    """Cierra el span una sola vez, con lo que se haya podido acumular."""
    try:
        if accumulator is not None:
            accumulator.finish(span)
        if error is not None:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {error}"))
        else:
            span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo al cerrar el span de streaming", exc_info=True)
    finally:
        span.end()


class StreamProxy:
    """Envuelve un stream síncrono sin cambiar cómo se usa.

    Delegar por `__getattr__` es deliberado: los clientes de los proveedores exponen
    atributos propios (`.response`, `.text_stream`…) que el código del usuario puede
    estar usando, y romperlos sería exactamente lo que el SDK promete no hacer.
    """

    def __init__(self, stream: Any, span: OtelSpan, accumulator: Accumulator) -> None:
        self._laplace_stream = stream
        self._laplace_span = span
        self._laplace_accumulator = accumulator
        self._laplace_done = False
        # Si nadie termina de consumir el stream, el span se cierra igual al recogerse
        # la basura: una traza a medias es mejor que un span que nunca acaba.
        self._laplace_finalizer = weakref.finalize(self, _end, span, accumulator, None)

    # -- ciclo de vida --------------------------------------------------------------

    def _laplace_close(self, error: BaseException | None = None) -> None:
        if self._laplace_done:
            return
        self._laplace_done = True
        self._laplace_finalizer.detach()
        _end(self._laplace_span, self._laplace_accumulator, error)

    # -- consumo --------------------------------------------------------------------

    def __iter__(self) -> StreamProxy:
        self._laplace_iterator = iter(self._laplace_stream)
        return self

    def __next__(self) -> Any:
        try:
            chunk = next(self._laplace_iterator)
        except StopIteration:
            self._laplace_close()
            raise
        except BaseException as exc:
            self._laplace_close(exc)
            raise
        self._feed(chunk)
        return chunk

    def _feed(self, chunk: Any) -> None:
        try:
            self._laplace_accumulator.feed(chunk)
        except Exception:  # noqa: BLE001 - nunca romper el stream del usuario
            logger.debug("laplace: chunk no reconocido", exc_info=True)

    # -- formas alternativas de uso ---------------------------------------------------

    def __enter__(self) -> StreamProxy:
        self._laplace_stream.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        self._laplace_close(exc)
        return self._laplace_stream.__exit__(exc_type, exc, tb)

    def close(self) -> Any:
        self._laplace_close()
        return self._laplace_stream.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._laplace_stream, name)


class AsyncStreamProxy(StreamProxy):
    """Lo mismo para streams asíncronos."""

    def __aiter__(self) -> AsyncStreamProxy:
        self._laplace_iterator = self._laplace_stream.__aiter__()
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._laplace_iterator.__anext__()
        except StopAsyncIteration:
            self._laplace_close()
            raise
        except BaseException as exc:
            self._laplace_close(exc)
            raise
        self._feed(chunk)
        return chunk

    async def __aenter__(self) -> AsyncStreamProxy:
        await self._laplace_stream.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        self._laplace_close(exc)
        return await self._laplace_stream.__aexit__(exc_type, exc, tb)


def wrap(stream: Any, span: OtelSpan, accumulator: Accumulator, *, is_async: bool) -> Any:
    """Envuelve el stream; si algo va mal, devuelve el original y cierra el span."""
    try:
        proxy = AsyncStreamProxy if is_async else StreamProxy
        return proxy(stream, span, accumulator)
    except Exception:  # noqa: BLE001
        logger.debug("laplace: no se pudo envolver el stream", exc_info=True)
        _end(span, None, None)
        return stream


# ---------------------------------------------------------------------------------
# Volcado común al span
# ---------------------------------------------------------------------------------


def record_stream_result(
    span: OtelSpan,
    *,
    system: str,
    text: str,
    role: str,
    model: str | None,
    response_id: str | None,
    finish_reasons: list[str],
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    fallback_input_tokens: int,
) -> None:
    """Cierra un span de streaming con los mismos atributos que uno normal.

    Si el proveedor no ha devuelto el recuento, se cuenta localmente y se marca el span
    como estimado: la interfaz distingue medido de estimado, y el usuario sabe de cuál
    de los dos sale su factura.
    """
    medido = input_tokens is not None or output_tokens is not None
    entrada = input_tokens if input_tokens is not None else fallback_input_tokens
    salida = output_tokens if output_tokens is not None else estimate_tokens(text)

    c.record_response(
        span,
        model=model,
        response_id=response_id,
        finish_reasons=finish_reasons,
        messages=[{"role": role, "content": text}] if text else [],
    )
    c.record_usage(
        span,
        input_tokens=entrada,
        output_tokens=salida,
        cached_input_tokens=cached_input_tokens,
    )
    c.set_attr(span, semconv.LAPLACE_STREAMING, True)
    # Sólo se marca como estimado cuando de verdad lo es: si el proveedor dio el
    # recuento, esto es un dato medido y la UI no tiene por qué desconfiar de él.
    if not medido:
        c.set_attr(span, semconv.LAPLACE_USAGE_ESTIMATED, True)


def safe(fn: Callable[[], None]) -> None:
    """Ejecuta algo del SDK sin dejar que reviente el stream del usuario."""
    try:
        fn()
    except Exception:  # noqa: BLE001
        logger.debug("laplace: fallo acumulando el stream", exc_info=True)
