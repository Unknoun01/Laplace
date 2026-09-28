"""Trazas de OpenInference y OpenLLMetry, por el camino real: OTel → protobuf → ingesta.

Cada span se emite con los atributos tal y como los escriben esos instrumentadores, sin
nada de Laplace encima, que es como llegan de un agente con LangGraph, CrewAI o el Agents
SDK de OpenAI. Antes llegaban como `chain` sin modelo ni coste (D-136).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from helpers import exporter, ingest
from opentelemetry import trace as otel_trace

from laplace_backend import insights
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

MANUAL = "Eres el asistente de Vuelos Laplace. Responde usando el manual. " * 20


def _emitir(nombre: str, atributos: dict) -> None:
    tracer = otel_trace.get_tracer("instrumentador-ajeno")
    with tracer.start_as_current_span(nombre) as span:
        for clave, valor in atributos.items():
            span.set_attribute(clave, valor)


def _openinference_llm(pregunta: str = "¿Cuánto equipaje?", salida: str = "Diez kilos.") -> dict:
    return {
        "openinference.span.kind": "LLM",
        "llm.model_name": "gpt-5.6-terra",
        "llm.provider": "openai",
        "llm.token_count.prompt": 1_200,
        "llm.token_count.completion": 12,
        "llm.token_count.prompt_details.cache_read": 1_000,
        "llm.invocation_parameters": json.dumps({"temperature": 0, "max_tokens": 50}),
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": MANUAL,
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": pregunta,
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.contents.0.message_content.text": salida,
        "session.id": "conv-7",
        "user.id": "cliente-3",
    }


def test_una_llamada_de_openinference_llega_con_modelo_tokens_y_coste():
    _emitir("ChatCompletion", _openinference_llm())
    (span,) = ingest()

    assert span.type == "llm"
    assert span.llm.request_model == "gpt-5.6-terra"
    assert span.llm.usage.input_tokens == 1_200
    assert span.llm.usage.output_tokens == 12
    assert span.llm.usage.cached_input_tokens == 1_000
    assert span.llm.cost.total_usd > 0 and not span.llm.cost.unknown
    assert [m["role"] for m in span.llm.input_messages] == ["system", "user"]
    assert span.llm.output_messages[0]["content"] == "Diez kilos."
    assert span.llm.params.get("temperature") == 0
    assert span.session_id == "conv-7" and span.user_id == "cliente-3"
    assert span.step_hint.startswith("Eres el asistente"), "la identidad de paso sale del prompt"
    assert not any(k.startswith("llm.input_messages.") for k in span.attributes), (
        "los mensajes aplanados no se guardan otra vez en crudo"
    )


def test_una_herramienta_de_openinference_es_una_herramienta():
    _emitir(
        "buscar_tarifa",
        {
            "openinference.span.kind": "TOOL",
            "tool.name": "buscar_tarifa",
            "input.value": json.dumps({"ruta": "MAD-BCN"}),
            "output.value": "tarifa-basica",
        },
    )
    (span,) = ingest()
    assert span.type == "tool"
    assert span.tool.name == "buscar_tarifa"
    assert span.tool.arguments == {"ruta": "MAD-BCN"}
    assert span.tool.output == "tarifa-basica"


def test_una_cadena_de_openinference_guarda_su_entrada_y_salida():
    _emitir(
        "LangGraph",
        {"openinference.span.kind": "AGENT", "input.value": "hola", "output.value": "adiós"},
    )
    (span,) = ingest()
    assert span.type == "agent"
    assert span.input == "hola" and span.output == "adiós"


def test_una_llamada_de_openllmetry_llega_con_modelo_tokens_y_coste():
    _emitir(
        "openai.chat",
        {
            "gen_ai.system": "openai",
            "llm.request.type": "chat",
            "gen_ai.request.model": "gpt-5.6-terra",
            "gen_ai.response.model": "gpt-5.6-terra",
            "gen_ai.usage.prompt_tokens": 900,
            "gen_ai.usage.completion_tokens": 7,
            "gen_ai.prompt.0.role": "system",
            "gen_ai.prompt.0.content": MANUAL,
            "gen_ai.prompt.1.role": "user",
            "gen_ai.prompt.1.content": "¿Puedo llevar líquidos?",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "Hasta 100 ml.",
            "gen_ai.completion.0.finish_reason": "stop",
            "traceloop.association.properties.session_id": "conv-9",
        },
    )
    (span,) = ingest()
    assert span.type == "llm"
    assert span.llm.operation == "chat"
    assert span.llm.usage.input_tokens == 900 and span.llm.usage.output_tokens == 7
    assert span.llm.cost.total_usd > 0
    assert span.llm.finish_reasons == ["stop"]
    assert span.llm.output_messages[0]["content"] == "Hasta 100 ml."
    assert span.session_id == "conv-9"


def test_una_tarea_de_openllmetry_es_un_paso_con_su_entrada():
    _emitir(
        "reservar.task",
        {
            "traceloop.span.kind": "tool",
            "traceloop.entity.name": "reservar",
            "traceloop.entity.input": json.dumps({"vuelo": "IB123"}),
            "traceloop.entity.output": json.dumps({"ok": True}),
        },
    )
    (span,) = ingest()
    assert span.type == "tool" and span.tool.name == "reservar"
    assert span.tool.arguments == {"vuelo": "IB123"}


def test_la_cache_separada_se_suma_a_la_entrada():
    """Algunas versiones dan con Anthropic la entrada nueva aparte de la caché. Nuestro
    `input_tokens` es el total facturable: si lo cacheado no cabe dentro, se suma."""
    _emitir(
        "anthropic.chat",
        {
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": "claude-sonnet-5",
            "gen_ai.usage.prompt_tokens": 100,
            "gen_ai.usage.completion_tokens": 20,
            "gen_ai.usage.cache_read_input_tokens": 3_000,
            "gen_ai.usage.cache_creation_input_tokens": 0,
        },
    )
    (span,) = ingest()
    assert span.llm.usage.input_tokens == 3_100
    assert span.llm.usage.cached_input_tokens == 3_000


def test_la_cache_con_los_nombres_nuevos_de_opentelemetry():
    _emitir(
        "chat claude-sonnet-5",
        {
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": "claude-sonnet-5",
            "gen_ai.usage.input_tokens": 4_000,
            "gen_ai.usage.output_tokens": 30,
            "gen_ai.usage.cache_read.input_tokens": 3_500,
        },
    )
    (span,) = ingest()
    assert span.llm.usage.cached_input_tokens == 3_500
    assert span.llm.usage.input_tokens == 4_000, "cabía dentro: no se suma"


def _openllmetry_js(instrucciones: str) -> dict:
    """Lo que manda OpenLLMetry-js 0.27 con OpenAI, copiado de una traza de verdad: ya
    usa las convenciones GenAI nuevas de OpenTelemetry, con el proveedor en
    `gen_ai.provider.name` y los mensajes en `parts` en lugar de `content`."""
    return {
        "gen_ai.provider.name": "openai",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-5.6-luna",
        "gen_ai.response.model": "gpt-5.6-luna",
        "traceloop.workflow.name": "atender_ticket",
        "gen_ai.usage.input_tokens": 1_200,
        "gen_ai.usage.output_tokens": 12,
        "gen_ai.input.messages": json.dumps(
            [
                {"role": "system", "parts": [{"type": "text", "content": instrucciones}]},
                {"role": "user", "parts": [{"type": "text", "content": "¿Equipaje?"}]},
            ]
        ),
        "gen_ai.output.messages": json.dumps(
            [
                {
                    "role": "assistant",
                    "finish_reason": "stop",
                    "parts": [{"type": "text", "content": "Una maleta."}],
                }
            ]
        ),
    }


def test_las_convenciones_nuevas_dicen_el_proveedor():
    """`gen_ai.system` pasó a llamarse `gen_ai.provider.name`."""
    _emitir("chat gpt-5.6-luna", _openllmetry_js("Clasifica el ticket."))
    (span,) = ingest()
    assert span.llm.system == "openai"


def test_las_instrucciones_en_parts_hacen_la_identidad_del_paso():
    """Con los mensajes en `parts`, la huella no veía las instrucciones: dos pasos con
    prompts distintos caían en uno, y la pista legible salía vacía."""
    _emitir("chat gpt-5.6-luna", _openllmetry_js("Clasifica el ticket."))
    _emitir("chat gpt-5.6-luna", _openllmetry_js("Resume el ticket."))
    spans = ingest()
    assert len({s.step_key for s in spans}) == 2
    assert {s.step_hint for s in spans} == {"Clasifica el ticket.", "Resume el ticket."}


def _genai_actual(**cambios) -> dict:
    """Lo que mandan el AI SDK de Vercel 7 (con `@ai-sdk/otel`) y OpenLLMetry-js 0.27 con
    Anthropic, copiado de trazas de verdad (D-165): las convenciones GenAI actuales,
    con las instrucciones APARTE, en `gen_ai.system_instructions`, y los mensajes en
    `parts`."""
    atributos = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "claude-haiku-4-5",
        "gen_ai.response.model": "claude-haiku-4-5",
        "gen_ai.system_instructions": json.dumps(
            [{"type": "text", "content": "Eres un asistente de equipaje."}]
        ),
        "gen_ai.input.messages": json.dumps(
            [{"role": "user", "parts": [{"type": "text", "content": "¿Cuánto equipaje?"}]}]
        ),
        "gen_ai.output.messages": json.dumps(
            [
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": "Una maleta de mano."}],
                    "finish_reason": "stop",
                }
            ]
        ),
        "gen_ai.response.finish_reasons": ["stop"],
        "gen_ai.usage.input_tokens": 1_200,
        "gen_ai.usage.output_tokens": 12,
    }
    atributos.update(cambios)
    return atributos


def test_las_instrucciones_aparte_son_el_mensaje_de_sistema():
    """Con las convenciones actuales el prompt de sistema no va entre los mensajes sino
    en `gen_ai.system_instructions`. Sin leerlo, el paso perdía la mitad de su identidad:
    todos los pasos con la misma pregunta de usuario caían en uno."""
    _emitir("chat claude-haiku-4-5", _genai_actual())
    _emitir(
        "chat claude-haiku-4-5",
        _genai_actual(
            **{
                "gen_ai.system_instructions": json.dumps(
                    [{"type": "text", "content": "Eres un clasificador de tickets."}]
                )
            }
        ),
    )
    spans = ingest()
    assert spans[0].llm.input_messages[0] == {
        "role": "system",
        "content": "Eres un asistente de equipaje.",
    }
    assert len({s.step_key for s in spans}) == 2
    assert {s.step_hint for s in spans} == {
        "Eres un asistente de equipaje.",
        "Eres un clasificador de tickets.",
    }
    assert "gen_ai.system_instructions" not in spans[0].attributes, (
        "ya está en los mensajes: no se guarda otra vez en crudo"
    )


def test_las_instrucciones_aparte_no_duplican_un_sistema_que_ya_viene():
    atributos = _genai_actual()
    atributos["gen_ai.input.messages"] = json.dumps(
        [
            {"role": "system", "parts": [{"type": "text", "content": "Eres un asistente."}]},
            {"role": "user", "parts": [{"type": "text", "content": "¿Cuánto equipaje?"}]},
        ]
    )
    _emitir("chat", atributos)
    (span,) = ingest()
    assert [m["role"] for m in span.llm.input_messages] == ["system", "user"]


def test_los_mensajes_en_parts_se_leen_como_texto():
    """Guardados tal cual, sin `content`, la interfaz los enseñaba vacíos y las reglas
    que miran el texto no tenían nada que mirar."""
    _emitir("chat claude-haiku-4-5", _genai_actual())
    (span,) = ingest()
    assert span.llm.input_messages[1] == {"role": "user", "content": "¿Cuánto equipaje?"}
    assert span.llm.output_messages == [
        {"role": "assistant", "content": "Una maleta de mano.", "finish_reason": "stop"}
    ]


def test_las_llamadas_a_herramientas_en_parts():
    """`tool_call` y `tool_call_response` pasan a la forma que escribe el SDK de Python
    (la de OpenAI), para que una herramienta se lea igual venga de donde venga."""
    atributos = _genai_actual()
    atributos["gen_ai.input.messages"] = json.dumps(
        [
            {"role": "user", "parts": [{"type": "text", "content": "¿Tarifa MAD-BCN?"}]},
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "buscar_tarifa",
                        "arguments": {"ruta": "MAD-BCN"},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [{"type": "tool_call_response", "id": "call_1", "response": "básica"}],
            },
        ]
    )
    _emitir("chat", atributos)
    (span,) = ingest()
    _sistema, _usuario, asistente, herramienta = span.llm.input_messages
    assert asistente["role"] == "assistant"
    assert asistente["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "buscar_tarifa", "arguments": '{"ruta": "MAD-BCN"}'},
        }
    ]
    assert herramienta == {"role": "tool", "tool_call_id": "call_1", "content": "básica"}


def test_las_partes_que_no_son_texto_no_se_pierden():
    atributos = _genai_actual()
    atributos["gen_ai.input.messages"] = json.dumps(
        [
            {
                "role": "user",
                "parts": [
                    {"type": "text", "content": "¿Cabe esta maleta?"},
                    {"type": "blob", "modality": "image", "mime_type": "image/png", "content": "…"},
                ],
            }
        ]
    )
    _emitir("chat", atributos)
    (span,) = ingest()
    usuario = span.llm.input_messages[1]
    assert usuario["content"] == "¿Cabe esta maleta?"
    assert usuario["parts"][1]["modality"] == "image", "lo que no es texto se guarda entero"


def test_los_mensajes_en_parts_se_leen_sin_nada_mas_que_los_delate():
    """Un span con sólo las convenciones GenAI (sin caché, sin instrucciones aparte, sin
    atributos de ninguna familia), como los de la instrumentación oficial de
    OpenTelemetry para OpenAI: los `parts` tienen que bastar para traducirlo."""
    _emitir(
        "chat gpt-5.6-luna",
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-5.6-luna",
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 5,
            "gen_ai.input.messages": json.dumps(
                [{"role": "user", "parts": [{"type": "text", "content": "Hola"}]}]
            ),
        },
    )
    (span,) = ingest()
    assert span.llm.input_messages == [{"role": "user", "content": "Hola"}]


def test_lo_nuestro_en_content_no_se_toca():
    """El SDK de Python escribe `gen_ai.input.messages` con `content`: nada cambia."""
    mensajes = [{"role": "system", "content": "Hola"}, {"role": "user", "content": "x"}]
    _emitir(
        "chat",
        {
            "gen_ai.request.model": "gpt-5.6-luna",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 1,
            "gen_ai.input.messages": json.dumps(mensajes),
        },
    )
    (span,) = ingest()
    assert span.llm.input_messages == mensajes


def _openinference_js_anthropic() -> dict:
    """Lo que manda `@arizeai/openinference-instrumentation-anthropic` 0.2.8, copiado de
    una traza de verdad (D-165): el `system` de Anthropic no va entre los mensajes sino
    dentro de `llm.invocation_parameters`, y el motivo de parada, en singular."""
    return {
        "openinference.span.kind": "LLM",
        "llm.model_name": "claude-haiku-4-5",
        "llm.request.model_name": "claude-haiku-4-5",
        "llm.response.model_name": "claude-haiku-4-5-20251001",
        "llm.system": "anthropic",
        "llm.provider": "anthropic",
        "llm.finish_reason": "end_turn",
        "llm.invocation_parameters": json.dumps(
            {"model": "claude-haiku-4-5", "max_tokens": 64, "system": MANUAL, "stream": True}
        ),
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "¿Cuánto equipaje?",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "Una maleta de mano.",
        "llm.token_count.prompt": 1_200,
        "llm.token_count.completion": 12,
        "llm.token_count.prompt_details.cache_read": 1_024,
    }


def test_el_system_de_anthropic_en_los_parametros_de_openinference():
    _emitir("Anthropic Messages", _openinference_js_anthropic())
    (span,) = ingest()
    assert span.llm.input_messages[0] == {"role": "system", "content": MANUAL}
    assert span.step_hint.startswith("Eres el asistente")
    assert span.llm.response_model == "claude-haiku-4-5-20251001"
    assert span.llm.finish_reasons == ["end_turn"]


def test_el_system_de_anthropic_en_bloques():
    atributos = _openinference_js_anthropic()
    atributos["llm.invocation_parameters"] = json.dumps(
        {
            "model": "claude-haiku-4-5",
            "system": [
                {"type": "text", "text": "Eres un asistente. "},
                {"type": "text", "text": "Responde breve.", "cache_control": {"type": "ephemeral"}},
            ],
        }
    )
    _emitir("Anthropic Messages", atributos)
    (span,) = ingest()
    assert span.llm.input_messages[0] == {
        "role": "system",
        "content": "Eres un asistente. Responde breve.",
    }


def test_la_respuesta_de_langchain_js_sale_de_sus_generaciones():
    """`@arizeai/openinference-instrumentation-langchain` 4.1 sólo manda el rol de la
    respuesta cuando el contenido de LangChain es una lista (con la Responses API de
    OpenAI, siempre). El texto está en `output.value`, en la forma `generations` de
    LangChain (D-165)."""
    atributos = _openinference_llm()
    del atributos["llm.output_messages.0.message.contents.0.message_content.text"]
    atributos["output.value"] = json.dumps(
        {"generations": [[{"text": "Diez kilos.", "message": {"lc": 1}}]], "llmOutput": {}}
    )
    atributos["output.mime_type"] = "application/json"
    _emitir("ChatOpenAI", atributos)
    (span,) = ingest()
    assert span.llm.output_messages == [{"role": "assistant", "content": "Diez kilos."}]


def test_el_nodo_de_langgraph_es_el_sitio_del_paso():
    """OpenInference no dice desde dónde se llama, pero con LangGraph deja el nodo en
    `metadata.langgraph_node`. Sin él, dos nodos con el mismo prompt de sistema se
    juntaban en un paso, que es lo que se vio con un agente de verdad (D-141)."""
    for nodo in ("clasificar", "responder"):
        atributos = _openinference_llm()
        atributos["metadata"] = json.dumps({"langgraph_node": nodo, "langgraph_step": 1})
        _emitir("ChatOpenAI", atributos)
    spans = ingest()
    assert len({s.step_key for s in spans}) == 2
    assert {s.step_label for s in spans} == {"clasificar", "responder"}


def test_el_nodo_de_langgraph_no_pisa_el_sitio_del_sdk():
    atributos = _openinference_llm()
    atributos["metadata"] = json.dumps({"langgraph_node": "agent"})
    atributos["laplace.step.parent"] = "atender_ticket"
    _emitir("ChatOpenAI", atributos)
    (span,) = ingest()
    assert span.step_label == "atender_ticket"


def test_lo_nuestro_manda_sobre_lo_ajeno():
    """Un span con los dos juegos de atributos: los de Laplace no se pisan."""
    atributos = _openinference_llm()
    atributos["gen_ai.usage.input_tokens"] = 1_500
    atributos["laplace.span.type"] = "llm"
    _emitir("mixto", atributos)
    (span,) = ingest()
    assert span.llm.usage.input_tokens == 1_500


def test_las_reglas_funcionan_sobre_trazas_de_openinference(tmp_path):
    """Lo que importa al final: que el motor encuentre derroche en un agente que nunca
    ha visto el SDK de Laplace. Tres llamadas idénticas por ejecución son una repetición."""
    for _ in range(4):
        tracer = otel_trace.get_tracer("instrumentador-ajeno")
        with tracer.start_as_current_span("agente") as raiz:
            raiz.set_attribute("openinference.span.kind", "AGENT")
            for _ in range(3):
                _emitir("ChatCompletion", _openinference_llm("¿Cuánto equipaje?"))
    spans = ingest()
    ahora = datetime.now(timezone.utc)
    for s in spans:
        s.project_id = "ajeno"
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(spans)

    ventana = Window(since=ahora - timedelta(hours=1), until=ahora + timedelta(hours=1), days=1)
    tipos = {f.kind for f in insights.detect(store, "ajeno", ventana)}
    assert "repeticion" in tipos, tipos


@pytest.fixture(autouse=True)
def _limpio():
    exporter.clear()
    yield
    exporter.clear()
