"""Las integraciones, contra los SDK **de verdad** de OpenAI y Anthropic.

Hasta aquí sólo se probaban contra dobles escritos a mano: objetos con la forma que
nosotros creíamos que tenían las respuestas. Eso deja fuera una clase entera de fallos y
justo la que más caro sale, porque instrumentar es lo primero que toca un usuario:

* que el parche no llegue a la clase que de verdad usa el cliente (cambian de sitio);
* que un campo se renombre y lo leamos como `None` sin enterarnos;
* que el iterador de streaming cambie de forma y no acumulemos nada;
* que el cliente rechace nuestro `create` envuelto por la firma.

Aquí los clientes son los reales —`openai.OpenAI`, `anthropic.Anthropic`—, con su código
de parseo, sus modelos Pydantic y su lector de SSE. Lo único falso es el transporte HTTP,
que devuelve cuerpos con la forma que documenta cada proveedor. No hace falta clave ni se
gasta dinero, y aun así se recorre todo el camino salvo la red.

Las pruebas **contra la API real** —las que además comprueban que nuestros números
cuadran con lo que factura el proveedor— están al final y sólo corren con
`LAPLACE_LIVE_TESTS=1` y una clave en el entorno. Cuestan céntimos, pero cuestan.
"""

from __future__ import annotations

import json
import os

import pytest
from helpers import exporter, ingest

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
anthropic = pytest.importorskip("anthropic", reason="el extra [anthropic] no está instalado")

# Los dos SDK ya no comparten cliente HTTP: `anthropic` usa `httpx2`. Cada uno tiene que
# recibir el suyo, y descubrirlo tarde es exactamente el tipo de cosa que este fichero
# existe para pillar.
import httpx  # noqa: E402
import httpx2  # noqa: E402
from laplace.integrations import anthropic as ai  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402

MODELO_OPENAI = "gpt-4o-mini"
MODELO_ANTHROPIC = "claude-sonnet-4-5"


@pytest.fixture(autouse=True)
def instrumentado():
    """Parchea antes de cada prueba y deja el mundo como estaba al salir.

    Sin el `uninstrument`, el parche se quedaría puesto para el resto de la sesión y
    otros ficheros verían spans que no esperan.
    """
    oi.instrument()
    ai.instrument()
    yield
    oi.uninstrument()
    ai.uninstrument()


def _openai(handler) -> openai.OpenAI:
    return openai.OpenAI(
        api_key="sk-de-mentira",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _anthropic(handler) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="sk-de-mentira",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _json_openai(cuerpo: dict):
    return lambda request: httpx.Response(200, json=cuerpo)


def _json_anthropic(cuerpo: dict):
    return lambda request: httpx2.Response(200, json=cuerpo)


def _sse_openai(texto: str):
    return lambda request: httpx.Response(
        200, content=texto.encode(), headers={"content-type": "text/event-stream"}
    )


def _sse_anthropic(texto: str):
    return lambda request: httpx2.Response(
        200, content=texto.encode(), headers={"content-type": "text/event-stream"}
    )


def _span_llm():
    """El span de LLM que ha salido por la ingesta, ya en el contrato."""
    spans = [s for s in ingest() if s.type == "llm"]
    assert len(spans) == 1, f"se esperaba un span de LLM y hay {len(spans)}"
    return spans[0]


# ---------------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------------

RESPUESTA_OPENAI = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1770000000,
    "model": "gpt-4o-mini-2024-07-18",
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
        "prompt_tokens_details": {"cached_tokens": 1024, "audio_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0},
    },
}


