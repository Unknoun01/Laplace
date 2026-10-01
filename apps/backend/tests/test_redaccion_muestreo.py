"""Redacción de datos personales y muestreo por cola en el SDK (D-181).

Se exige:

* que los detectores pillen correos, teléfonos, tarjetas, IBAN, IP y claves, y no
  confundan fechas, instantes ni recuentos con ellos;
* que la marca sea la misma para el mismo valor y distinta para otro, para que una
  repetición siga siéndolo y dos peticiones distintas no se junten;
* que lo que sale por OTLP no lleve el dato, y que la estructura (cliente, modelo,
  tokens, paso) no se toque;
* que el muestreo se quede con todo lo que falla, lo caro y las evaluaciones, y con una
  parte de lo demás marcada con a cuántas representa;
* y que el servidor lo guarde y lo diga, en los dos almacenes.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

import pytest
from laplace import semconv
from laplace._filtro import FiltroProcessor, Muestreo, Redactor
from laplace._tracer import filtro
from laplace.config import LaplaceConfig
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from opentelemetry import trace as otel
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode
from test_margen import AHORA, VENTANA, almacen  # noqa: F401

from laplace_backend import coverage
from laplace_backend.ingest.otlp import parse_spans

CORREO = "ana.garcia@example.com"


# ---------------------------------------------------------------------------------
# Los detectores
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "texto"),
    [
        ("email", f"escríbeme a {CORREO} mañana"),
        ("phone", "llama al +34 612 345 678"),
        ("phone", "o al 612-345-678"),
        ("phone", "US: (555) 123-4567"),
        ("card", "tarjeta 4111 1111 1111 1111 caduca"),
        ("iban", "IBAN ES91 2100 0418 4502 0005 1332"),
        ("ip", "desde 192.168.10.24"),
        ("secret", "la clave es sk-proj-AbCdEf0123456789xyz"),
        ("secret", "Authorization: Bearer eyAbc123def456ghi789"),
    ],
)
def test_cada_detector_pilla_lo_suyo(tipo, texto):
    limpio = Redactor.of(True, "k").texto(texto)
    assert re.search(rf"\[{tipo}:[0-9a-f]{{8}}\]", limpio), limpio


@pytest.mark.parametrize(
    "texto",
    [
        "el 2026-10-01 a las 10:30",
        "1727740800000 ms desde la época",
        "usó 12345 tokens de entrada y 678 de salida",
        "pedido 4111 1111 1111 1112",  # no pasa Luhn
        "versión 3.11.4 de Python",
        "ES00 0000 0000 0000 0000 0000",  # no pasa el módulo 97
    ],
)
def test_no_confunde_lo_que_no_es_un_dato_personal(texto):
    assert Redactor.of(True, "k").texto(texto) == texto


def test_la_marca_es_estable_y_distingue():
    r = Redactor.of(True, "clave")
    a, b = r.texto(CORREO), r.texto("otra@example.com")
    assert a == r.texto(CORREO), "el mismo valor, la misma marca"
    assert a != b, "dos correos distintos no se juntan en uno"
    assert Redactor.of(True, "clave").texto(CORREO) == a, "y no depende del proceso"
    assert Redactor.of(True, "otra clave").texto(CORREO) != a, "sin la clave no se rehace"


def test_solo_los_detectores_pedidos_y_lo_propio():
    r = Redactor.of(["email", re.compile(r"EXP-\d+"), lambda s: s.replace("Ana", "[nombre]")])
    texto = r.texto(f"Ana ({CORREO}, +34 612 345 678) abrió EXP-2231")
    assert CORREO not in texto and "EXP-2231" not in texto and "Ana" not in texto
    assert "+34 612 345 678" in texto, "el teléfono no se pidió"
    with pytest.raises(ValueError):
        Redactor.of(["dni"])


# ---------------------------------------------------------------------------------
# De punta a punta: lo que sale por OTLP
# ---------------------------------------------------------------------------------


def _proveedor(cfg: LaplaceConfig):
    salida = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(filtro(cfg, SimpleSpanProcessor(salida)))
    return proveedor.get_tracer("prueba"), salida, proveedor


def _llamada(tracer, *, contenido: str, tokens=(100, 20), error=False, tags=None, cliente="acme"):
    with tracer.start_as_current_span("atender") as raiz:
        raiz.set_attribute(semconv.LAPLACE_CUSTOMER_ID, cliente)
        if tags:
            raiz.set_attribute(semconv.LAPLACE_TAGS, json.dumps(tags))
        with tracer.start_as_current_span("chat gpt-5.6-terra") as llm:
            llm.set_attribute(semconv.LAPLACE_SPAN_TYPE, "llm")
            llm.set_attribute(semconv.GEN_AI_SYSTEM, "openai")
            llm.set_attribute(semconv.GEN_AI_REQUEST_MODEL, "gpt-5.6-terra")
            llm.set_attribute(semconv.GEN_AI_USAGE_INPUT_TOKENS, tokens[0])
            llm.set_attribute(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, tokens[1])
            llm.set_attribute(
                semconv.GEN_AI_INPUT_MESSAGES,
                json.dumps([
                    {"role": "system", "content": "Eres soporte. Escala a jefe@example.com."},
                    {"role": "user", "content": contenido},
                ], ensure_ascii=False),
            )
            if error:
                llm.set_status(Status(StatusCode.ERROR, f"falló para {CORREO}"))
        return raiz.get_span_context().trace_id


def test_lo_que_sale_no_lleva_el_dato_y_la_estructura_no_cambia():
    cfg = LaplaceConfig(redact=True, api_key="k")
    tracer, salida, _ = _proveedor(cfg)
    # El cliente es un correo: lo pone el usuario a propósito y el margen casa por él.
    cliente = "facturacion@acme.com"
    _llamada(tracer, contenido=f"Soy {CORREO}, tarjeta 4111 1111 1111 1111", error=True,
             cliente=cliente)
    _llamada(tracer, contenido=f"Soy {CORREO}, tarjeta 4111 1111 1111 1111", cliente=cliente)
    sobre = encode_spans(salida.get_finished_spans()).SerializeToString()
    assert CORREO.encode() not in sobre and b"4111 1111" not in sobre
    assert b"jefe@example.com" not in sobre, "también en las instrucciones"
    spans = parse_spans(encode_spans(salida.get_finished_spans()))
    llms = [s for s in spans if s.type == "llm"]
    assert all(s.llm.request_model == "gpt-5.6-terra" for s in llms)
    assert all(s.llm.usage.input_tokens == 100 for s in llms)
    raices = [s for s in spans if s.parent_span_id is None]
    assert {s.customer_id for s in raices} == {cliente}, "el cliente se pone a propósito"
    assert CORREO not in (llms[0].status_message or "") and "[email:" in llms[0].status_message
    # La misma petición, la misma huella: el paso y la repetición se siguen viendo.
    assert llms[0].step_key == llms[1].step_key and llms[0].dedup_hash == llms[1].dedup_hash


def test_si_la_redaccion_falla_sale_sin_contenido(monkeypatch):
    cfg = LaplaceConfig(redact=True, api_key="k")
    tracer, salida, _ = _proveedor(cfg)

    def roto(self, atributos):
        raise RuntimeError("roto")

    monkeypatch.setattr(Redactor, "atributos", roto)
    _llamada(tracer, contenido=f"Soy {CORREO}")
    sobre = encode_spans(salida.get_finished_spans()).SerializeToString()
    assert CORREO.encode() not in sobre
    llm = next(s for s in salida.get_finished_spans() if s.name.startswith("chat"))
    assert llm.attributes[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 100, "la estructura queda"


def test_sin_redaccion_ni_muestreo_no_se_interpone_nada():
    procesador = SimpleSpanProcessor(InMemorySpanExporter())
    assert filtro(LaplaceConfig(), procesador) is procesador


# ---------------------------------------------------------------------------------
# Muestreo
# ---------------------------------------------------------------------------------


def test_el_muestreo_se_queda_con_lo_que_importa():
    cfg = LaplaceConfig(sample_rate=0.1, sample_keep_tokens=5_000)
    tracer, salida, _ = _proveedor(cfg)
    normales = [_llamada(tracer, contenido="hola") for _ in range(400)]
    fallidas = {_llamada(tracer, contenido="hola", error=True) for _ in range(20)}
    caras = {_llamada(tracer, contenido="hola", tokens=(6_000, 100)) for _ in range(20)}
    evals = {_llamada(tracer, contenido="hola", tags=[semconv.EVAL_TAG]) for _ in range(20)}

    por_traza: dict[int, list] = {}
    for s in salida.get_finished_spans():
        por_traza.setdefault(s.context.trace_id, []).append(s)
    assert fallidas | caras | evals <= set(por_traza), "lo que importa llega todo"
    for tid in fallidas | caras | evals:
        assert all("laplace.sample.rate" not in s.attributes for s in por_traza[tid])
    azar = [t for t in normales if t in por_traza]
    assert 15 <= len(azar) <= 70, len(azar)  # ~40 de 400
    for tid in azar:
        assert len(por_traza[tid]) == 2, "una traza llega entera o no llega"
        assert all(s.attributes["laplace.sample.rate"] == 10 for s in por_traza[tid])
    # Determinista por trace_id: la misma decisión en otro proceso.
    m = Muestreo(rate=0.1)
    assert all(m.por_azar(t) == (t in por_traza) for t in normales)


def test_lo_que_acaba_despues_de_la_raiz_sigue_su_suerte_y_nada_se_pierde_al_cerrar():
    salida = InMemorySpanExporter()
    procesador = FiltroProcessor(
        SimpleSpanProcessor(salida), muestreo=Muestreo(rate=0.5, keep_tokens=None)
    )
    proveedor = TracerProvider()
    proveedor.add_span_processor(procesador)
    tracer = proveedor.get_tracer("prueba")
    tardios = []
    for _ in range(40):
        raiz = tracer.start_span("raiz")
        hijo = tracer.start_span("tarea", context=otel.set_span_in_context(raiz))
        raiz.end()
        hijo.end()  # acaba después de su raíz
        tardios.append(raiz.get_span_context().trace_id)
    llegadas = [s.context.trace_id for s in salida.get_finished_spans()]
    for tid in set(llegadas):
        assert llegadas.count(tid) == 2, "el hijo tardío va con su raíz"
    assert 0 < procesador.descartadas < 40
    # Una traza sin cerrar se manda entera al cerrar el proceso, sin muestrear.
    abierta = tracer.start_span("raiz")
    hijo = tracer.start_span("tarea", context=otel.set_span_in_context(abierta))
    hijo.end()
    proveedor.shutdown()
    llegados = {s.context.span_id for s in salida.get_finished_spans()}
    assert hijo.get_span_context().span_id in llegados


def test_init_valida_lo_que_se_le_pide():
    import laplace

    with pytest.raises(ValueError):
        laplace.init(project="x", sample_rate=0, disabled=True)
    with pytest.raises(ValueError):
        laplace.init(project="x", redact=["dni"], disabled=True)


# ---------------------------------------------------------------------------------
# El servidor: lo guarda y lo dice
# ---------------------------------------------------------------------------------


def test_la_ingesta_lee_a_cuantas_representa():
    cfg = LaplaceConfig(sample_rate=0.5, sample_keep_tokens=None)
    tracer, salida, _ = _proveedor(cfg)
    for _ in range(30):
        _llamada(tracer, contenido="hola")
    spans = parse_spans(encode_spans(salida.get_finished_spans()))
    assert spans and {s.sample_rate for s in spans} == {2.0}


def _traza(proyecto: str, coste: float, rate: float) -> list[Span]:
    traza = uuid.uuid4().hex
    inicio = AHORA - timedelta(hours=1)
    raiz = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto, name="agente",
        type="agent", status="ok", start_time=inicio, end_time=inicio + timedelta(seconds=1),
        duration_ms=1000.0, sample_rate=rate,
    )
    llm = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, parent_span_id=raiz.span_id,
        project_id=proyecto, name="chat", type="llm", status="ok", start_time=inicio,
        end_time=inicio + timedelta(milliseconds=500), duration_ms=500.0, sample_rate=rate,
    )
    llm.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=100),
        cost=Cost(total_usd=coste, input_usd=coste),
    )
    return [raiz, llm]


def test_el_servidor_dice_lo_que_falta_por_el_muestreo(almacen):  # noqa: F811
    store, proyecto = almacen
    spans = _traza(proyecto, 0.30, 1.0) + _traza(proyecto, 0.02, 10.0) + _traza(
        proyecto, 0.04, 10.0
    )
    store.insert_spans(spans)
    hechos = store.coverage(proyecto, VENTANA)
    assert hechos.sampled_traces == 2
    assert hechos.represented_traces == pytest.approx(20)
    assert hechos.unseen_cost_usd == pytest.approx((0.02 + 0.04) * 9)
    leido = store.get_trace_spans(spans[2].trace_id, proyecto)
    assert {s.sample_rate for s in leido} == {10.0}
    texto = coverage.build(hechos).sampling
    assert "2" in texto and "20" in texto, texto


def test_sin_muestreo_no_se_dice_nada(almacen):  # noqa: F811
    store, proyecto = almacen
    store.insert_spans(_traza(proyecto, 0.3, 1.0))
    hechos = store.coverage(proyecto, VENTANA)
    assert hechos.sampled_traces == 0 and coverage.build(hechos).sampling == ""
