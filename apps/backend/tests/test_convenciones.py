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