def test_openai_real_una_llamada_normal():
    """El camino entero: cliente real → nuestro parche → span → ingesta → contrato."""
    cliente = _openai(_json_openai(RESPUESTA_OPENAI))
    respuesta = cliente.chat.completions.create(
        model=MODELO_OPENAI,
        messages=[
            {"role": "system", "content": "Eres un asistente de equipaje."},
            {"role": "user", "content": "¿Cuánto equipaje puedo llevar?"},
        ],
        temperature=0.2,
    )
    assert respuesta.choices[0].message.content == "Una maleta de mano."

    span = _span_llm()
    assert span.llm.request_model == MODELO_OPENAI
    # El modelo de la respuesta es el que factura, y no es el que se pidió.
    assert span.llm.response_model == "gpt-4o-mini-2024-07-18"
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.estimated is False, "los tokens los ha dado el proveedor"
    assert span.llm.params.get("temperature") == 0.2
    # Y el contenido llega entero, que es de lo que vive el diagnóstico.
    assert span.llm.input_messages[0]["content"] == "Eres un asistente de equipaje."
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."


def test_openai_real_la_cache_no_se_cuenta_dos_veces():
    """`prompt_tokens` de OpenAI **incluye** los cacheados. Si los sumáramos aparte, la
    entrada de cualquier agente con caché saldría inflada (D-050)."""
    cliente = _openai(_json_openai(RESPUESTA_OPENAI))
    cliente.chat.completions.create(
        model=MODELO_OPENAI, messages=[{"role": "user", "content": "hola"}]
    )
    uso = _span_llm().llm.usage
    assert uso.input_tokens == 1200
    assert uso.cached_input_tokens == 1024
    assert uso.uncached_input_tokens == 176


SSE_OPENAI = (
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1770000000,'
    '"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"role":"assistant",'
    '"content":"Una "},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1770000000,'
    '"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"content":"maleta '
    'de mano."},"finish_reason":"stop"}]}\n\n'
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1770000000,'
    '"model":"gpt-4o-mini-2024-07-18","choices":[],"usage":{"prompt_tokens":1200,'
    '"completion_tokens":12,"total_tokens":1212,'
    '"prompt_tokens_details":{"cached_tokens":1024}}}\n\n'
    "data: [DONE]\n\n"
)


