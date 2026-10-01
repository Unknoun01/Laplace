"""La caché compartida entre pasos (D-178): el mismo prefijo en varios pasos.

Lo que se exige, en los dos almacenes:

* que la ingesta dé la misma huella de prefijo a dos pasos con las mismas instrucciones
  llamados desde sitios distintos, y distinta si cambian las instrucciones;
* el dinero exacto del caso de libro: dos pasos, una vez cada uno por ejecución;
* que no se cuente lo que ya cuenta el contexto fijo dentro de cada paso;
* que no dispare con un solo paso, ni cuando el proveedor ya está cacheando el prefijo;
* y que su ficha cuente lo mismo que su tarjeta.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.ingest.otlp import prefix_hash, step_identity
from laplace_backend.pricing import get_price_table
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)
MODELO = "gpt-5.6-terra"
VENTANA = Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=2), days=1)
SISTEMA = [{"role": "system", "content": "Eres el agente de reservas. " * 200}]


def test_la_huella_del_prefijo_no_depende_del_sitio():
    mensajes = [*SISTEMA, {"role": "user", "content": "hola"}]
    otros = [*SISTEMA, {"role": "user", "content": "adiós"}]
    sitio = "laplace.step.site"
    clave_a, *_ = step_identity({sitio: "agente > clasificar"}, "chat", mensajes, None)
    clave_b, *_ = step_identity({sitio: "agente > redactar"}, "chat", otros, None)
    assert clave_a != clave_b, "dos sitios son dos pasos"
    assert prefix_hash(mensajes, None) == prefix_hash(otros, None) != ""
    distinto = [
        {"role": "system", "content": "Eres otro agente."},
        {"role": "user", "content": "x"},
    ]
    assert prefix_hash(distinto, None) != prefix_hash(mensajes, None)
    assert prefix_hash([{"role": "user", "content": "sin instrucciones"}], None) == ""


@pytest.fixture(params=["sqlite", "clickhouse"])
def store(request, tmp_path):
    if request.param == "sqlite":
        local = SQLiteStore(tmp_path / "c.db")
        local.migrate()
        yield local
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d178_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    nube = ClickHouseStore(Settings(clickhouse_database=base))
    nube.migrate()
    try:
        yield nube
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _llamada(
    proyecto: str, traza: str, paso: str, i: int, *, prefijo: str = "comun",
    entrada: int = 3_000, cacheados: int = 0,
) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name=f"chat {MODELO}", type="llm", status="ok", start_time=inicio,
        end_time=inicio + timedelta(milliseconds=500), duration_ms=500.0,
        step_key=f"k-{paso}", step_label=paso, step_site=f"agente > {paso}",
        step_hint="Eres el agente de reservas.", prefix_hash=prefijo,
        dedup_hash=uuid.uuid4().hex,
    )
    # Salida larga: así la regla del modelo caro no recomienda otro modelo y la tarifa
    # es la de éste, que es la cuenta que se comprueba a mano abajo.
    span.llm = LLMAttributes(
        system="openai", request_model=MODELO, response_model=MODELO,
        usage=TokenUsage(input_tokens=entrada, output_tokens=400, cached_input_tokens=cacheados),
        cost=Cost(total_usd=0.01),
    )
    return span


def _ejecuciones(proyecto: str, n: int, pasos: tuple[str, ...], **kw) -> list[Span]:
    spans = []
    for t in range(n):
        traza = uuid.uuid4().hex
        for j, paso in enumerate(pasos):
            spans.append(_llamada(proyecto, traza, paso, t * 10 + j, **kw))
    return spans


def _compartidas(store, proyecto: str):
    return [f for f in insights.detect(store, proyecto, VENTANA) if f.kind == "cache_compartida"]


def test_dos_pasos_una_vez_por_ejecucion(store):
    """El caso de libro: diez ejecuciones, dos pasos con el mismo prefijo de 3.000 tokens.

    Veinte llamadas, diez ejecuciones: diez llamadas encontrarían la caché caliente.
    Paso a paso, ninguna (cada uno se llama una vez por ejecución), así que no hay nada
    que descontar. Menos una escritura de caché por ejecución.
    """
    store.insert_spans(_ejecuciones("libro", 10, ("clasificar", "redactar")))
    [hallazgo] = _compartidas(store, "libro")
    precio = get_price_table().lookup(MODELO)
    lecturas = 3_000 * (20 - 10)
    esperado = lecturas * (precio.input - precio.cached_input) / 1e6
    esperado -= 3_000 * 10 * (precio.cache_write - precio.input) / 1e6
    assert hallazgo.window_waste_usd == pytest.approx(esperado)
    assert hallazgo.step_shares == pytest.approx(
        {"k-clasificar": esperado / 2, "k-redactar": esperado / 2}
    )
    assert "2" in hallazgo.title and "3.000" in hallazgo.title
    # Paso a paso no había nada: ninguna regla de contexto fijo reclama lo mismo.
    assert not [f for f in insights.detect(store, "libro", VENTANA) if f.kind == "contexto_fijo"]
    ficha = insights.detail(store, "libro", VENTANA, hallazgo.id)
    assert ficha is not None and ficha.window_waste_usd == pytest.approx(esperado)
    assert ficha.title == hallazgo.title


def test_no_se_cuenta_lo_que_ya_cuenta_el_contexto_fijo(store):
    """Si un paso se llama dos veces por ejecución, la segunda ya la reclama el contexto
    fijo de ese paso. Aquí sólo queda la del otro paso."""
    store.insert_spans(_ejecuciones("doble", 10, ("clasificar", "clasificar", "redactar")))
    [hallazgo] = _compartidas(store, "doble")
    precio = get_price_table().lookup(MODELO)
    # 30 llamadas − 10 ejecuciones = 20 lecturas; el paso doble ya cuenta 10.
    esperado = 3_000 * (20 - 10) * (precio.input - precio.cached_input) / 1e6
    esperado -= 3_000 * 10 * (precio.cache_write - precio.input) / 1e6
    assert hallazgo.window_waste_usd == pytest.approx(esperado)
    reclamado: dict[str, float] = {}
    for f in insights.detect(store, "doble", VENTANA):
        for paso, dinero in insights.reparto(f).items():
            reclamado[paso] = reclamado.get(paso, 0.0) + dinero
    costes = {u.key: u.cost_usd for u in store.model_usage("doble", VENTANA, min_calls=1)}
    for paso, dinero in reclamado.items():
        assert dinero <= costes[paso] + 1e-9, (paso, dinero, costes[paso])


def test_un_solo_paso_no_es_compartir(store):
    store.insert_spans(_ejecuciones("solo", 20, ("clasificar",)))
    assert _compartidas(store, "solo") == []


def test_prefijos_distintos_no_se_juntan(store):
    spans = _ejecuciones("dos", 10, ("clasificar",), prefijo="uno")
    spans += _ejecuciones("dos", 10, ("redactar",), prefijo="otro")
    store.insert_spans(spans)
    assert _compartidas(store, "dos") == []


def test_si_el_proveedor_ya_lo_cachea_no_se_promete_otra_vez(store):
    """OpenAI cachea sola los prefijos idénticos de más de 1.024 tokens: si la caché ya
    sirve casi todo el prefijo, no hay nada que proponer."""
    # Cuatro pasos: sin el filtro, lo poco que queda sin cachear aún daría un ahorro
    # positivo, así que lo que calla aquí es el filtro y no la cuenta.
    pasos = ("clasificar", "redactar", "revisar", "resumir")
    store.insert_spans(_ejecuciones("cacheado", 10, pasos, cacheados=2_250))
    assert _compartidas(store, "cacheado") == []
