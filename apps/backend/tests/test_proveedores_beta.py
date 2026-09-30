"""Las puertas `client.beta.*` de los dos SDK, contra los clientes de verdad.

Lo nuevo de cada proveedor sale primero en `beta`: en Anthropic, la gestión de contexto,
la compactación, los servidores MCP o el modo rápido sólo se piden por
`client.beta.messages`, y quien los usa manda **todo** su tráfico por ahí. Esas clases
son otras (`anthropic.resources.beta.messages.Messages`), no heredan de las normales y
van directas a la red, así que un parche puesto en `messages` no las ve: esas llamadas no
existían para Laplace. Ni coste, ni hallazgos, y la cobertura no podía avisar porque ni
siquiera veía el span.

En OpenAI, `client.beta.chat.completions` es la misma clase que `client.chat.completions`
y ya estaba cubierta (se comprueba aquí para que no cambie sin enterarnos), pero
`client.beta.responses` es otra, con su propio `create`.

Como en `test_proveedores_reales.py`: los clientes son los reales y lo único falso es el
transporte HTTP. `span_llm()` exige exactamente un span por llamada, así que también se
prueba que ninguna puerta beta cuenta dos veces.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from helpers import ingest, span_llm

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
anthropic = pytest.importorskip("anthropic", reason="el extra [anthropic] no está instalado")

import httpx  # noqa: E402
import httpx2  # noqa: E402
from laplace.integrations import anthropic as ai  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402
from pydantic import BaseModel  # noqa: E402

MODELO_OPENAI = "gpt-5.6-luna"
MODELO_ANTHROPIC = "claude-haiku-4-5"
INSTRUCCIONES = "Eres un asistente de equipaje de Vuelos Laplace."


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    ai.instrument()
    yield
    oi.uninstrument()
    ai.uninstrument()


def _anthropic(handler, *, asincrono: bool = False):
    if asincrono:
        return anthropic.AsyncAnthropic(
            api_key="sk-de-mentira",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
    return anthropic.Anthropic(
        api_key="sk-de-mentira",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _openai(handler, *, asincrono: bool = False):
    if asincrono:
        return openai.AsyncOpenAI(
            api_key="sk-de-mentira",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
    return openai.OpenAI(
        api_key="sk-de-mentira",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _sse(texto: str, *, modulo=httpx2):
    return lambda request: modulo.Response(
        200, content=texto.encode(), headers={"content-type": "text/event-stream"}
    )


def _llms():
    return [s for s in ingest() if s.type == "llm"]


# ---------------------------------------------------------------------------------
# Anthropic: client.beta.messages
# ---------------------------------------------------------------------------------

RESPUESTA_ANTHROPIC = {
    "id": "msg_beta",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": "Una maleta de mano."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        # Como en la API normal, `input_tokens` NO incluye lo leído de caché (D-050).
        "input_tokens": 176,
        "output_tokens": 12,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1024,
    },
}

SSE_ANTHROPIC = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1",'
    '"type":"message","role":"assistant","model":"claude-haiku-4-5","content":[],'
    '"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":176,'
    '"output_tokens":1,"cache_read_input_tokens":1024,'
    '"cache_creation_input_tokens":0}}}\n\n'
    'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"Una maleta "}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"de mano."}}\n\n'
    'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":'
    '"end_turn","stop_sequence":null},"usage":{"output_tokens":12}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def _comprobar_anthropic(*, streaming: bool):
    span = span_llm()
    assert span.llm.request_model == MODELO_ANTHROPIC
    assert span.llm.usage.input_tokens == 1200, "176 nuevos + 1024 de caché (D-050)"
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.llm.cost.unknown is False
    assert span.llm.cost.total_usd > 0
    assert span.llm.input_messages[0] == {"role": "system", "content": INSTRUCCIONES}
    # Con streaming y sin él, el texto: la misma forma para la misma llamada.
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."
    assert span.llm.finish_reasons == ["end_turn"]
    assert bool(span.attributes.get("laplace.streaming")) is streaming
    return span


def test_anthropic_beta_create():
    """La puerta de la gestión de contexto, la compactación y los servidores MCP."""
    cliente = _anthropic(lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC))
    respuesta = cliente.beta.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        system=INSTRUCCIONES,
        messages=[{"role": "user", "content": "¿Cuánto equipaje puedo llevar?"}],
        betas=["context-management-2025-06-27"],
    )
    assert respuesta.content[0].text == "Una maleta de mano."
    _comprobar_anthropic(streaming=False)


def test_anthropic_beta_create_asincrono():
    cliente = _anthropic(lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC), asincrono=True)

    async def llamar():
        return await cliente.beta.messages.create(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            system=INSTRUCCIONES,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
        )

    assert asyncio.run(llamar()).content[0].text == "Una maleta de mano."
    _comprobar_anthropic(streaming=False)


def test_anthropic_beta_create_en_streaming():
    cliente = _anthropic(_sse(SSE_ANTHROPIC))
    eventos = list(
        cliente.beta.messages.create(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            system=INSTRUCCIONES,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
            stream=True,
        )
    )
    assert eventos[0].type == "message_start"
    _comprobar_anthropic(streaming=True)


def test_anthropic_beta_stream_con_get_final_message():
    """El gestor beta guarda la petición en otro atributo privado que el normal
    (`_BetaMessageStreamManager__api_request`): buscar sólo el nombre del normal dejaba
    estas llamadas sin ver aunque el método estuviera parcheado."""
    cliente = _anthropic(_sse(SSE_ANTHROPIC))
    with cliente.beta.messages.stream(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        system=INSTRUCCIONES,
        messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
    ) as flujo:
        mensaje = flujo.get_final_message()
    assert mensaje.content[0].text == "Una maleta de mano."
    _comprobar_anthropic(streaming=True)


def test_anthropic_beta_stream_asincrono():
    cliente = _anthropic(_sse(SSE_ANTHROPIC), asincrono=True)

    async def consumir():
        async with cliente.beta.messages.stream(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            system=INSTRUCCIONES,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
        ) as flujo:
            return "".join([t async for t in flujo.text_stream])

    assert asyncio.run(consumir()) == "Una maleta de mano."
    _comprobar_anthropic(streaming=True)


def test_anthropic_beta_stream_sin_abrir_no_emite_nada():
    cliente = _anthropic(_sse(SSE_ANTHROPIC))
    cliente.beta.messages.stream(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    )
    assert _llms() == []


class Equipaje(BaseModel):
    piezas: int
    kilos: int


def test_anthropic_beta_parse():
    cuerpo = dict(RESPUESTA_ANTHROPIC)
    cuerpo["content"] = [{"type": "text", "text": '{"piezas": 1, "kilos": 8}'}]
    cliente = _anthropic(lambda r: httpx2.Response(200, json=cuerpo))
    respuesta = cliente.beta.messages.parse(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
        output_format=Equipaje,
    )
    assert respuesta.parsed_output == Equipaje(piezas=1, kilos=8)
    assert span_llm().llm.usage.input_tokens == 1200


def test_anthropic_beta_un_error_no_se_traga():
    def falla(request):
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "mal"}},
        )

    cliente = _anthropic(falla)
    with pytest.raises(anthropic.BadRequestError):
        cliente.beta.messages.create(
            model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
        )
    assert span_llm().status == "error"


def test_anthropic_beta_contar_tokens_no_es_una_llamada_al_modelo():
    """`count_tokens` no genera nada ni se factura como una llamada: no es un span de LLM."""
    cliente = _anthropic(lambda r: httpx2.Response(200, json={"input_tokens": 42}))
    cliente.beta.messages.count_tokens(
        model=MODELO_ANTHROPIC, messages=[{"role": "user", "content": "x"}]
    )
    assert _llms() == []


def test_desinstrumentar_deja_anthropic_beta_como_estaba():
    ai.uninstrument()
    cliente = _anthropic(lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC))
    cliente.beta.messages.create(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    )
    with cliente.beta.messages.stream(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    ):
        pass
    assert _llms() == []


# ---------------------------------------------------------------------------------
# OpenAI: client.beta.chat.completions y client.beta.responses
# ---------------------------------------------------------------------------------

RESPUESTA_CHAT = {
    "id": "chatcmpl-beta",
    "object": "chat.completion",
    "created": 1770000000,
    "model": "gpt-5.6-luna",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Una maleta de mano."},
            "finish_reason": "stop",
            "logprobs": None,
        }
    ],
    "usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 12,
        "total_tokens": 1212,
        "prompt_tokens_details": {"cached_tokens": 1024},
    },
}

RESPUESTA_RESPONSES = {
    "id": "resp_beta",
    "object": "response",
    "created_at": 1770000000,
    "status": "completed",
    "model": "gpt-5.6-luna-2026-08-01",
    "output": [
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Una maleta de mano.", "annotations": []}],
        }
    ],
    "parallel_tool_calls": True,
    "tool_choice": "auto",
    "tools": [],
    "usage": {
        "input_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 1024},
        "output_tokens": 12,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 1212,
    },
}


def _eventos(*eventos: dict) -> str:
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in eventos
    )


def test_openai_beta_chat_es_la_misma_puerta_y_cuenta_una_vez():
    cliente = _openai(lambda r: httpx.Response(200, json=RESPUESTA_CHAT))
    cliente.beta.chat.completions.create(
        model=MODELO_OPENAI, messages=[{"role": "user", "content": "hola"}]
    )
    span = span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.cached_input_tokens == 1024


def _comprobar_responses():
    span = span_llm()
    assert span.llm.request_model == MODELO_OPENAI
    assert span.llm.response_model == "gpt-5.6-luna-2026-08-01"
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.llm.cost.unknown is False
    assert span.llm.input_messages[0] == {"role": "system", "content": INSTRUCCIONES}
    assert span.llm.output_messages == [{"role": "assistant", "content": "Una maleta de mano."}]
    return span


def test_openai_beta_responses():
    cliente = _openai(lambda r: httpx.Response(200, json=RESPUESTA_RESPONSES))
    respuesta = cliente.beta.responses.create(
        model=MODELO_OPENAI, instructions=INSTRUCCIONES, input="¿Cuánto equipaje?"
    )
    assert respuesta.output[0].content[0].text == "Una maleta de mano."
    _comprobar_responses()


def test_openai_beta_responses_asincrono():
    cliente = _openai(lambda r: httpx.Response(200, json=RESPUESTA_RESPONSES), asincrono=True)

    async def llamar():
        return await cliente.beta.responses.create(
            model=MODELO_OPENAI, instructions=INSTRUCCIONES, input="¿Cuánto equipaje?"
        )

    asyncio.run(llamar())
    _comprobar_responses()


def test_openai_beta_responses_en_streaming():
    base = {k: v for k, v in RESPUESTA_RESPONSES.items() if k not in ("output", "usage")}
    flujo = _eventos(
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**base, "status": "in_progress", "output": [], "usage": None},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "Una maleta ",
            "logprobs": [],
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 2,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "de mano.",
            "logprobs": [],
        },
        {"type": "response.completed", "sequence_number": 3, "response": RESPUESTA_RESPONSES},
    )
    cliente = _openai(_sse(flujo, modulo=httpx))
    eventos = list(
        cliente.beta.responses.create(
            model=MODELO_OPENAI, instructions=INSTRUCCIONES, input="¿Equipaje?", stream=True
        )
    )
    assert eventos[-1].type == "response.completed"
    span = _comprobar_responses()
    assert span.attributes.get("laplace.streaming") is True


def test_desinstrumentar_deja_openai_beta_como_estaba():
    oi.uninstrument()
    cliente = _openai(lambda r: httpx.Response(200, json=RESPUESTA_RESPONSES))
    cliente.beta.responses.create(model=MODELO_OPENAI, input="hola")
    assert _llms() == []
