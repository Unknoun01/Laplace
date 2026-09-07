"""Registro manual de llamadas a modelos.

Para proveedores que Laplace todavía no instrumenta sola, gateways propios o modelos
auto-alojados. Emite exactamente los mismos atributos que las integraciones
automáticas, así que el coste y el panel de ahorro funcionan igual.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import semconv
from ._tracer import get_tracer
from .integrations import _common as c


class LLMSpanRecorder:
    """Handle para volcar la respuesta en un span `llm` abierto manualmente."""

    def __init__(self, span: OtelSpan) -> None:
        self._span = span

    @property
    def span(self) -> OtelSpan:
        return self._span

    def record_response(
        self,
        *,
        output_messages: Any = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cache_write_1h_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        response_model: str | None = None,
        response_id: str | None = None,
        finish_reasons: list[str] | None = None,
    ) -> None:
        c.record_response(
            self._span,
            model=response_model,
            response_id=response_id,
            finish_reasons=finish_reasons,
            messages=output_messages,
        )
        c.record_usage(
            self._span,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
            reasoning_tokens=reasoning_tokens,
        )


@contextmanager
def llm_span(
    *,
    model: str,
    system: str = "custom",
    input_messages: Any = None,
    operation: str = semconv.OPERATION_CHAT,
    name: str | None = None,
    **params: Any,
) -> Iterator[LLMSpanRecorder]:
    """Abre un span `llm` que se rellena a mano.

        with laplace.llm_span(model="gpt-4o-mini", system="openai",
                              input_messages=msgs, temperature=0) as llm:
            respuesta = mi_cliente(msgs)
            llm.record_response(output_messages=[respuesta],
                                input_tokens=120, output_tokens=45)
    """
    tracer = get_tracer()
    span_name = name or c.span_name(operation, model)
    # Antes de entrar en el span nuevo: dentro, el activo ya sería él.
    envolvente = c.enclosing_step()
    with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
        c.record_request(
            span,
            system=system,
            model=model,
            messages=input_messages,
            kwargs=params,
            operation=operation,
            enclosing=envolvente,
        )
        recorder = LLMSpanRecorder(span)
        try:
            yield recorder
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            raise
        else:
            span.set_status(Status(StatusCode.OK))
