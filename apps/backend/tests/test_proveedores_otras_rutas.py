"""Las otras puertas por las que un agente llama al modelo, contra los SDK de verdad.

`chat.completions.create` y `messages.create` no son las únicas. El Agents SDK de OpenAI
y cualquier código nuevo usan la **Responses API** (`client.responses.create`, y sus
ayudantes `stream()` y `parse()`); la salida estructurada va por `chat.completions.parse`;
y el ejemplo de streaming de la documentación de Anthropic es `client.messages.stream()`.
Una llamada que no pasa por nuestro parche no existe para Laplace: ni coste, ni tokens,
ni hallazgos, y la cobertura no puede decir que falta porque ni siquiera ve el span.

Como en `test_proveedores_reales.py`, los clientes son los reales y lo único falso es el
transporte HTTP. Y la condición de `span_llm()` —exactamente un span por llamada— es la
mitad de lo que se prueba: los ayudantes de los SDK llaman por dentro a `create`, y
parchear los dos sin mirar contaría la misma llamada dos veces.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from helpers import span_llm

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
anthropic = pytest.importorskip("anthropic", reason="el extra [anthropic] no está instalado")

import httpx  # noqa: E402
import httpx2  # noqa: E402
from laplace.integrations import anthropic as ai  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402
from pydantic import BaseModel  # noqa: E402

MODELO_OPENAI = "gpt-5.6-luna"
MODELO_ANTHROPIC = "claude-haiku-4-5"


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    ai.instrument()
    yield
    oi.uninstrument()
    ai.uninstrument()


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


def _json(cuerpo: dict):
    return lambda request: httpx.Response(200, json=cuerpo)


def _sse(texto: str, *, modulo=httpx):
    return lambda request: modulo.Response(
        200, content=texto.encode(), headers={"content-type": "text/event-stream"}
    )


def _eventos(*eventos: dict) -> str:
    """Un flujo SSE con la forma de la Responses API: `event:` y `data:` por evento."""
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in eventos
    )


# ---------------------------------------------------------------------------------
# OpenAI: Responses API
# ---------------------------------------------------------------------------------

INSTRUCCIONES = "Eres un asistente de equipaje de Vuelos Laplace."

USO_RESPONSES = {
    # En la Responses API, como en Chat, `input_tokens` ya incluye lo servido desde
    # caché: el contrato lo quiere así y no hay que sumar nada (D-050).
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    "output_tokens": 12,
    "output_tokens_details": {"reasoning_tokens": 4},
    "total_tokens": 1212,
}


def _respuesta(**cambios) -> dict:
    cuerpo = {
        "id": "resp_abc123",
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
                "content": [
                    {"type": "output_text", "text": "Una maleta de mano.", "annotations": []}
                ],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": USO_RESPONSES,
    }
    cuerpo.update(cambios)
    return cuerpo


def test_responses_una_llamada_normal():
    """El camino del Agents SDK: `instructions` + `input`, y el uso en `usage`."""
    cliente = _openai(_json(_respuesta()))
    respuesta = cliente.responses.create(
        model=MODELO_OPENAI,
        instructions=INSTRUCCIONES,
        input="¿Cuánto equipaje puedo llevar?",
        temperature=0.2,
        max_output_tokens=64,
    )
    assert respuesta.output_text == "Una maleta de mano."

    span = span_llm()
    assert span.llm.request_model == MODELO_OPENAI
    # El que factura es el de la respuesta, con su fecha, y tiene que resolverse.
    assert span.llm.response_model == "gpt-5.6-luna-2026-08-01"
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.uncached_input_tokens == 176
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.reasoning_tokens == 4
    assert span.llm.usage.estimated is False
    assert span.llm.cost.unknown is False
    assert span.llm.cost.total_usd > 0
    assert span.llm.params.get("temperature") == 0.2
    assert span.llm.params.get("max_tokens") == 64
    # Las instrucciones van fuera de `input`, como el `system` de Anthropic. Si no se
    # unieran como mensaje de sistema, se perdería la mitad de la identidad del paso.
    assert span.llm.input_messages[0] == {"role": "system", "content": INSTRUCCIONES}
    assert span.llm.input_messages[1] == {
        "role": "user",
        "content": "¿Cuánto equipaje puedo llevar?",
    }
    assert span.llm.output_messages == [{"role": "assistant", "content": "Una maleta de mano."}]
    assert span.llm.finish_reasons == ["completed"]


def test_responses_las_instrucciones_hacen_la_identidad_del_paso():
    """Dos llamadas con distintas `instructions` y la misma pregunta son dos pasos."""
    cliente = _openai(_json(_respuesta()))
    for instrucciones in ("Clasifica el ticket.", "Resume el ticket."):
        cliente.responses.create(
            model=MODELO_OPENAI, instructions=instrucciones, input="El ticket."
        )
    from helpers import ingest

    claves = {s.step_key for s in ingest() if s.type == "llm"}
    assert len(claves) == 2


def test_responses_el_rol_developer_cuenta_como_instrucciones():
    """En la API nueva de OpenAI el prompt de sistema se llama `developer`. Si la huella
    sólo mirara `system`, todo el tráfico que lo use caería en un único paso."""
    cliente = _openai(_json(_respuesta()))
    for instrucciones in ("Clasifica el ticket.", "Resume el ticket."):
        cliente.responses.create(
            model=MODELO_OPENAI,
            input=[
                {"role": "developer", "content": instrucciones},
                {"role": "user", "content": "El ticket."},
            ],
        )
    from helpers import ingest

    spans = [s for s in ingest() if s.type == "llm"]
    assert len({s.step_key for s in spans}) == 2
    assert all(s.step_hint for s in spans), "la pista legible sale de esas instrucciones"


def test_responses_una_entrada_con_partes_se_lee_como_texto():
    """`input` admite mensajes cuyo contenido son partes `input_text`. Se aplanan para que
    el explorador enseñe texto y el prompt gestionado se pueda reconocer dentro."""
    cliente = _openai(_json(_respuesta()))
    cliente.responses.create(
        model=MODELO_OPENAI,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "¿Cuánto "},
                    {"type": "input_text", "text": "equipaje?"},
                ],
            }
        ],
    )
    assert span_llm().llm.input_messages == [{"role": "user", "content": "¿Cuánto equipaje?"}]


def test_responses_una_llamada_a_herramienta_se_guarda_entera():
    """Una respuesta que pide una herramienta no trae texto: trae la llamada. Es lo que
    necesita la regla de bucles para distinguir un reintento de un avance."""
    cuerpo = _respuesta(
        output=[
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "buscar_vuelo",
                "arguments": '{"origen": "MAD"}',
                "status": "completed",
            }
        ]
    )
    cliente = _openai(_json(cuerpo))
    cliente.responses.create(
        model=MODELO_OPENAI,
        input="Busca un vuelo desde Madrid.",
        tools=[{"type": "function", "name": "buscar_vuelo", "parameters": {"type": "object"}}],
    )
    span = span_llm()
    salida = span.llm.output_messages
    assert salida[0]["type"] == "function_call"
    assert salida[0]["name"] == "buscar_vuelo"
    assert salida[0]["arguments"] == '{"origen": "MAD"}'
    # Las herramientas declaradas son la otra mitad de la huella del paso.
    assert "buscar_vuelo" in span.attributes.get("laplace.request.tools", "")


def test_responses_la_escritura_de_cache_se_cobra():
    """El mismo campo que D-101 cerró en Chat, por la otra API: si no se lee, esos tokens
    se cobran como entrada normal y el coste sale por debajo del real."""
    uso = dict(USO_RESPONSES)
    uso.update(
        input_tokens=10_000,
        input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 8_000},
        output_tokens=10,
        output_tokens_details={"reasoning_tokens": 0},
    )
    cliente = _openai(_json(_respuesta(model="gpt-5.6-luna", usage=uso)))
    cliente.responses.create(model="gpt-5.6-luna", input="hola")

    span = span_llm()
    assert span.llm.usage.cache_write_tokens == 8_000
    esperado = (2_000 * 0.2 + 8_000 * 0.25) / 1_000_000
    assert span.llm.cost.input_usd == pytest.approx(esperado)


def test_responses_el_modo_prioritario_se_factura_aparte():
    """`service_tier="priority"` es el modo rápido de OpenAI y tiene tarifa propia."""
    cliente = _openai(_json(_respuesta(service_tier="priority")))
    cliente.responses.create(model=MODELO_OPENAI, input="hola", service_tier="priority")
    assert span_llm().llm.billing_tier == "fast"


def test_responses_una_respuesta_cortada_lo_dice():
    """Una respuesta que se queda sin tokens llega `incomplete`, y el motivo es lo que
    dirá mañana si la salida se trunca: se guarda el motivo, no el estado."""
    cuerpo = _respuesta(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    cliente = _openai(_json(cuerpo))
    cliente.responses.create(model=MODELO_OPENAI, input="hola", max_output_tokens=4)
    assert span_llm().llm.finish_reasons == ["max_output_tokens"]


def test_responses_asincrono():
    cliente = _openai(_json(_respuesta()), asincrono=True)

    async def llamar():
        return await cliente.responses.create(model=MODELO_OPENAI, input="hola")

    respuesta = asyncio.run(llamar())
    assert respuesta.output_text == "Una maleta de mano."
    assert span_llm().llm.usage.input_tokens == 1200


def _flujo_responses(texto_partes=("Una maleta ", "de mano."), final=None) -> str:
    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1770000000,
        "model": "gpt-5.6-luna-2026-08-01",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    item = {"type": "message", "id": "msg_1", "status": "in_progress", "role": "assistant"}
    completo = "".join(texto_partes)
    eventos = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**base, "status": "in_progress"},
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {**item, "content": []},
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 2,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
    ]
    for i, trozo in enumerate(texto_partes):
        eventos.append(
            {
                "type": "response.output_text.delta",
                "sequence_number": 3 + i,
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": trozo,
                "logprobs": [],
            }
        )
    n = 3 + len(texto_partes)
    parte = {"type": "output_text", "text": completo, "annotations": []}
    eventos += [
        {
            "type": "response.output_text.done",
            "sequence_number": n,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": completo,
            "logprobs": [],
        },
        {
            "type": "response.content_part.done",
            "sequence_number": n + 1,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": parte,
        },
        {
            "type": "response.output_item.done",
            "sequence_number": n + 2,
            "output_index": 0,
            "item": {**item, "status": "completed", "content": [parte]},
        },
        final
        or {
            "type": "response.completed",
            "sequence_number": n + 3,
            "response": {
                **base,
                "status": "completed",
                "output": [{**item, "status": "completed", "content": [parte]}],
                "usage": USO_RESPONSES,
            },
        },
    ]
    return _eventos(*eventos)


def test_responses_en_streaming():
    """En streaming la Responses API manda el uso siempre, en `response.completed`: el
    coste es medido y no hace falta `include_usage` como en Chat."""
    cliente = _openai(_sse(_flujo_responses()))
    eventos = list(
        cliente.responses.create(
            model=MODELO_OPENAI, instructions=INSTRUCCIONES, input="¿Equipaje?", stream=True
        )
    )
    assert eventos[-1].type == "response.completed"

    span = span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.attributes.get("laplace.streaming") is True
    assert span.llm.response_model == "gpt-5.6-luna-2026-08-01"
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."
    assert span.llm.finish_reasons == ["completed"]


def test_responses_en_streaming_asincrono():
    cliente = _openai(_sse(_flujo_responses()), asincrono=True)

    async def consumir():
        flujo = await cliente.responses.create(model=MODELO_OPENAI, input="hola", stream=True)
        return [e async for e in flujo]

    asyncio.run(consumir())
    uso = span_llm().llm.usage
    assert uso.input_tokens == 1200
    assert uso.estimated is False


def test_responses_un_flujo_que_falla_se_marca_y_no_se_inventa_el_uso():
    """`response.failed` sin uso: el span sale como error y los tokens, estimados. Nunca
    un cero que diga que la llamada no costó nada."""
    fallo = {
        "type": "response.failed",
        "sequence_number": 99,
        "response": {
            "id": "resp_1",
            "object": "response",
            "created_at": 1770000000,
            "model": "gpt-5.6-luna",
            "status": "failed",
            "error": {"code": "server_error", "message": "se cayó"},
            "output": [],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": None,
        },
    }
    cliente = _openai(_sse(_flujo_responses(final=fallo)))
    list(cliente.responses.create(model=MODELO_OPENAI, input="hola", stream=True))

    span = span_llm()
    assert span.status == "error", "el flujo acabó bien por fuera, pero la llamada falló"
    assert "se cayó" in span.status_message
    assert span.llm.usage.estimated is True
    assert span.llm.usage.output_tokens > 0
    assert span.llm.finish_reasons == ["failed"]


def test_responses_el_ayudante_stream_cuenta_una_vez():
    """`responses.stream()` llama por dentro a `responses.create(stream=True)`. Tiene que
    salir un span con el uso medido, y uno solo."""
    cliente = _openai(_sse(_flujo_responses()))
    with cliente.responses.stream(model=MODELO_OPENAI, input="hola") as flujo:
        for _ in flujo:
            pass
        final = flujo.get_final_response()
    assert final.output_text == "Una maleta de mano."

    span = span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.estimated is False
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."


class Equipaje(BaseModel):
    piezas: int
    kilos: int


def test_responses_parse():
    """La salida estructurada de la Responses API va por `parse`, que no pasa por
    `create`: sin su propio parche, esas llamadas no existían."""
    json_salida = '{"piezas": 1, "kilos": 8}'
    cuerpo = _respuesta(
        output=[
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json_salida, "annotations": []}],
            }
        ]
    )
    cliente = _openai(_json(cuerpo))
    respuesta = cliente.responses.parse(
        model=MODELO_OPENAI, input="¿Cuánto equipaje?", text_format=Equipaje
    )
    assert respuesta.output_parsed == Equipaje(piezas=1, kilos=8)

    span = span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.output_messages == [{"role": "assistant", "content": json_salida}]


def test_responses_parse_asincrono():
    json_salida = '{"piezas": 2, "kilos": 20}'
    cuerpo = _respuesta(
        output=[
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json_salida, "annotations": []}],
            }
        ]
    )
    cliente = _openai(_json(cuerpo), asincrono=True)

    async def llamar():
        return await cliente.responses.parse(model=MODELO_OPENAI, input="x", text_format=Equipaje)

    assert asyncio.run(llamar()).output_parsed.piezas == 2
    assert span_llm().llm.usage.output_tokens == 12


def test_responses_un_error_no_se_traga():
    def falla(request):
        return httpx.Response(429, json={"error": {"message": "rate limit", "type": "rate_limit"}})

    cliente = _openai(falla)
    with pytest.raises(openai.RateLimitError):
        cliente.responses.create(model=MODELO_OPENAI, input="hola")
    span = span_llm()
    assert span.status == "error"


def test_desinstrumentar_deja_la_responses_api_como_estaba():
    oi.uninstrument()
    cliente = _openai(_json(_respuesta()))
    cliente.responses.create(model=MODELO_OPENAI, input="hola")
    cliente.chat.completions.parse  # noqa: B018 - existe y no está envuelto
    from helpers import ingest

    assert [s for s in ingest() if s.type == "llm"] == []


# ---------------------------------------------------------------------------------
# OpenAI: chat.completions.parse
# ---------------------------------------------------------------------------------

RESPUESTA_CHAT_PARSE = {
    "id": "chatcmpl-parse",
    "object": "chat.completion",
    "created": 1770000000,
    "model": "gpt-5.6-luna",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"piezas": 1, "kilos": 8}'},
            "finish_reason": "stop",
            "logprobs": None,
        }
    ],
    "usage": {
        "prompt_tokens": 300,
        "completion_tokens": 9,
        "total_tokens": 309,
        "prompt_tokens_details": {"cached_tokens": 0},
    },
}


def test_chat_parse():
    """`chat.completions.parse` va directo a la red sin pasar por `create`."""
    cliente = _openai(_json(RESPUESTA_CHAT_PARSE))
    respuesta = cliente.chat.completions.parse(
        model=MODELO_OPENAI,
        messages=[
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": "¿Cuánto equipaje?"},
        ],
        response_format=Equipaje,
    )
    assert respuesta.choices[0].message.parsed == Equipaje(piezas=1, kilos=8)

    span = span_llm()
    assert span.llm.usage.input_tokens == 300
    assert span.llm.usage.output_tokens == 9
    assert span.llm.input_messages[0]["role"] == "system"


def test_chat_parse_asincrono():
    cliente = _openai(_json(RESPUESTA_CHAT_PARSE), asincrono=True)

    async def llamar():
        return await cliente.chat.completions.parse(
            model=MODELO_OPENAI,
            messages=[{"role": "user", "content": "x"}],
            response_format=Equipaje,
        )

    assert asyncio.run(llamar()).choices[0].message.parsed.kilos == 8
    assert span_llm().llm.usage.input_tokens == 300


SSE_CHAT = (
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1770000000,'
    '"model":"gpt-5.6-luna","choices":[{"index":0,"delta":{"role":"assistant",'
    '"content":"Hola."},"finish_reason":"stop"}]}\n\n'
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1770000000,'
    '"model":"gpt-5.6-luna","choices":[],"usage":{"prompt_tokens":50,'
    '"completion_tokens":2,"total_tokens":52}}\n\n'
    "data: [DONE]\n\n"
)


def test_chat_el_ayudante_stream_cuenta_una_vez():
    """`chat.completions.stream()` llama a `create(stream=True)` por dentro. Ya se veía;
    esto es para que siga siendo un span y no dos el día que se parchee algo más."""
    cliente = _openai(_sse(SSE_CHAT))
    with cliente.chat.completions.stream(
        model=MODELO_OPENAI,
        messages=[{"role": "user", "content": "hola"}],
        stream_options={"include_usage": True},
    ) as flujo:
        for _ in flujo:
            pass
    uso = span_llm().llm.usage
    assert uso.input_tokens == 50
    assert uso.estimated is False


def test_volcar_una_salida_estructurada_no_ensucia_la_consola_del_usuario():
    """Pydantic avisa al volcar un `parsed` con su clase del usuario dentro. Ese aviso
    saldría en el proceso de quien nos instala, por cada llamada: observar no puede
    hacer ruido en lo observado."""
    import warnings

    cliente = _openai(_json(RESPUESTA_CHAT_PARSE))
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        cliente.chat.completions.parse(
            model=MODELO_OPENAI,
            messages=[{"role": "user", "content": "x"}],
            response_format=Equipaje,
        )
    assert [str(a.message) for a in avisos] == []
    assert span_llm().llm.output_messages[0]["parsed"] == {"piezas": 1, "kilos": 8}


# ---------------------------------------------------------------------------------
# Anthropic: messages.stream() y messages.parse()
# ---------------------------------------------------------------------------------

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


def _comprobar_flujo_anthropic():
    span = span_llm()
    assert span.llm.usage.input_tokens == 1200, "176 nuevos + 1024 de caché (D-050)"
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.attributes.get("laplace.streaming") is True
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."
    assert span.llm.finish_reasons == ["end_turn"]
    assert span.llm.input_messages[0] == {"role": "system", "content": INSTRUCCIONES}
    return span


def test_anthropic_stream_con_get_final_message():
    """El ejemplo de la documentación de Anthropic. `stream()` no pasa por `create`: va
    directo a la red, así que sin su propio parche estas llamadas no existían."""
    cliente = _anthropic(_sse(SSE_ANTHROPIC, modulo=httpx2))
    with cliente.messages.stream(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        system=INSTRUCCIONES,
        messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
    ) as flujo:
        mensaje = flujo.get_final_message()
    assert mensaje.content[0].text == "Una maleta de mano."
    _comprobar_flujo_anthropic()


def test_anthropic_stream_con_text_stream():
    cliente = _anthropic(_sse(SSE_ANTHROPIC, modulo=httpx2))
    with cliente.messages.stream(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        system=INSTRUCCIONES,
        messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
    ) as flujo:
        texto = "".join(flujo.text_stream)
    assert texto == "Una maleta de mano."
    _comprobar_flujo_anthropic()


def test_anthropic_stream_asincrono():
    cliente = _anthropic(_sse(SSE_ANTHROPIC, modulo=httpx2), asincrono=True)

    async def consumir():
        async with cliente.messages.stream(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            system=INSTRUCCIONES,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
        ) as flujo:
            return await flujo.get_final_message()

    assert asyncio.run(consumir()).content[0].text == "Una maleta de mano."
    _comprobar_flujo_anthropic()


def test_anthropic_stream_abandonado_cierra_el_span():
    """Quien sale del `with` sin leer nada no puede dejar un span abierto para siempre."""
    cliente = _anthropic(_sse(SSE_ANTHROPIC, modulo=httpx2))
    with cliente.messages.stream(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    ):
        pass
    assert span_llm().llm.request_model == MODELO_ANTHROPIC


def test_anthropic_stream_un_error_no_se_traga():
    def falla(request):
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "mal"}},
        )

    cliente = _anthropic(falla)
    with pytest.raises(anthropic.BadRequestError):
        with cliente.messages.stream(
            model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
        ):
            pass
    assert span_llm().status == "error"


def test_anthropic_stream_sin_abrir_no_emite_nada():
    """`stream()` sólo prepara la petición; la llamada ocurre al entrar en el `with`. Un
    gestor que nunca se abre no ha llamado al modelo y no puede aparecer como llamada."""
    cliente = _anthropic(_sse(SSE_ANTHROPIC, modulo=httpx2))
    cliente.messages.stream(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    )
    from helpers import ingest

    assert [s for s in ingest() if s.type == "llm"] == []


RESPUESTA_ANTHROPIC_PARSE = {
    "id": "msg_parse",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": '{"piezas": 1, "kilos": 8}'}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 300, "output_tokens": 9},
}


def test_anthropic_parse():
    """La salida estructurada de Anthropic también va directa a la red."""
    cliente = _anthropic(lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC_PARSE))
    respuesta = cliente.messages.parse(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
        output_format=Equipaje,
    )
    assert respuesta.parsed_output == Equipaje(piezas=1, kilos=8)
    span = span_llm()
    assert span.llm.usage.input_tokens == 300
    assert span.llm.usage.output_tokens == 9


def test_anthropic_parse_asincrono():
    cliente = _anthropic(
        lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC_PARSE), asincrono=True
    )

    async def llamar():
        return await cliente.messages.parse(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            messages=[{"role": "user", "content": "x"}],
            output_format=Equipaje,
        )

    assert asyncio.run(llamar()).parsed_output.kilos == 8
    assert span_llm().llm.usage.input_tokens == 300


# ---------------------------------------------------------------------------------
# `with_raw_response`: la respuesta cruda
# ---------------------------------------------------------------------------------
#
# Quien quiere las cabeceras (los límites de tasa, el id de petición) llama por
# `with_raw_response`, y LiteLLM lo hace en todas sus llamadas a OpenAI. Nuestro parche
# se ejecuta igual, pero recibe la respuesta HTTP sin parsear: sin leerla, el span salía
# con cero tokens, **medido**, y la llamada costaba cero dólares. Es la mentira que este
# producto no puede decir.


def test_chat_con_respuesta_cruda_cuenta_los_tokens():
    cuerpo = json.loads(json.dumps(RESPUESTA_CHAT_PARSE))
    cliente = _openai(_json(cuerpo))
    crudo = cliente.chat.completions.with_raw_response.create(
        model=MODELO_OPENAI, messages=[{"role": "user", "content": "hola"}]
    )
    # Lo del usuario sigue funcionando igual: sus cabeceras y su objeto parseado.
    assert crudo.headers.get("content-type") == "application/json"
    assert crudo.parse().usage.prompt_tokens == 300

    span = span_llm()
    assert span.llm.usage.input_tokens == 300
    assert span.llm.usage.output_tokens == 9
    assert span.llm.usage.estimated is False
    assert span.llm.cost.total_usd > 0


def test_responses_con_respuesta_cruda_cuenta_los_tokens():
    cliente = _openai(_json(_respuesta()))
    crudo = cliente.responses.with_raw_response.create(model=MODELO_OPENAI, input="hola")
    assert crudo.parse().output_text == "Una maleta de mano."
    assert span_llm().llm.usage.input_tokens == 1200


def test_anthropic_con_respuesta_cruda_cuenta_los_tokens():
    cliente = _anthropic(lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC_PARSE))
    crudo = cliente.messages.with_raw_response.create(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
    )
    assert crudo.parse().usage.input_tokens == 300
    span = span_llm()
    assert span.llm.usage.input_tokens == 300
    assert span.llm.usage.estimated is False


def test_una_respuesta_que_no_se_puede_leer_no_cuesta_cero():
    """`with_streaming_response` entrega el cuerpo sin leer, para que el usuario lo lea a
    su ritmo; leerlo nosotros antes se lo cambiaría. Ahí no hay tokens que leer, y lo
    honrado es estimarlos y decirlo, no escribir un cero medido."""
    cliente = _openai(_json(RESPUESTA_CHAT_PARSE))
    with cliente.chat.completions.with_streaming_response.create(
        model=MODELO_OPENAI, messages=[{"role": "user", "content": "¿Cuánto equipaje?"}]
    ) as crudo:
        assert json.loads(crudo.read())["usage"]["prompt_tokens"] == 300

    uso = span_llm().llm.usage
    assert uso.estimated is True
    assert uso.input_tokens > 0


def test_anthropic_asincrono_con_respuesta_cruda_cuenta_los_tokens():
    """En el cliente asíncrono de Anthropic `.parse()` de la cruda es una corrutina."""
    cliente = _anthropic(
        lambda r: httpx2.Response(200, json=RESPUESTA_ANTHROPIC_PARSE), asincrono=True
    )

    async def llamar():
        crudo = await cliente.messages.with_raw_response.create(
            model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "x"}]
        )
        return await crudo.parse()

    assert asyncio.run(llamar()).usage.input_tokens == 300
    assert span_llm().llm.usage.input_tokens == 300
