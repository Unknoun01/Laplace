"""Replay contrafactual, lado del backend: qué llamadas de un conjunto se reenvían (D-167).

El reenvío lo hace el SDK con las claves del usuario (`test_replay_sdk.py`). Aquí se
prueba lo que decide el backend: sólo llamadas hoja del paso, sin herramientas, en texto,
que salieron bien y con respuesta; lo demás, contado por motivo. Y las dos piezas que
hacen justa la comparación: el coste de la tirada original limitado a los spans
reenviados, y el juez con la respuesta de la llamada como referencia.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(hours=1)
PASO = "clasificar"
SISTEMA = "Clasifica el ticket en facturación, equipaje u otro."


def _id() -> str:
    return uuid.uuid4().hex[:16]


def _llm(
    trace_id: str,
    padre: str,
    *,
    paso: str = PASO,
    mensajes: list | None = None,
    salida: list | None = None,
    **cambios,
) -> Span:
    span = Span(
        span_id=_id(),
        trace_id=trace_id,
        parent_span_id=padre,
        project_id="local",
        name="chat gpt-5.6-terra",
        type="llm",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=400),
        duration_ms=400.0,
        step_key=paso,
    )
    span.llm = LLMAttributes(
        system="openai",
        request_model="gpt-5.6-terra",
        input_messages=mensajes
        if mensajes is not None
        else [
            {"role": "system", "content": SISTEMA},
            {"role": "user", "content": f"Ticket {trace_id}: no me llega la factura"},
        ],
        output_messages=salida
        if salida is not None
        else [{"role": "assistant", "content": "facturación"}],
        params={"temperature": 0, "max_tokens": 20, "response_format": "json"},
        usage=TokenUsage(input_tokens=900, output_tokens=3),
        cost=Cost(total_usd=0.002),
    )
    for clave, valor in cambios.items():
        setattr(span, clave, valor)
    return span


def _traza(trace_id: str, *llamadas_extra) -> list[Span]:
    raiz = Span(
        span_id=_id(),
        trace_id=trace_id,
        project_id="local",
        name="atender_ticket",
        type="agent",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(seconds=2),
        duration_ms=2000.0,
        input="no me llega la factura",
        output="Te la reenvío.",
    )
    buena = _llm(trace_id, raiz.span_id)
    otro_paso = _llm(trace_id, raiz.span_id, paso="responder")
    herramienta = Span(
        span_id=_id(),
        trace_id=trace_id,
        parent_span_id=raiz.span_id,
        project_id="local",
        name="buscar_factura",
        type="tool",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=50),
        duration_ms=50.0,
    )
    return [
        raiz,
        buena,
        otro_paso,
        herramienta,
        *[f(trace_id, raiz.span_id) for f in llamadas_extra],
    ]


def _con_herramientas_declaradas(trace_id, padre):
    span = _llm(trace_id, padre)
    span.attributes["laplace.request.tools"] = '[{"name": "buscar_factura"}]'
    return span


def _que_pide_una_herramienta(trace_id, padre):
    return _llm(
        trace_id,
        padre,
        salida=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "buscar_factura"}}],
            }
        ],
    )


def _con_resultado_de_herramienta(trace_id, padre):
    return _llm(
        trace_id,
        padre,
        mensajes=[
            {"role": "system", "content": SISTEMA},
            {"role": "tool", "tool_call_id": "c1", "content": "factura 42"},
        ],
    )


def _recortada(trace_id, padre):
    """Lo que deja la ingesta cuando el SDK recortó los mensajes por tamaño."""
    return _llm(trace_id, padre, mensajes=[{"content": '[{"role": "system", "cont…'}])


def _con_imagen(trace_id, padre):
    return _llm(
        trace_id,
        padre,
        mensajes=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "¿Qué pone?"},
                    {"type": "image_url", "image_url": {"url": "data:…"}},
                ],
            }
        ],
    )


def _fallida(trace_id, padre):
    return _llm(trace_id, padre, status="error", salida=[])


def _sin_respuesta(trace_id, padre):
    return _llm(trace_id, padre, salida=[])


def _de_una_tirada(trace_id, padre):
    return _llm(trace_id, padre, tags=[EVAL_TAG])


def _con_hijos(trace_id, padre):
    return _llm(trace_id, padre)


EXTRAS = (
    _con_herramientas_declaradas,
    _que_pide_una_herramienta,
    _con_resultado_de_herramienta,
    _recortada,
    _con_imagen,
    _fallida,
    _sin_respuesta,
    _de_una_tirada,
)


@pytest.fixture
def local(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans: list[Span] = []
    for i in range(3):
        spans += _traza(f"tr{i:02d}", *(EXTRAS if i == 0 else ()))
    # Una llamada de la que cuelga otra cosa no es una hoja: reenviarla sola no es
    # reenviar lo que pasó.
    padre = _llm("tr01", spans[0].span_id)
    padre.parent_span_id = next(
        s.span_id for s in spans if s.trace_id == "tr01" and s.type == "agent"
    )
    hijo = _llm("tr01", padre.span_id, paso="otro")
    spans += [padre, hijo]
    store.insert_spans(spans)

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        client.spans = spans  # type: ignore[attr-defined]
        yield client
    config.get_settings.cache_clear()


def _conjunto(client, **filtro) -> dict:
    respuesta = client.post(
        "/api/datasets",
        json={"project_id": "local", "name": "clasificar", "filter": filtro, "limit": 10},
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_se_reenvian_las_llamadas_hoja_del_paso_y_nada_mas(local):
    conjunto = _conjunto(local, step_key=PASO)
    cuerpo = local.get(f"/api/datasets/{conjunto['id']}/replay").json()

    assert cuerpo["step_key"] == PASO
    # Una buena por traza: las tres. La del otro paso no es ni reenviable ni excluida.
    assert len(cuerpo["calls"]) == 3
    llamada = cuerpo["calls"][0]
    assert llamada["messages"][0] == {"role": "system", "content": SISTEMA}
    assert llamada["output"] == "facturación"
    assert llamada["model"] == "gpt-5.6-terra"
    assert llamada["cost_usd"] == pytest.approx(0.002)
    assert llamada["input_tokens"] == 900
    # Los parámetros que no cambian de sentido; `response_format` no se lleva.
    assert llamada["params"] == {"temperature": 0, "max_tokens": 20}
    casos = {c["id"] for c in local.get(f"/api/datasets/{conjunto['id']}").json()["items"]}
    assert {c["case_id"] for c in cuerpo["calls"]} == casos


def test_lo_que_no_se_reenvia_se_cuenta_por_motivo(local):
    conjunto = _conjunto(local, step_key=PASO)
    fuera = local.get(f"/api/datasets/{conjunto['id']}/replay").json()["excluded"]
    assert fuera == {
        "herramientas": 3,
        "mensajes_no_texto": 2,
        "fallo": 1,
        "sin_respuesta": 1,
        "tirada_de_evaluacion": 1,
        "no_es_hoja": 1,
    }


def test_sin_paso_en_el_conjunto_valen_todas_las_llamadas(local):
    conjunto = _conjunto(local)
    cuerpo = local.get(f"/api/datasets/{conjunto['id']}/replay").json()
    # Las de «clasificar» y las de «responder» de las tres trazas, y el hijo de tr01.
    assert len(cuerpo["calls"]) == 7


def test_con_modelo_da_su_tarifa_para_el_tope(local):
    conjunto = _conjunto(local, step_key=PASO)
    cuerpo = local.get(
        f"/api/datasets/{conjunto['id']}/replay", params={"model": "gpt-5.6-luna"}
    ).json()
    assert cuerpo["target"]["input_usd_per_mtok"] > 0
    assert cuerpo["target"]["output_usd_per_mtok"] > 0


def test_un_modelo_sin_tarifa_no_tiene_tarifa(local):
    """Sin tarifa no hay tope que se pueda cumplir: el SDK se niega a reenviar."""
    conjunto = _conjunto(local, step_key=PASO)
    cuerpo = local.get(
        f"/api/datasets/{conjunto['id']}/replay", params={"model": "modelo-inventado-9"}
    ).json()
    assert cuerpo["target"] is None


def test_un_conjunto_que_no_existe(local):
    assert local.get("/api/datasets/ds_nada/replay").status_code == 404


# ---------------------------------------------------------------------------------
# La comparación justa: la tirada original cuesta sólo las llamadas reenviadas
# ---------------------------------------------------------------------------------


def _tiradas(client, conjunto: dict, llamadas: list[dict], *, span_extra: str = ""):
    """Las dos tiradas que registra `laplace replay`: la original, que apunta a los
    spans reenviados dentro de las trazas reales, y la barata, con sus trazas nuevas
    (aquí, las mismas trazas reales enteras, para tener algo que costar)."""
    por_caso: dict[str, dict] = {}
    for llamada in llamadas:
        caso = por_caso.setdefault(
            llamada["case_id"],
            {"case_id": llamada["case_id"], "trace_id": llamada["trace_id"], "span_ids": []},
        )
        caso["span_ids"].append(llamada["span_id"])
    if span_extra:
        next(iter(por_caso.values()))["span_ids"].append(span_extra)
    original = client.post(
        "/api/runs",
        json={
            "project_id": "local",
            "dataset_id": conjunto["id"],
            "variant": "original",
            "items": list(por_caso.values()),
        },
    )
    assert original.status_code == 200, original.text
    entera = client.post(
        "/api/runs",
        json={
            "project_id": "local",
            "dataset_id": conjunto["id"],
            "variant": "traza entera",
            "items": [
                {"case_id": c["case_id"], "trace_id": c["trace_id"]} for c in por_caso.values()
            ],
        },
    ).json()
    return original.json(), entera


def test_la_tirada_original_cuesta_solo_las_llamadas_reenviadas(local):
    conjunto = _conjunto(local, step_key=PASO)
    llamadas = local.get(f"/api/datasets/{conjunto['id']}/replay").json()["calls"]
    original, entera = _tiradas(local, conjunto, llamadas)

    cuerpo = local.get(
        "/api/experiments/compare",
        params={"project_id": "local", "a": original["id"], "b": entera["id"]},
    ).json()
    # Tres llamadas de 0,002 $: el paso, no el agente entero (que lleva además la
    # llamada de «responder» y, en tr00, las ocho excluidas).
    assert cuerpo["a"]["cost_usd"] == pytest.approx(0.006)
    assert cuerpo["b"]["cost_usd"] > 0.012
    assert cuerpo["a"]["cost_is_floor"] is False


def test_los_spans_de_cada_caso_se_guardan(local):
    conjunto = _conjunto(local, step_key=PASO)
    llamadas = local.get(f"/api/datasets/{conjunto['id']}/replay").json()["calls"]
    original, _ = _tiradas(local, conjunto, llamadas)
    tiradas = local.get(
        "/api/runs", params={"project_id": "local", "dataset_id": conjunto["id"]}
    ).json()["runs"]
    assert {t["variant"] for t in tiradas} == {"original", "traza entera"}
    resumen = next(t for t in tiradas if t["variant"] == "original")
    assert resumen["cost_usd"] == pytest.approx(0.006)


def test_un_span_que_ya_no_esta_no_cuesta_cero_sin_decirlo(local):
    """Si un span de la tirada original ha caducado, su coste no se sabe: el total pasa
    a ser un suelo, no una cifra más baja que parece exacta."""
    conjunto = _conjunto(local, step_key=PASO)
    llamadas = local.get(f"/api/datasets/{conjunto['id']}/replay").json()["calls"]
    original, entera = _tiradas(local, conjunto, llamadas, span_extra="ffffffffffffffff")
    cuerpo = local.get(
        "/api/experiments/compare",
        params={"project_id": "local", "a": original["id"], "b": entera["id"]},
    ).json()
    assert cuerpo["a"]["cost_is_floor"] is True


def test_el_juez_compara_con_la_respuesta_de_la_llamada(local, monkeypatch):
    """En un paso intermedio la salida final del agente no es la referencia: lo es lo
    que respondió esa llamada. `expected` por traza manda sobre la del caso."""
    from laplace.schema import Annotation, JudgeRun

    from laplace_backend import api_evals
    from laplace_backend.judge import JudgeConfig

    vistos: dict[str, object] = {}

    def juez_falso(config, trace_id, spans, expected=None):
        vistos[trace_id] = expected
        return Annotation(
            id=f"an_{trace_id}",
            trace_id=trace_id,
            source="llm_judge",
            verdict="pass",
            author="juez",
            created_at=datetime.now(timezone.utc),
            judge=JudgeRun(model="juez", prompt_version="v2"),
        )

    monkeypatch.setattr(api_evals, "judge_trace", juez_falso)
    local.app.state.judge = JudgeConfig(enabled=True, model="juez", api_key="x")

    conjunto = _conjunto(local, step_key=PASO)
    llamadas = local.get(f"/api/datasets/{conjunto['id']}/replay").json()["calls"]
    _, entera = _tiradas(local, conjunto, llamadas)
    trazas = [i["trace_id"] for i in entera["items"]]
    respuesta = local.post(
        "/api/judge",
        json={
            "project_id": "local",
            "run_id": entera["id"],
            "expected": {trazas[0]: "facturación"},
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    assert vistos[trazas[0]] == "facturación"
    # Las demás, con la referencia de su caso, como siempre.
    assert vistos[trazas[1]] == "Te la reenvío."