def test_openai_real_streaming_con_usage():
    """En un agente de verdad la mayoría de las llamadas van en streaming. Si no se
    contaran, el coste estaría mal por defecto en cualquier carga real."""
    cliente = _openai(_sse_openai(SSE_OPENAI))
    trozos = list(
        cliente.chat.completions.create(
            model=MODELO_OPENAI,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    assert len(trozos) == 3, "el iterador del cliente real tiene que seguir funcionando"

    span = _span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.attributes.get("laplace.streaming") is True
    # El texto se acumula trozo a trozo: sin esto, una respuesta en streaming no se
    # puede leer en el explorador ni pasársela a un juez.
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."


def test_openai_real_streaming_sin_usage_estima_y_lo_dice():
    """Sin `include_usage` el proveedor no manda tokens. Se cuentan por nuestra cuenta y
    **se marca como estimado**: un coste que sale de dividir caracteres entre cuatro no
    es lo mismo que uno que viene de la factura."""
    lineas = [x for x in SSE_OPENAI.splitlines() if '"usage"' not in x]
    sin_usage = "\n".join(lineas) + "\n"
    cliente = _openai(_sse_openai(sin_usage))
    list(
        cliente.chat.completions.create(
            model=MODELO_OPENAI,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
            stream=True,
        )
    )
    uso = _span_llm().llm.usage
    assert uso.estimated is True
    assert uso.output_tokens > 0


def test_openai_real_un_error_del_proveedor_no_se_traga():
    """Si la llamada falla, la excepción tiene que llegar al usuario tal cual y el span
    tiene que quedar marcado como error. Observar no puede cambiar lo observado."""

    def falla(request):
        return httpx.Response(429, json={"error": {"message": "rate limit", "type": "rate_limit"}})

    cliente = _openai(falla)
    with pytest.raises(openai.RateLimitError):
        cliente.chat.completions.create(
            model=MODELO_OPENAI, messages=[{"role": "user", "content": "hola"}]
        )
    span = _span_llm()
    assert span.status == "error"
    assert "rate limit" in (span.status_message or "").lower()


# ---------------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------------

RESPUESTA_ANTHROPIC = {
    "id": "msg_abc123",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "content": [{"type": "text", "text": "Una maleta de mano."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 176,
        "output_tokens": 12,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1024,
    },
}


def test_anthropic_real_una_llamada_normal():
    cliente = _anthropic(_json_anthropic(RESPUESTA_ANTHROPIC))
    respuesta = cliente.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        system="Eres un asistente de equipaje.",
        messages=[{"role": "user", "content": "¿Cuánto equipaje puedo llevar?"}],
    )
    assert respuesta.content[0].text == "Una maleta de mano."

    span = _span_llm()
    assert span.llm.request_model == MODELO_ANTHROPIC
    assert span.llm.usage.output_tokens == 12
    # Anthropic devuelve la caché APARTE de `input_tokens`; el contrato la quiere
    # DENTRO, para que el mismo agente no cueste distinto según el proveedor (D-050).
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.input_tokens == 1200
    # El `system` va fuera de `messages` en esta API: si no se uniera, se perdería la
    # mitad del prompt y con ella la identidad del paso.
    roles = [m["role"] for m in span.llm.input_messages]
    assert "system" in roles


def test_anthropic_real_la_escritura_de_cache_se_cobra_aparte():
    """Escribir en caché cuesta más que la entrada normal (1,25x o 2x según duración).
    Si no se separase, la factura de cualquier agente con caché saldría mal."""
    cuerpo = json.loads(json.dumps(RESPUESTA_ANTHROPIC))
    cuerpo["usage"] = {
        "input_tokens": 100,
        "output_tokens": 12,
        "cache_creation_input_tokens": 2048,
        "cache_read_input_tokens": 0,
    }
    cliente = _anthropic(_json_anthropic(cuerpo))
    cliente.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=64,
        messages=[{"role": "user", "content": "hola"}],
    )
    uso = _span_llm().llm.usage
    assert uso.cache_write_tokens == 2048
    assert uso.input_tokens == 2148, "lo escrito en caché también es entrada facturable"


SSE_ANTHROPIC = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1",'
    '"type":"message","role":"assistant","model":"claude-sonnet-4-5","content":[],'
    # `input_tokens` de Anthropic NO incluye lo leído de caché: aquí son 176 nuevos y
    # 1024 de caché, que el contrato normaliza a 1200 de entrada facturable (D-050).
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


def test_anthropic_real_streaming():
    cliente = _anthropic(_sse_anthropic(SSE_ANTHROPIC))
    eventos = list(
        cliente.messages.create(
            model=MODELO_ANTHROPIC,
            max_tokens=64,
            messages=[{"role": "user", "content": "¿Cuánto equipaje?"}],
            stream=True,
        )
    )
    assert [e.type for e in eventos][0] == "message_start"

    span = _span_llm()
    assert span.llm.usage.input_tokens == 1200
    assert span.llm.usage.cached_input_tokens == 1024
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.estimated is False
    assert span.llm.output_messages[0]["content"] == "Una maleta de mano."


def test_anthropic_real_un_error_del_proveedor_no_se_traga():
    def falla(request):
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "mal"}},
        )

    cliente = _anthropic(falla)
    with pytest.raises(anthropic.BadRequestError):
        cliente.messages.create(
            model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "hola"}]
        )
    assert _span_llm().status == "error"


# ---------------------------------------------------------------------------------
# Contra la API real. Cuestan dinero, así que hay que pedirlas.
# ---------------------------------------------------------------------------------

VIVO = os.getenv("LAPLACE_LIVE_TESTS") == "1"

vivo = pytest.mark.skipif(
    not VIVO,
    reason=(
        "pruebas contra la API real. Se encienden con LAPLACE_LIVE_TESTS=1 y la clave "
        "del proveedor en el entorno (OPENAI_API_KEY / ANTHROPIC_API_KEY). Gastan unos "
        "céntimos por ejecución."
    ),
)

