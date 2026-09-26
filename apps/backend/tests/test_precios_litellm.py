"""La capa de LiteLLM: muchos más modelos, marcados como tarifa no verificada.

La tabla propia tiene los modelos de OpenAI y Anthropic, transcritos a mano de sus
páginas y con fecha. Todo lo demás —Gemini, Mistral, DeepSeek, lo que pase por Bedrock
o por OpenRouter— salía como «coste desconocido», que es honrado pero deja sin cifra a
casi cualquiera que no use esos dos proveedores. La tabla comunitaria de LiteLLM tiene
miles de modelos; aquí se carga **debajo** de la propia y con una regla: una tarifa que
sale sólo de ella se dice que no está verificada, igual que se dice que una tarifa es
asumida.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laplace_backend.pricing import (
    LITELLM_PATH,
    PriceTable,
    convertir_litellm,
    get_price_table,
)

PROPIA = Path(__file__).parents[1] / "laplace_backend" / "pricing" / "model_prices.json"


def _tabla(tmp_path, modelos_litellm: dict, *, propia: dict | None = None) -> PriceTable:
    """Una tabla con la capa propia de verdad (o una dada) y una capa de LiteLLM dada."""
    capa = tmp_path / "litellm.json"
    capa.write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-26",
                "url": "https://example.test/litellm.json",
                "models": convertir_litellm(modelos_litellm),
            }
        ),
        encoding="utf-8",
    )
    ruta_propia = PROPIA
    if propia is not None:
        ruta_propia = tmp_path / "propia.json"
        ruta_propia.write_text(json.dumps(propia), encoding="utf-8")
    return PriceTable.load(ruta_propia, unverified_path=capa)


GEMINI = {
    "gemini-9-pro": {
        "mode": "chat",
        "litellm_provider": "vertex_ai-language-models",
        "input_cost_per_token": 1.25e-06,
        "output_cost_per_token": 1e-05,
        "cache_read_input_token_cost": 1.25e-07,
    }
}


# ---------------------------------------------------------------------------------
# La conversión
# ---------------------------------------------------------------------------------


def test_convertir_pasa_de_por_token_a_por_millon_y_mapea_los_metros():
    convertido = convertir_litellm(
        {
            "modelo-x": {
                "mode": "chat",
                "input_cost_per_token": 3e-06,
                "output_cost_per_token": 1.5e-05,
                "cache_read_input_token_cost": 3e-07,
                "cache_creation_input_token_cost": 3.75e-06,
                "cache_creation_input_token_cost_above_1hr": 6e-06,
                "input_cost_per_token_priority": 6e-06,
                "output_cost_per_token_priority": 3e-05,
            }
        }
    )
    assert convertido["modelo-x"] == {
        "input": 3.0,
        "output": 15.0,
        "cached_input": 0.3,
        "cache_write": 3.75,
        "cache_write_1h": 6.0,
        "fast_input": 6.0,
        "fast_output": 30.0,
    }


def test_convertir_se_queda_solo_con_modelos_de_texto():
    """Imágenes, embeddings o audio se cobran por otras unidades que el contrato no mide:
    cargarlos daría una tarifa por token que no significa nada."""
    convertido = convertir_litellm(
        {
            "chat": {"mode": "chat", "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
            "resp": {
                "mode": "responses",
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 2e-06,
            },
            "emb": {"mode": "embedding", "input_cost_per_token": 1e-07},
            "img": {"mode": "image_generation", "output_cost_per_image": 0.04},
            "sin_modo": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
            "sample_spec": {"mode": "chat", "input_cost_per_token": 0, "output_cost_per_token": 0},
        }
    )
    assert set(convertido) == {"chat", "resp"}


def test_un_modelo_gratis_en_litellm_no_entra():
    """LiteLLM pone a cero los modelos locales y algunos de prueba. Para Laplace «cuesta
    cero» es una afirmación, y ésa no la ha verificado nadie: se quedan sin tarifa, que
    es lo que ya se hace con un modelo local (D-108)."""
    convertido = convertir_litellm(
        {
            "ollama/llama9": {
                "mode": "chat",
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
            }
        }
    )
    assert convertido == {}


def test_un_precio_ilegible_no_entra():
    convertido = convertir_litellm(
        {"raro": {"mode": "chat", "input_cost_per_token": "gratis", "output_cost_per_token": 1}}
    )
    assert convertido == {}


def test_los_nombres_se_guardan_en_minusculas():
    """La búsqueda compara en minúsculas: una clave con mayúsculas no se encontraría nunca."""
    convertido = convertir_litellm(
        {
            "Modelo-X": {
                "mode": "chat",
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 1e-06,
            }
        }
    )
    assert list(convertido) == ["modelo-x"]


# ---------------------------------------------------------------------------------
# Las dos capas
# ---------------------------------------------------------------------------------


def test_un_modelo_que_solo_esta_en_litellm_tiene_coste_y_se_dice_no_verificado(tmp_path):
    tabla = _tabla(tmp_path, GEMINI)
    coste = tabla.compute("gemini-9-pro", input_tokens=1_000_000, output_tokens=0)
    assert coste.unknown is False
    assert coste.input_usd == pytest.approx(1.25)
    assert coste.unverified is True, "sin verificar no puede presentarse como cifra firme"
    # Y no es una tarifa asumida: eso dice «coste mínimo», y una tarifa de LiteLLM puede
    # quedarse corta o pasarse. Decir «suelo» de ella sería afirmar lo que no se sabe.
    assert coste.assumed is False
    assert "no verificada" in coste.note
    assert "LiteLLM" in coste.note
    assert coste.rate == "gemini-9-pro @ litellm 2026-09-26"


def test_la_capa_propia_manda_cuando_coinciden(tmp_path):
    """El mismo modelo en las dos: gana la tarifa transcrita y fechada por nosotros."""
    capa = {
        "gpt-5.6-luna": {
            "mode": "chat",
            "input_cost_per_token": 9e-06,
            "output_cost_per_token": 9e-06,
        }
    }
    tabla = _tabla(tmp_path, capa)
    coste = tabla.compute("gpt-5.6-luna", input_tokens=100_000, output_tokens=0)
    assert coste.input_usd == pytest.approx(0.02)
    assert coste.assumed is False
    assert coste.unverified is False
    assert "litellm" not in coste.rate


def test_la_capa_propia_manda_tambien_por_snapshot(tmp_path):
    """Un snapshot con fecha que LiteLLM tiene como entrada exacta no puede ganarle al
    modelo base verificado: la regla es que la capa propia resuelve primero, y lo que
    LiteLLM diga distinto sale en el informe semanal, no en la factura de nadie."""
    capa = {
        "gpt-5.6-luna-2026-08-01": {
            "mode": "chat",
            "input_cost_per_token": 9e-06,
            "output_cost_per_token": 9e-06,
        }
    }
    tabla = _tabla(tmp_path, capa)
    coste = tabla.compute("gpt-5.6-luna-2026-08-01", input_tokens=100_000, output_tokens=0)
    assert coste.input_usd == pytest.approx(0.02)
    assert coste.assumed is False


def test_un_gateway_con_precio_propio_no_se_cobra_como_el_proveedor(tmp_path):
    """`eu.anthropic.claude-…` en Bedrock, `azure/…` en una zona de datos u
    `openrouter/…` son otros vendedores, y cobran otra cosa: Bedrock regional, un 10 %
    más. Antes se les quitaba el adorno y se cobraban a la tarifa verificada del
    proveedor directo, por debajo de la factura y sin decirlo. Si LiteLLM conoce ese
    nombre exacto, su precio —marcado como no verificado— está más cerca de la verdad.
    """
    capa = {
        "eu.anthropic.claude-haiku-4-5": {
            "mode": "chat",
            "input_cost_per_token": 1.1e-06,
            "output_cost_per_token": 5.5e-06,
        }
    }
    tabla = _tabla(tmp_path, capa)
    coste = tabla.compute("eu.anthropic.claude-haiku-4-5", input_tokens=100_000, output_tokens=0)
    assert coste.input_usd == pytest.approx(0.11)
    assert coste.unverified is True
    # Un gateway que LiteLLM no conoce sigue resolviendo al proveedor, como antes.
    otro = tabla.compute("apac.anthropic.claude-haiku-4-5", input_tokens=100_000, output_tokens=0)
    assert otro.input_usd == pytest.approx(0.1)
    assert otro.unverified is False


def test_las_tarifas_propias_del_usuario_mandan_sobre_litellm(tmp_path, monkeypatch):
    """Quien ha puesto su tarifa negociada a mano ha dicho algo a propósito (D-123)."""
    extra = tmp_path / "extra.json"
    extra.write_text(
        json.dumps({"models": {"gemini-9-pro": {"input": 0.5, "output": 1.0}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LAPLACE_PRICES_EXTRA", str(extra))
    capa = tmp_path / "litellm.json"
    capa.write_text(
        json.dumps({"fetched_at": "2026-09-26", "models": convertir_litellm(GEMINI)}),
        encoding="utf-8",
    )
    tabla = PriceTable.load(unverified_path=capa)
    coste = tabla.compute("gemini-9-pro", input_tokens=1_000_000, output_tokens=0)
    assert coste.input_usd == pytest.approx(0.5)
    assert "no verificada" not in coste.note


def test_los_adornos_del_gateway_se_quitan_tambien_en_litellm(tmp_path):
    tabla = _tabla(tmp_path, GEMINI)
    assert tabla.lookup("vertex_ai/gemini-9-pro").model == "gemini-9-pro"
    assert tabla.lookup("gemini-9-pro-20260901").model == "gemini-9-pro"


def test_litellm_no_deja_saltar_de_version(tmp_path):
    """La regla del prefijo vale en las dos capas: `gemini-9-pro-max` no es un snapshot."""
    tabla = _tabla(tmp_path, GEMINI)
    assert tabla.lookup("gemini-9-pro-max") is None
    assert tabla.compute("gemini-9-pro-max", input_tokens=1, output_tokens=1).unknown is True


def test_un_modelo_que_no_esta_en_ninguna_sigue_sin_tarifa(tmp_path):
    tabla = _tabla(tmp_path, GEMINI)
    assert tabla.compute("modelo-inventado-7b", input_tokens=1, output_tokens=1).unknown is True


def test_la_capa_no_verificada_no_cuenta_como_fuente_que_caduca(tmp_path):
    """La que caduca a los 30 días es la verificada. La de LiteLLM se renueva sola cada
    semana y no puede poner la suite en rojo por su fecha."""
    tabla = _tabla(tmp_path, GEMINI)
    assert "litellm" not in tabla.sources


def test_una_prueba_que_carga_su_fichero_no_recibe_litellm(tmp_path):
    """Como las tarifas propias: una tabla cargada de un fichero concreto es ese fichero."""
    propia = tmp_path / "p.json"
    propia.write_text(json.dumps({"version": "x", "sources": {}, "models": {}}), encoding="utf-8")
    assert PriceTable.load(propia).lookup("gemini-2.5-pro") is None


def test_una_capa_ilegible_no_tumba_nada(tmp_path):
    rota = tmp_path / "rota.json"
    rota.write_text("{ no es json", encoding="utf-8")
    tabla = PriceTable.load(PROPIA, unverified_path=rota)
    assert tabla.lookup("gpt-5.6-luna") is not None
    assert tabla.lookup("gemini-2.5-pro") is None


# ---------------------------------------------------------------------------------
# La capa que se distribuye
# ---------------------------------------------------------------------------------


def test_la_capa_distribuida_se_carga_y_es_amplia():
    datos = json.loads(LITELLM_PATH.read_text(encoding="utf-8"))
    assert datos["url"].startswith("https://")
    assert datos["fetched_at"]
    assert len(datos["models"]) > 1000
    for nombre, entrada in datos["models"].items():
        assert entrada["input"] > 0 or entrada["output"] > 0, nombre
        assert nombre == nombre.lower(), nombre


def test_la_tabla_del_producto_usa_las_dos_capas():
    tabla = get_price_table()
    assert tabla.lookup("gpt-5.6-luna").verified is True
    gemini = tabla.lookup("gemini-2.5-pro")
    assert gemini is not None and gemini.verified is False


def test_una_tarifa_asumida_y_no_verificada_lo_dice_todo(tmp_path):
    """Una llamada por lotes a un modelo de LiteLLM: la capa no publica el descuento, se
    cobra el estándar (asumida, suelo) y además la tarifa no está verificada."""
    tabla = _tabla(tmp_path, GEMINI)
    coste = tabla.compute("gemini-9-pro", input_tokens=1000, output_tokens=0, tier="batch")
    assert coste.assumed is True
    assert coste.unverified is True
    assert "no verificada" in coste.note and "lotes" in coste.note


# ---------------------------------------------------------------------------------
# De la ingesta a la pantalla, por los dos almacenes
# ---------------------------------------------------------------------------------


def _span_gemini(project: str):
    from datetime import datetime, timedelta, timezone

    from laplace.schema import LLMAttributes, Span, TokenUsage

    from laplace_backend.ingest.otlp import recalcular_coste

    inicio = datetime.now(timezone.utc) - timedelta(minutes=5)
    usage = TokenUsage(input_tokens=1000, output_tokens=100)
    llm = LLMAttributes(system="vertex_ai", request_model="gemini-2.5-pro", usage=usage)
    span = Span(
        span_id="a" * 16,
        trace_id="b" * 32,
        project_id=project,
        name="chat gemini-2.5-pro",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(seconds=1),
        duration_ms=1000.0,
        llm=llm,
    )
    return recalcular_coste(span, get_price_table())


def test_la_ingesta_marca_la_tarifa_no_verificada():
    span = _span_gemini("p")
    assert span.llm.cost.unknown is False
    assert span.llm.cost.rate_unverified is True
    assert span.llm.cost.rate_assumed is False


def test_la_marca_sobrevive_al_almacen_local(tmp_path):
    from laplace_backend.storage.sqlite import SQLiteStore

    almacen = SQLiteStore(tmp_path / "l.db")
    almacen.migrate()
    span = _span_gemini("p")
    almacen.insert_spans([span])
    leido = almacen.get_trace_spans(span.trace_id, "p")[0]
    assert leido.llm.cost.rate_unverified is True


def test_la_marca_sobrevive_igual_en_la_nube(tmp_path):
    import uuid

    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore
    from laplace_backend.storage.sqlite import SQLiteStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()
    local = SQLiteStore(tmp_path / "l.db")
    local.migrate()
    proyecto = f"litellm-{uuid.uuid4().hex[:8]}"
    span = _span_gemini(proyecto)
    local.insert_spans([span])
    nube.insert_spans([span])
    try:
        alli = nube.get_trace_spans(span.trace_id, proyecto)[0]
        aqui = local.get_trace_spans(span.trace_id, proyecto)[0]
        assert alli.llm.cost.rate_unverified is True
        assert alli.llm.cost.model_dump() == aqui.llm.cost.model_dump()
    finally:
        nube.delete_project(proyecto)


# ---------------------------------------------------------------------------------
# Los hallazgos y la traza también lo dicen (D-141)
# ---------------------------------------------------------------------------------


def _repeticion_con(modelo: str, tmp_path):
    """Cuatro ejecuciones con la misma llamada tres veces: una repetición de libro."""
    from datetime import datetime, timedelta, timezone

    from helpers import exporter, ingest
    from opentelemetry import trace as otel_trace

    from laplace_backend import insights
    from laplace_backend.storage.base import Window
    from laplace_backend.storage.sqlite import SQLiteStore

    exporter.clear()
    tracer = otel_trace.get_tracer("prueba")
    for _ in range(4):
        with tracer.start_as_current_span("agente"):
            for _ in range(3):
                with tracer.start_as_current_span(f"chat {modelo}") as span:
                    for clave, valor in {
                        "laplace.span.type": "llm",
                        "gen_ai.system": "vertex_ai",
                        "gen_ai.request.model": modelo,
                        "gen_ai.usage.input_tokens": 5_000,
                        "gen_ai.usage.output_tokens": 50,
                        "gen_ai.input.messages": json.dumps(
                            [{"role": "user", "content": "¿Cuánto equipaje?"}]
                        ),
                    }.items():
                        span.set_attribute(clave, valor)
    spans = ingest()
    exporter.clear()
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(spans)
    ahora = datetime.now(timezone.utc)
    ventana = Window(since=ahora - timedelta(hours=1), until=ahora + timedelta(hours=1), days=1)
    proyecto = spans[0].project_id
    hallazgos = [f for f in insights.detect(store, proyecto, ventana) if f.kind == "repeticion"]
    assert hallazgos, "la regla tiene que ver la repetición"
    return hallazgos[0], store, proyecto, spans


def test_un_hallazgo_con_tarifa_de_litellm_lo_dice(tmp_path):
    hallazgo, *_ = _repeticion_con("gemini-2.5-pro", tmp_path)
    assert hallazgo.window_waste_usd > 0
    assert hallazgo.cost_unverified is True
    assert hallazgo.unverified_rate_models == ["gemini-2.5-pro"]
    # Y no por eso es un suelo: no verificada no quiere decir «al menos».
    assert hallazgo.cost_is_floor is False


def test_un_hallazgo_con_tarifa_verificada_no_dice_nada(tmp_path):
    hallazgo, *_ = _repeticion_con("gpt-5.6-luna", tmp_path)
    assert hallazgo.cost_unverified is False
    assert hallazgo.unverified_rate_models == []


def test_la_traza_agregada_lo_dice(tmp_path):
    _, store, proyecto, spans = _repeticion_con("gemini-2.5-pro", tmp_path)
    from laplace_backend.storage.base import TraceFilter

    trazas = store.list_traces(TraceFilter(project_id=proyecto)).traces
    assert trazas and all(t.cost.rate_unverified for t in trazas)
