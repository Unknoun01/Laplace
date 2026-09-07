"""Round-trip real: SDK -> spans OTel -> protobuf OTLP -> contrato de traza.

Es la prueba que importa del contrato: si el SDK y la ingesta se desincronizan, aquí
se rompe. No necesita ni ClickHouse ni claves de API.
"""

from __future__ import annotations

import pytest
from helpers import exporter, ingest
from laplace import decorators, manual

from laplace_backend.tree import build_tree, summarize

# ---------------------------------------------------------------------------------


def test_observe_produce_un_span_tipado_con_entrada_y_salida():
    @decorators.observe(type="tool")
    def buscar(query: str, top: int = 3):
        return ["a", "b"]

    buscar("vuelos a bcn")

    spans = ingest()
    assert len(spans) == 1
    span = spans[0]
    assert span.type == "tool"
    assert span.name == "buscar"
    assert span.project_id == "test-project"
    assert span.status == "ok"
    assert span.tool is not None
    assert span.tool.name == "buscar"
    assert span.tool.arguments == {"query": "vuelos a bcn", "top": 3}
    assert span.tool.output == ["a", "b"]


def test_llm_span_guarda_tokens_separados_y_calcula_coste():
    mensajes = [{"role": "user", "content": "hola"}]
    with manual.llm_span(model="gpt-4o-mini", system="openai", input_messages=mensajes,
                         temperature=0.5) as llm:
        llm.record_response(
            output_messages=[{"role": "assistant", "content": "qué tal"}],
            input_tokens=1000,
            output_tokens=500,
            response_model="gpt-4o-mini-2024-07-18",
            finish_reasons=["stop"],
        )

    span = ingest()[0]
    assert span.type == "llm"
    assert span.name == "chat gpt-4o-mini"
    assert span.llm is not None
    # Entrada y salida por separado: sin esto no hay panel de ahorro.
    assert span.llm.usage.input_tokens == 1000
    assert span.llm.usage.output_tokens == 500
    # El modelo exacto, no una familia.
    assert span.llm.request_model == "gpt-4o-mini"
    assert span.llm.response_model == "gpt-4o-mini-2024-07-18"
    # Coste desglosado, resuelto por prefijo contra la tabla de precios.
    assert span.llm.cost.unknown is False
    # La tarifa aplicada queda anotada, para poder auditar el cálculo meses después.
    assert span.llm.cost.rate.startswith("gpt-4o-mini @ ")
    assert span.llm.cost.input_usd == pytest.approx(1000 * 0.15 / 1e6)
    assert span.llm.cost.output_usd == pytest.approx(500 * 0.6 / 1e6)
    assert span.llm.cost.total_usd == pytest.approx(
        span.llm.cost.input_usd + span.llm.cost.output_usd
    )
    # Los mensajes llegan enteros, no truncados.
    assert span.llm.input_messages == mensajes
    assert span.llm.params["temperature"] == 0.5


def test_un_modelo_sin_tarifa_no_cuesta_cero_sino_desconocido():
    """Un 0 silencioso se suma a los totales y los corrompe sin que nadie se entere."""
    with manual.llm_span(model="modelo-inventado-7b", input_messages=[]) as llm:
        llm.record_response(input_tokens=100, output_tokens=50)

    span = ingest()[0]
    assert span.llm.cost.unknown is True
    assert span.llm.cost.rate == ""


def test_una_version_nueva_no_hereda_la_tarifa_de_la_anterior():
    """Regresión: `claude-opus-4-5` cuesta un tercio que `claude-opus-4`.

    Dejar que el prefijo colara habría cobrado el triple en silencio, y ese número es
    el que vende el producto.
    """
    with manual.llm_span(model="claude-opus-4-5", input_messages=[]) as llm:
        llm.record_response(input_tokens=1_000_000, output_tokens=0)
    with manual.llm_span(model="claude-opus-4", input_messages=[]) as llm:
        llm.record_response(input_tokens=1_000_000, output_tokens=0)

    nuevo, viejo = ingest()
    assert nuevo.llm.cost.total_usd == pytest.approx(5.0)
    assert viejo.llm.cost.total_usd == pytest.approx(15.0)

    # Y una versión futura que aún no está en la tabla no hereda nada: es desconocida.
    exporter.clear()
    with manual.llm_span(model="claude-opus-9", input_messages=[]) as llm:
        llm.record_response(input_tokens=1000, output_tokens=10)
    assert ingest()[0].llm.cost.unknown is True


def test_las_llamadas_repetidas_comparten_dedup_hash():
    @decorators.observe(type="tool")
    def buscar(origen: str, destino: str):
        return ["IB3421"]

    buscar("MAD", "BCN")
    buscar("MAD", "BCN")
    buscar("MAD", "VLC")

    hashes = [s.dedup_hash for s in ingest()]
    assert hashes[0] == hashes[1], "mismos argumentos => misma llamada repetida"
    assert hashes[0] != hashes[2], "argumentos distintos => llamada distinta"


def test_una_excepcion_deja_el_span_en_error_con_el_evento():
    @decorators.observe(type="tool")
    def reservar():
        raise RuntimeError("el proveedor rechazó la reserva")

    with pytest.raises(RuntimeError):
        reservar()

    span = ingest()[0]
    assert span.status == "error"
    assert "rechazó la reserva" in span.status_message
    assert any(e.name == "exception" for e in span.events)
    assert span.events[0].attributes["exception.type"] == "RuntimeError"


def test_el_arbol_anida_los_spans_y_acumula_el_coste_del_subarbol():
    @decorators.observe(type="chain")
    def paso():
        with manual.llm_span(model="gpt-4o-mini", input_messages=[]) as llm:
            llm.record_response(input_tokens=1_000_000, output_tokens=0)

    @decorators.observe(type="agent")
    def agente():
        paso()
        paso()

    agente()

    spans = ingest()
    roots = build_tree(spans)
    assert len(roots) == 1

    raiz = roots[0]
    assert raiz.span.type == "agent"
    assert len(raiz.children) == 2
    assert raiz.children[0].span.type == "chain"
    assert raiz.children[0].children[0].span.type == "llm"

    # El nodo raíz no gasta nada por sí mismo, pero su subárbol sí.
    assert raiz.span.cost.total_usd == 0.0
    assert raiz.subtree.cost_usd == pytest.approx(2 * 0.15)  # 2 x 1M tokens a 0,15 $/1M
    assert raiz.subtree.span_count == 5

    resumen = summarize(spans, spans[0].trace_id)
    assert resumen.span_count == 5
    assert resumen.llm_call_count == 2
    assert resumen.status == "ok"
    assert resumen.cost.total_usd == pytest.approx(0.30)


def test_los_spans_huerfanos_se_cuelgan_de_la_raiz_en_vez_de_perderse():
    @decorators.observe(type="agent")
    def agente():
        pass

    agente()
    spans = ingest()
    # Se simula que el span padre aún no ha llegado.
    spans[0].parent_span_id = "ffffffffffffffff"

    roots = build_tree(spans)
    assert len(roots) == 1, "una traza incompleta se ve incompleta, no vacía"


def test_sesion_y_usuario_se_propagan_a_los_spans():
    decorators.set_context(session_id="conv-42", user_id="u-7")

    @decorators.observe(type="agent")
    def agente():
        pass

    agente()
    span = ingest()[0]
    assert span.session_id == "conv-42"
    assert span.user_id == "u-7"
    decorators.set_context(session_id="", user_id="")
