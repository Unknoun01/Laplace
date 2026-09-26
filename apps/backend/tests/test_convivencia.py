"""`laplace.init()` junto a otro instrumentador de LLM: cada llamada, una vez.

Con LangGraph instrumentado por OpenInference y `laplace.init()` a la vez, cada llamada
al modelo salía dos veces —una del span de LangChain y otra de nuestro parche de
OpenAI—, en trazas distintas y sin nada que permita emparejarlas en la ingesta: el gasto
del panel, el doble (D-141). Cuando hay otro instrumentador de LLM activo, el nuestro
cede y lo dice en el log.
"""

from __future__ import annotations

import logging
import sys
import types

import httpx
import pytest
from helpers import ingest
from laplace import _tracer
from laplace.integrations import _common as c
from laplace.integrations import openai as oi
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor

openai = pytest.importorskip("openai")

RESPUESTA = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1770000000,
    "model": "gpt-5.6-luna",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hola"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}


def _instrumentador_falso(nombre_modulo: str, monkeypatch) -> BaseInstrumentor:
    """Un instrumentador con la forma de los de verdad, en un módulo con su nombre."""

    class FalsoInstrumentor(BaseInstrumentor):
        def instrumentation_dependencies(self):
            return []

        def _instrument(self, **kwargs):
            pass

        def _uninstrument(self, **kwargs):
            pass

    modulo = types.ModuleType(nombre_modulo)
    modulo.FalsoInstrumentor = FalsoInstrumentor
    monkeypatch.setitem(sys.modules, nombre_modulo, modulo)
    return FalsoInstrumentor()


@pytest.fixture(autouse=True)
def limpio():
    oi.instrument()
    c._avisado.clear()
    yield
    oi.uninstrument()
    _tracer.get_config().defer_to_others = True


def _llamar():
    cliente = openai.OpenAI(
        api_key="x",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=RESPUESTA))
        ),
    )
    respuesta = cliente.chat.completions.create(
        model="gpt-5.6-luna", messages=[{"role": "user", "content": "hola"}]
    )
    assert respuesta.choices[0].message.content == "hola", "la llamada del usuario, intacta"
    return [s for s in ingest() if s.type == "llm"]


def test_sin_otro_instrumentador_se_traza_como_siempre():
    assert len(_llamar()) == 1


@pytest.mark.parametrize(
    "modulo",
    [
        "openinference.instrumentation.langchain",
        "openinference.instrumentation.openai",
        "opentelemetry.instrumentation.openai",  # OpenLLMetry
        "opentelemetry.instrumentation.langchain",
    ],
)
def test_con_otro_instrumentador_de_llm_activo_se_cede(modulo, monkeypatch, caplog):
    otro = _instrumentador_falso(modulo, monkeypatch)
    otro.instrument()
    try:
        with caplog.at_level(logging.WARNING, logger="laplace"):
            assert _llamar() == [], "esa llamada ya la traza el otro: no se cuenta dos veces"
            _llamar()
        avisos = [r for r in caplog.records if "FalsoInstrumentor" in r.getMessage()]
        assert len(avisos) == 1, "se avisa una vez, no en cada llamada"
        assert "defer_to_others" in avisos[0].getMessage(), "y se dice cómo evitarlo"
    finally:
        otro.uninstrument()


def test_un_instrumentador_importado_pero_apagado_no_cuenta(monkeypatch):
    otro = _instrumentador_falso("openinference.instrumentation.langchain", monkeypatch)
    otro.instrument()
    otro.uninstrument()
    assert len(_llamar()) == 1


def test_un_instrumentador_que_no_es_de_llm_no_cuenta(monkeypatch):
    """`requests` o `httpx` instrumentados no trazan llamadas al modelo."""
    otro = _instrumentador_falso("opentelemetry.instrumentation.httpx", monkeypatch)
    otro.instrument()
    try:
        assert len(_llamar()) == 1
    finally:
        otro.uninstrument()


def test_se_puede_forzar_que_no_ceda(monkeypatch):
    """Quien manda el otro instrumentador a otra parte y quiere estas llamadas también
    en Laplace lo pide a propósito: ahí contar dos veces es cosa suya."""
    otro = _instrumentador_falso("openinference.instrumentation.langchain", monkeypatch)
    otro.instrument()
    try:
        _tracer.get_config().defer_to_others = False
        assert len(_llamar()) == 1
    finally:
        otro.uninstrument()


def test_init_y_el_entorno_leen_la_opcion(monkeypatch):
    from laplace.config import LaplaceConfig

    monkeypatch.setenv("LAPLACE_DEFER_TO_OTHERS", "false")
    assert LaplaceConfig.from_env().defer_to_others is False
    monkeypatch.delenv("LAPLACE_DEFER_TO_OTHERS")
    assert LaplaceConfig.from_env().defer_to_others is True
