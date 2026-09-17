"""La misma llamada, en streaming y sin streaming, tiene que costar lo mismo.

En un agente real la mayoría de las llamadas van en streaming. Mientras no se contaran,
el coste total estaba mal por defecto en cualquier carga de verdad. Estas pruebas usan
clientes falsos con la forma exacta de los chunks de cada proveedor: no hacen falta
claves de API, pero sí obligan a que el acumulador entienda el formato real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from helpers import exporter, ingest
from laplace import decorators  # noqa: F401  (registra el proveedor de tracing)
from laplace.integrations import _streaming as st
from laplace.integrations.anthropic import _AnthropicStream
from laplace.integrations.openai import _OpenAIStream
from opentelemetry.trace import SpanKind

TEXTO = "Te recomiendo el vuelo IB3421 de las 08:15."
MENSAJES = [{"role": "user", "content": "¿Qué vuelos hay a Barcelona por la mañana?"}]


# ---------------------------------------------------------------------------------
# Dobles con la forma real de los chunks
# ---------------------------------------------------------------------------------


@dataclass
class _Obj:
    """Objeto con atributos arbitrarios, como los modelos de los SDK."""

    _campos: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["_campos"][name]
        except KeyError:
            return None


def obj(**campos: Any) -> _Obj:
    return _Obj(campos)


def chunks_openai(*, con_usage: bool) -> list[_Obj]:
    """`chat.completions` en streaming: deltas y, opcionalmente, el chunk de usage."""
    trozos = [
        obj(
            id="chatcmpl-1",
            model="gpt-5.6-luna",
            choices=[obj(delta=obj(content=parte), finish_reason=None)],
        )
        for parte in (TEXTO[:20], TEXTO[20:])
    ]
    trozos.append(obj(id="chatcmpl-1", model="gpt-5.6-luna",
                      choices=[obj(delta=obj(content=None), finish_reason="stop")]))
    if con_usage:
        # Sólo llega si la petición pidió stream_options={"include_usage": True}.
        trozos.append(obj(id="chatcmpl-1", model="gpt-5.6-luna", choices=[],
                          usage=obj(prompt_tokens=120, completion_tokens=14)))
    return trozos


def eventos_anthropic() -> list[_Obj]:
    """`messages.create(stream=True)`: Anthropic sí manda los recuentos."""
    return [
        obj(type="message_start",
            message=obj(id="msg_1", model="claude-haiku-4-5", role="assistant",
                        usage=obj(input_tokens=120, output_tokens=0))),
        obj(type="content_block_delta", delta=obj(text=TEXTO[:20])),
        obj(type="content_block_delta", delta=obj(text=TEXTO[20:])),
        obj(type="message_delta", delta=obj(stop_reason="end_turn"),
            usage=obj(output_tokens=14)),
        obj(type="message_stop"),
    ]


class _StreamFalso:
    """Se comporta como el `Stream` de un cliente: iterable y context manager."""

    def __init__(self, trozos: list[Any]) -> None:
        self._trozos = trozos
        self.cerrado = False

    def __iter__(self):
        return iter(self._trozos)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        self.cerrado = True

    @property
    def response(self):
        return "atributo propio del cliente"


def _consumir(accumulator, trozos) -> None:
    """Abre un span, lo envuelve como haría la integración, y consume el stream."""
    from laplace._tracer import get_tracer

    span = get_tracer().start_span("chat gpt-5.6-luna", kind=SpanKind.CLIENT)
    proxy = st.wrap(_StreamFalso(trozos), span, accumulator, is_async=False)
    for _ in proxy:
        pass


# ---------------------------------------------------------------------------------


def test_openai_en_streaming_acumula_tokens_y_texto_del_proveedor():
    _consumir(_OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
              chunks_openai(con_usage=True))

    span = ingest()[0]
    assert span.type == "llm"
    assert span.llm.usage.input_tokens == 120
    assert span.llm.usage.output_tokens == 14
    # Recuento del proveedor: esto no es una estimación.
    assert span.llm.usage.estimated is False
    assert span.llm.output_messages[0]["content"] == TEXTO
    assert span.llm.finish_reasons == ["stop"]
    # Y el coste sale como en cualquier otra llamada.
    assert span.llm.cost.unknown is False
    assert span.llm.cost.total_usd == pytest.approx((120 * 0.2 + 14 * 1.2) / 1e6)


def test_sin_include_usage_se_estima_y_se_marca_como_tal():
    """OpenAI no manda el recuento salvo que se lo pidas. No se lo pedimos por su cuenta.

    Inyectar `stream_options` cambiaría la forma del stream que recibe el usuario, y eso
    es justo lo que un SDK de observabilidad no puede permitirse. Se estima y se dice.
    """
    _consumir(_OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
              chunks_openai(con_usage=False))

    span = ingest()[0]
    assert span.llm.usage.estimated is True
    assert span.llm.usage.output_tokens > 0
    assert span.llm.output_messages[0]["content"] == TEXTO


def test_anthropic_en_streaming_usa_los_recuentos_de_los_eventos():
    _consumir(_AnthropicStream({"model": "claude-haiku-4-5", "messages": MENSAJES}),
              eventos_anthropic())

    span = ingest()[0]
    assert span.llm.usage.input_tokens == 120
    assert span.llm.usage.output_tokens == 14
    assert span.llm.usage.estimated is False
    assert span.llm.output_messages[0]["content"] == TEXTO
    assert span.llm.finish_reasons == ["end_turn"]
    assert span.llm.cost.total_usd == pytest.approx((120 * 1.0 + 14 * 5.0) / 1e6)


def test_streaming_y_no_streaming_cuestan_lo_mismo():
    """La comprobación que pedía el encargo: mismos tokens, mismo coste."""
    from laplace import manual

    # Sin streaming.
    with manual.llm_span(model="gpt-5.6-luna", system="openai", input_messages=MENSAJES) as llm:
        llm.record_response(
            output_messages=[{"role": "assistant", "content": TEXTO}],
            input_tokens=120,
            output_tokens=14,
        )
    sin_stream = ingest()[0]

    # En streaming, con el recuento del proveedor.
    exporter.clear()
    _consumir(_OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
              chunks_openai(con_usage=True))
    con_stream = ingest()[0]

    assert con_stream.llm.usage.input_tokens == sin_stream.llm.usage.input_tokens
    assert con_stream.llm.usage.output_tokens == sin_stream.llm.usage.output_tokens
    assert con_stream.llm.cost.total_usd == pytest.approx(sin_stream.llm.cost.total_usd)
    assert con_stream.llm.output_messages[0]["content"] == TEXTO


def test_la_estimacion_se_queda_en_el_mismo_orden_de_magnitud():
    """No hace falta que clave el número, pero no puede irse por un factor de diez."""
    _consumir(_OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
              chunks_openai(con_usage=False))
    estimado = ingest()[0]

    exporter.clear()
    _consumir(_OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
              chunks_openai(con_usage=True))
    medido = ingest()[0]

    ratio = estimado.llm.usage.output_tokens / medido.llm.usage.output_tokens
    assert 0.4 < ratio < 2.5, f"la estimación se desvía demasiado (x{ratio:.2f})"


# ---------------------------------------------------------------------------------
# El envoltorio no puede cambiar cómo se usa el stream
# ---------------------------------------------------------------------------------


def test_el_envoltorio_delega_lo_que_no_intercepta():
    from laplace._tracer import get_tracer

    original = _StreamFalso(chunks_openai(con_usage=True))
    span = get_tracer().start_span("chat", kind=SpanKind.CLIENT)
    proxy = st.wrap(original, span, _OpenAIStream({"model": "gpt-5.6-luna"}), is_async=False)

    # Atributos propios del cliente que el código del usuario puede estar usando.
    assert proxy.response == "atributo propio del cliente"

    with proxy as abierto:
        for _ in abierto:
            pass
    assert ingest()[0].llm.usage.output_tokens == 14


def test_abandonar_el_stream_a_medias_cierra_el_span_igual():
    """Una traza a medias es mejor que un span que no acaba nunca."""
    from laplace._tracer import get_tracer

    span = get_tracer().start_span("chat", kind=SpanKind.CLIENT)
    proxy = st.wrap(
        _StreamFalso(chunks_openai(con_usage=True)),
        span,
        _OpenAIStream({"model": "gpt-5.6-luna", "messages": MENSAJES}),
        is_async=False,
    )
    iterador = iter(proxy)
    next(iterador)  # sólo el primer trozo
    proxy.close()

    spans = ingest()
    assert len(spans) == 1
    assert spans[0].llm is not None