#: Tope de tokens de salida por llamada viva. Con esto, las seis llamadas de aquí
#: cuestan menos de un céntimo en los modelos pequeños. Un tope no es cosmético: estas
#: pruebas pueden acabar en un CI que corra cien veces al día.
MAX_TOKENS_VIVOS = 32


@vivo
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY")
def test_vivo_openai_los_tokens_cuadran_con_los_del_proveedor():
    """Lo único que estas pruebas añaden sobre las de arriba, y es lo importante: que
    los números que guardamos son los que el proveedor dice que ha cobrado."""
    cliente = openai.OpenAI()
    respuesta = cliente.chat.completions.create(
        model=MODELO_OPENAI,
        max_tokens=MAX_TOKENS_VIVOS,
        messages=[{"role": "user", "content": "Responde sólo con la palabra: hola"}],
    )
    span = _span_llm()
    assert span.llm.usage.input_tokens == respuesta.usage.prompt_tokens
    assert span.llm.usage.output_tokens == respuesta.usage.completion_tokens
    assert span.llm.response_model == respuesta.model
    assert span.llm.cost.total_usd > 0, "con tarifa conocida, la llamada tiene que costar"
    assert span.llm.cost.unknown is False


@vivo
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY")
def test_vivo_openai_streaming_cuadra_con_el_no_streaming():
    """La misma llamada, en streaming y sin streaming, tiene que costar lo mismo. Es la
    promesa de D-016 comprobada contra la API de verdad y no contra un doble."""
    cliente = openai.OpenAI()
    mensajes = [{"role": "user", "content": "Responde sólo con la palabra: hola"}]

    cliente.chat.completions.create(
        model=MODELO_OPENAI, max_tokens=MAX_TOKENS_VIVOS, messages=mensajes
    )
    normal = _span_llm()
    exporter.clear()

    list(
        cliente.chat.completions.create(
            model=MODELO_OPENAI,
            max_tokens=MAX_TOKENS_VIVOS,
            messages=mensajes,
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    en_streaming = _span_llm()

    assert en_streaming.llm.usage.estimated is False
    assert en_streaming.llm.usage.input_tokens == normal.llm.usage.input_tokens
    # La salida puede variar un token entre dos llamadas: lo que no puede variar es el
    # orden de magnitud ni que una de las dos salga a cero.
    assert en_streaming.llm.usage.output_tokens > 0
    assert en_streaming.llm.cost.total_usd > 0


@vivo
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="falta ANTHROPIC_API_KEY")
def test_vivo_anthropic_los_tokens_cuadran_con_los_del_proveedor():
    cliente = anthropic.Anthropic()
    respuesta = cliente.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=MAX_TOKENS_VIVOS,
        messages=[{"role": "user", "content": "Responde sólo con la palabra: hola"}],
    )
    span = _span_llm()
    # El contrato mete la caché DENTRO de la entrada, así que se compara sumando lo que
    # Anthropic devuelve por separado. Si esta suma dejara de cuadrar, el mismo agente
    # costaría distinto según el proveedor, que es justo lo que D-050 evita.
    esperado = (
        respuesta.usage.input_tokens
        + (respuesta.usage.cache_read_input_tokens or 0)
        + (respuesta.usage.cache_creation_input_tokens or 0)
    )
    assert span.llm.usage.input_tokens == esperado
    assert span.llm.usage.output_tokens == respuesta.usage.output_tokens
    assert span.llm.cost.total_usd > 0
    assert span.llm.cost.unknown is False


@vivo
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="falta ANTHROPIC_API_KEY")
def test_vivo_anthropic_streaming():
    cliente = anthropic.Anthropic()
    with cliente.messages.stream(
        model=MODELO_ANTHROPIC,
        max_tokens=MAX_TOKENS_VIVOS,
        messages=[{"role": "user", "content": "Responde sólo con la palabra: hola"}],
    ) as flujo:
        flujo.get_final_message()

    span = _span_llm()
    assert span.llm.usage.output_tokens > 0
    assert span.llm.usage.estimated is False, "Anthropic manda el uso en el propio flujo"
    assert span.llm.cost.total_usd > 0
