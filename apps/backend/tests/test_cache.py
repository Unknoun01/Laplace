"""La caché, de punta a punta: del cliente del proveedor a la cifra en dólares.

Un agente repite su prompt de sistema en cada paso, así que la caché salta siempre.
Mientras el motor de coste no la modelaba, Laplace inflaba la factura de todos sus
usuarios y, con ella, el ahorro que les prometía. Peor: la tercera regla de detección
recomienda activar la caché, y el propio cálculo no sabía representar la mejora.

Los dos proveedores cuentan los tokens de caché de formas **distintas**: OpenAI los
incluye en `prompt_tokens` y Anthropic los devuelve aparte. Estas pruebas fijan que el
contrato guarda un único criterio (el total facturable en `input_tokens`, D-050), para
que el mismo agente no cueste distinto según con quién hable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from helpers import exporter, ingest
from laplace import decorators  # noqa: F401  (registra el proveedor de tracing)
from laplace._tracer import get_tracer
from laplace.integrations import anthropic as ant
from laplace.integrations import openai as oai
from opentelemetry.trace import SpanKind

MENSAJES = [{"role": "user", "content": "¿Qué vuelos hay a Barcelona por la mañana?"}]
TEXTO = "El IB3421 sale a las 08:15."


@dataclass
class _Obj:
    _campos: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["_campos"][name]
        except KeyError:
            return None


def obj(**campos: Any) -> _Obj:
    return _Obj(campos)


def _span(nombre: str = "chat") -> Any:
    return get_tracer().start_span(nombre, kind=SpanKind.CLIENT)


# ---------------------------------------------------------------------------------
# Normalización del uso
# ---------------------------------------------------------------------------------


def test_anthropic_suma_los_tokens_de_cache_al_total_de_entrada():
    """Anthropic devuelve la entrada SIN la caché. Si no se suma, falta media factura.

    Con 400 de entrada nueva, 8.000 leídos de caché y 2.000 escritos, lo facturable son
    10.400 tokens de entrada, no 400.
    """
    respuesta = obj(
        id="msg_1",
        model="claude-sonnet-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
        stop_reason="end_turn",
        usage=obj(
            input_tokens=400,
            output_tokens=50,
            cache_read_input_tokens=8_000,
            cache_creation_input_tokens=2_000,
        ),
    )
    span = _span()
    ant._finish(span, {"model": "claude-sonnet-5", "messages": MENSAJES}, respuesta)
    span.end()

    llm = ingest()[0].llm
    assert llm.usage.input_tokens == 10_400
    assert llm.usage.cached_input_tokens == 8_000
    assert llm.usage.cache_write_tokens == 2_000
    assert llm.usage.uncached_input_tokens == 400

    # 400 a 2 $ + 8.000 a 0,20 $ + 2.000 a 2,50 $ + 50 de salida a 10 $.
    esperado = (400 * 2.0 + 8_000 * 0.2 + 2_000 * 2.5 + 50 * 10.0) / 1e6
    assert llm.cost.total_usd == pytest.approx(esperado)
    assert llm.cost.cache_read_usd == pytest.approx(8_000 * 0.2 / 1e6)
    assert llm.cost.cache_write_usd == pytest.approx(2_000 * 2.5 / 1e6)


def test_anthropic_separa_la_cache_de_una_hora_de_la_de_cinco_minutos():
    """Cuestan distinto (2x frente a 1,25x), así que no pueden ir al mismo saco."""
    respuesta = obj(
        id="msg_2",
        model="claude-sonnet-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
        usage=obj(
            input_tokens=0,
            output_tokens=0,
            cache_creation=obj(
                ephemeral_5m_input_tokens=1_000, ephemeral_1h_input_tokens=3_000
            ),
        ),
    )
    span = _span()
    ant._finish(span, {"model": "claude-sonnet-5", "messages": MENSAJES}, respuesta)
    span.end()

    llm = ingest()[0].llm
    assert llm.usage.cache_write_tokens == 1_000
    assert llm.usage.cache_write_1h_tokens == 3_000
    assert llm.usage.input_tokens == 4_000
    assert llm.cost.total_usd == pytest.approx((1_000 * 2.5 + 3_000 * 4.0) / 1e6)


def test_openai_no_suma_dos_veces_lo_que_ya_venia_incluido():
    """`prompt_tokens` de OpenAI YA incluye los cacheados. Sumarlos los contaría doble."""
    respuesta = obj(
        id="chatcmpl-1",
        model="gpt-5.6-terra",
        choices=[obj(message=obj(role="assistant", content=TEXTO), finish_reason="stop")],
        usage=obj(
            prompt_tokens=10_000,
            completion_tokens=50,
            prompt_tokens_details=obj(cached_tokens=9_000),
        ),
    )
    span = _span()
    oai._finish(span, {"model": "gpt-5.6-terra", "messages": MENSAJES}, respuesta)
    span.end()

    llm = ingest()[0].llm
    assert llm.usage.input_tokens == 10_000
    assert llm.usage.cached_input_tokens == 9_000
    # 1.000 a 2 $ + 9.000 a 0,20 $ + 50 de salida a 12 $.
    esperado = (1_000 * 2.0 + 9_000 * 0.2 + 50 * 12.0) / 1e6
    assert llm.cost.total_usd == pytest.approx(esperado)


def test_la_misma_llamada_con_y_sin_cache_no_cuesta_lo_mismo():
    """La prueba que pedía el encargo, extremo a extremo desde el cliente."""
    comun = dict(
        id="msg_3",
        model="claude-sonnet-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
    )
    sin = obj(**comun, usage=obj(input_tokens=10_000, output_tokens=50))
    span = _span()
    ant._finish(span, {"model": "claude-sonnet-5", "messages": MENSAJES}, sin)
    span.end()
    coste_sin = ingest()[0].llm.cost.total_usd

    exporter.clear()
    con = obj(
        **comun,
        usage=obj(input_tokens=1_000, output_tokens=50, cache_read_input_tokens=9_000),
    )
    span = _span()
    ant._finish(span, {"model": "claude-sonnet-5", "messages": MENSAJES}, con)
    span.end()
    traza = ingest()[0]

    # Los mismos 10.000 tokens de entrada facturables en los dos casos.
    assert traza.llm.usage.input_tokens == 10_000
    assert traza.llm.cost.total_usd < coste_sin
    assert traza.llm.cost.cache_saving_usd == pytest.approx(9_000 * (2.0 - 0.2) / 1e6)


# ---------------------------------------------------------------------------------
# Metros de facturación
# ---------------------------------------------------------------------------------


def test_el_modo_rapido_de_anthropic_se_cobra_a_su_tarifa():
    respuesta = obj(
        id="msg_4",
        model="claude-opus-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
        usage=obj(input_tokens=1_000_000, output_tokens=0),
    )
    span = _span()
    ant._finish(span, {"model": "claude-opus-5", "speed": "fast", "messages": MENSAJES}, respuesta)
    span.end()

    llm = ingest()[0].llm
    assert llm.billing_tier == "fast"
    assert llm.cost.total_usd == pytest.approx(10.0)  # frente a 5 $ en estándar
    assert llm.cost.rate_assumed is False


def test_la_residencia_de_datos_de_anthropic_aplica_su_recargo():
    respuesta = obj(
        id="msg_5",
        model="claude-sonnet-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
        usage=obj(input_tokens=1_000_000, output_tokens=0),
    )
    span = _span()
    ant._finish(
        span,
        {"model": "claude-sonnet-5", "inference_geo": "us", "messages": MENSAJES},
        respuesta,
    )
    span.end()

    llm = ingest()[0].llm
    assert llm.billing_region == "regional"
    assert llm.cost.total_usd == pytest.approx(2.2)


def test_sin_atributos_de_facturacion_el_metro_es_el_estandar_y_no_se_marca_nada():
    """Ausencia no es duda: estándar y global es lo que facturan los dos por defecto."""
    respuesta = obj(
        id="msg_6",
        model="claude-sonnet-5",
        role="assistant",
        content=[obj(type="text", text=TEXTO)],
        usage=obj(input_tokens=1_000, output_tokens=10),
    )
    span = _span()
    ant._finish(span, {"model": "claude-sonnet-5", "messages": MENSAJES}, respuesta)
    span.end()

    llm = ingest()[0].llm
    assert llm.billing_tier == "standard"
    assert llm.billing_region == "global"
    assert llm.cost.rate_assumed is False
    assert llm.cost.rate_note == ""
