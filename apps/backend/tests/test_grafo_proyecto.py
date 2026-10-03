"""El grafo del proyecto: el agente entero sumando las trazas (D-188).

El de la vista de traza (D-153) dice cómo está hecho el agente en una ejecución; éste lo
dice en todas las de la ventana: un nodo por paso —la misma identidad, `step_key` o tipo
y nombre—, con sus llamadas, sus modelos y su coste, y una arista por cada «este paso
llama a este otro», con cuántas veces y lo que costaron esas llamadas. Las tiradas de
evaluación no son tráfico real y no entran. Un modelo sin tarifa no cuesta cero.

Tiene que salir igual en SQLite y en ClickHouse; la siembra está empatada a propósito
(D-099).
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend import grafo
from laplace_backend.config import Settings
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
VENTANA = Window(since=AHORA - timedelta(days=1), until=AHORA + timedelta(minutes=30), days=1)


def _span(proyecto, traza, sid, nombre, tipo, *, padre=None, t=0, **extra) -> Span:
    inicio = AHORA + timedelta(seconds=t)
    return Span(
        span_id=sid,
        trace_id=traza,
        parent_span_id=padre,
        project_id=proyecto,
        name=nombre,
        type=tipo,
        status=extra.pop("status", "ok"),
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=300),
        duration_ms=300.0,
        **extra,
    )


def _llm(proyecto, traza, sid, *, padre, clave, modelo, coste, t=0, sin_tarifa=False,
         estimada=False) -> Span:
    s = _span(proyecto, traza, sid, f"chat {modelo}", "llm", padre=padre, t=t,
              step_key=clave, step_label=clave.removeprefix("k_"))
    s.llm = LLMAttributes(
        request_model=modelo,
        usage=TokenUsage(input_tokens=500, output_tokens=50, estimated=estimada),
        cost=Cost(unknown=True) if sin_tarifa else Cost(input_usd=coste, total_usd=coste),
    )
    return s


def _ejecucion(proyecto: str, traza: str, *, tags=None, t=0) -> list[Span]:
    """atender → buscar ×2 (tool) → resumir (llm) ; atender → clasificar (llm sin tarifa)."""
    raiz = _span(proyecto, traza, f"{traza}-r", "atender", "agent", t=t, tags=tags or [])
    spans = [raiz]
    for i in range(2):
        spans.append(_span(proyecto, traza, f"{traza}-b{i}", "buscar", "tool",
                           padre=raiz.span_id, t=t + 1 + i))
    spans.append(_llm(proyecto, traza, f"{traza}-l1", padre=raiz.span_id, clave="k_resumir",
                      modelo="gpt-5.6-luna", coste=0.002, t=t + 3))
    spans.append(_llm(proyecto, traza, f"{traza}-l2", padre=raiz.span_id,
                      clave="k_clasificar", modelo="modelo-casero", coste=0, t=t + 4,
                      sin_tarifa=True))
    # Un paso que se llama a sí mismo no es una arista: es el mismo nodo.
    spans.append(_span(proyecto, traza, f"{traza}-b9", "buscar", "tool",
                       padre=f"{traza}-b0", t=t + 5))
    return spans


def _sembrar(store, proyecto: str) -> None:
    spans: list[Span] = []
    for i in range(3):
        spans += _ejecucion(proyecto, f"{proyecto}-t{i}")
    # Una ejecución con otro modelo en el mismo paso y con el recuento estimado.
    otra = _ejecucion(proyecto, f"{proyecto}-t3")
    otra[3] = _llm(proyecto, f"{proyecto}-t3", f"{proyecto}-t3-l1", padre=otra[0].span_id,
                   clave="k_resumir", modelo="gpt-5.6-sol", coste=0.002, t=3, estimada=True)
    spans += otra
    # Una tirada de evaluación: no es tráfico real.
    spans += _ejecucion(proyecto, f"{proyecto}-eval", tags=[EVAL_TAG])
    # Una traza fuera de la ventana.
    spans += _ejecucion(proyecto, f"{proyecto}-vieja", t=-3 * 86400)
    # Un paso que falla.
    fallo = _span(proyecto, f"{proyecto}-t5", f"{proyecto}-t5-r", "atender", "agent")
    spans += [fallo, _span(proyecto, f"{proyecto}-t5", f"{proyecto}-t5-x", "buscar", "tool",
                           padre=fallo.span_id, status="error")]
    store.insert_spans(spans)


def _sqlite(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "laplace.db")
    store.migrate()
    return store


def _nube():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    store.migrate()
    return store


def _por_id(g) -> dict:
    return {n.id: n for n in g.nodes}


def _aristas(g) -> dict:
    return {(a.source, a.target): a for a in g.edges}


# ---------------------------------------------------------------------------------
# La cuenta
# ---------------------------------------------------------------------------------


def test_el_agente_entero_sumando_las_trazas(tmp_path):
    store = _sqlite(tmp_path)
    _sembrar(store, "p")
    g = grafo.del_proyecto(store, "p", VENTANA)

    nodos = _por_id(g)
    assert set(nodos) == {"agent:atender", "tool:buscar", "k_resumir", "k_clasificar"}
    assert g.traces == 5  # t0..t3 y t5: ni la de evaluación ni la vieja
    atender = nodos["agent:atender"]
    assert (atender.calls, atender.roots, atender.type) == (5, 5, "agent")
    buscar = nodos["tool:buscar"]
    assert (buscar.calls, buscar.errors) == (13, 1)
    resumir = nodos["k_resumir"]
    assert resumir.label == "resumir"
    assert resumir.calls == 4
    assert resumir.cost_usd == pytest.approx(0.008)
    assert resumir.models == ["gpt-5.6-luna", "gpt-5.6-sol"]
    assert resumir.estimated_spans == 1

    aristas = _aristas(g)
    assert set(aristas) == {
        ("agent:atender", "tool:buscar"),
        ("agent:atender", "k_resumir"),
        ("agent:atender", "k_clasificar"),
    }
    assert aristas[("agent:atender", "tool:buscar")].calls == 9
    assert aristas[("agent:atender", "k_resumir")].calls == 4
    assert aristas[("agent:atender", "k_resumir")].cost_usd == pytest.approx(0.008)


def test_un_modelo_sin_tarifa_no_cuesta_cero(tmp_path):
    store = _sqlite(tmp_path)
    _sembrar(store, "p")
    g = grafo.del_proyecto(store, "p", VENTANA)
    clasificar = _por_id(g)["k_clasificar"]
    assert clasificar.unknown_cost_spans == 4
    assert clasificar.cost_usd == 0
    arista = _aristas(g)[("agent:atender", "k_clasificar")]
    assert arista.unknown_cost_spans == 4


def test_sin_trafico_no_hay_grafo(tmp_path):
    g = grafo.del_proyecto(_sqlite(tmp_path), "nadie", VENTANA)
    assert (g.nodes, g.edges, g.traces) == ([], [], 0)


def test_con_muchos_pasos_se_quedan_los_mas_llamados_y_se_dice_cuantos_faltan(tmp_path):
    store = _sqlite(tmp_path)
    spans = []
    raiz = _span("p", "t", "r", "atender", "agent")
    spans.append(raiz)
    for i in range(grafo.MAX_NODOS + 5):
        # La herramienta i se llama i+1 veces: el orden está claro.
        for j in range(i + 1):
            spans.append(_span("p", "t", f"h{i}-{j}", f"herramienta{i:02d}", "tool",
                               padre="r", t=1))
    store.insert_spans(spans)
    g = grafo.del_proyecto(store, "p", VENTANA)
    assert len(g.nodes) == grafo.MAX_NODOS
    assert g.hidden_nodes == 6
    assert g.hidden_calls == 1 + 1 + 2 + 3 + 4 + 5
    ids = {n.id for n in g.nodes}
    assert "tool:herramienta00" not in ids and "agent:atender" not in ids
    assert all(a.source in ids and a.target in ids for a in g.edges)


# ---------------------------------------------------------------------------------
# Paridad
# ---------------------------------------------------------------------------------


def test_sale_igual_en_sqlite_y_en_clickhouse(tmp_path):
    nube = _nube()
    proyecto = f"grafo-{uuid.uuid4().hex[:8]}"
    local = _sqlite(tmp_path)
    try:
        _sembrar(local, proyecto)
        _sembrar(nube, proyecto)
        a = grafo.del_proyecto(local, proyecto, VENTANA)
        b = grafo.del_proyecto(nube, proyecto, VENTANA)
        assert a.model_dump(exclude={"nodes", "edges"}) == b.model_dump(
            exclude={"nodes", "edges"}
        )
        assert [n.id for n in a.nodes] == [n.id for n in b.nodes]
        for x, y in zip(a.nodes, b.nodes, strict=True):
            assert x.model_dump(exclude={"cost_usd"}) == y.model_dump(exclude={"cost_usd"})
            assert x.cost_usd == pytest.approx(y.cost_usd)
        assert [(e.source, e.target) for e in a.edges] == [(e.source, e.target) for e in b.edges]
        for x, y in zip(a.edges, b.edges, strict=True):
            assert x.model_dump(exclude={"cost_usd"}) == y.model_dump(exclude={"cost_usd"})
            assert x.cost_usd == pytest.approx(y.cost_usd)
    finally:
        nube.delete_project(proyecto)


# ---------------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------------


@pytest.fixture
def local(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    _sembrar(store, "p")
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_la_ruta_del_grafo(local):
    r = local.get("/api/graph", params={"project_id": "p", "days": 1})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert {n["id"] for n in datos["nodes"]} == {
        "agent:atender", "tool:buscar", "k_resumir", "k_clasificar"
    }
    assert datos["traces"] == 5
