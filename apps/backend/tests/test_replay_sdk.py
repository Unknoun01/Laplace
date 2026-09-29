"""Replay contrafactual, lado del SDK: `laplace replay` de punta a punta (D-167).

El backend es el de `laplace ui` en memoria; las peticiones del SDK le llegan por un
puente, y los spans que el proceso exporta se guardan en su almacén como los guardaría
la ingesta. Los clientes de OpenAI y Anthropic son los reales, con el transporte HTTP
falso: se comprueba lo que de verdad les llega.
"""

from __future__ import annotations

import importlib
import json
import urllib.parse
from datetime import datetime, timezone

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from helpers import exporter, ingest
from laplace import evals as sdk_evals
from laplace import replay as sdk_replay
from laplace.evals import EvalError
from laplace.integrations import anthropic as ai
from laplace.integrations import openai as oi
from laplace.schema import Span
from laplace.semconv import EVAL_TAG
from test_replay import PASO, SISTEMA, _traza

from laplace_backend.storage.sqlite import SQLiteStore

openai = pytest.importorskip("openai")
anthropic = pytest.importorskip("anthropic")

BARATO = "gpt-5.6-luna"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans: list[Span] = []
    for i in range(3):
        spans += _traza(f"tr{i:02d}")
    store.insert_spans(spans)

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    def guardar() -> None:
        """Lo exportado, al almacén. El proveedor de OTel de las pruebas lo marca con
        otro proyecto; con `laplace.init(project=...)` es el mismo que el del conjunto."""
        nuevos = ingest()
        for span in nuevos:
            span.project_id = "local"
        store.insert_spans(nuevos)
        exporter.clear()

    with TestClient(main.app) as client:
        peticiones: list[tuple[str, str]] = []

        def puente(url: str, *, method: str = "GET", payload=None):
            # Lo exportado hasta ahora llega antes que la petición, como con el
            # `flush()` del SDK contra un servidor de verdad.
            guardar()
            partes = urllib.parse.urlsplit(url)
            ruta = partes.path + (f"?{partes.query}" if partes.query else "")
            peticiones.append((method, partes.path))
            respuesta = client.request(method, ruta, json=payload)
            if respuesta.status_code >= 400:
                raise EvalError(f"{respuesta.status_code}: {respuesta.text}")
            return respuesta.json()

        monkeypatch.setattr(sdk_evals, "_request", puente)
        monkeypatch.setattr(sdk_replay, "_request", puente)
        oi.instrument()
        ai.instrument()
        conjunto = client.post(
            "/api/datasets",
            json={
                "project_id": "local",
                "name": "clasificar",
                "filter": {"step_key": PASO},
                "limit": 10,
            },
        ).json()
        client.store = store  # type: ignore[attr-defined]
        client.guardar = guardar  # type: ignore[attr-defined]
        client.conjunto = conjunto  # type: ignore[attr-defined]
        client.peticiones = peticiones  # type: ignore[attr-defined]
        yield client
        oi.uninstrument()
        ai.uninstrument()
    config.get_settings.cache_clear()


def _openai(recibidas: list[dict], *, falla_en: int | None = None):
    def responder(request):
        cuerpo = json.loads(request.content)
        recibidas.append(cuerpo)
        if falla_en is not None and len(recibidas) == falla_en:
            return httpx.Response(500, json={"error": {"message": "caído", "type": "server"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-r",
                "object": "chat.completion",
                "created": 1770000000,
                "model": cuerpo["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "facturación"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 950, "completion_tokens": 3, "total_tokens": 953},
            },
        )

    return openai.OpenAI(
        api_key="sk-de-mentira",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(responder)),
    )


def _replay(backend, cliente, **cambios):
    argumentos = {
        "model": BARATO,
        "max_usd": 1.0,
        "project": "local",
        "endpoint": "http://laplace.test",
        "client": cliente,
        "confirm": lambda plan: True,
    }
    argumentos.update(cambios)
    return sdk_replay.replay_dataset(backend.conjunto["name"], **argumentos)


def test_el_replay_entero(backend):
    recibidas: list[dict] = []
    resultado = _replay(backend, _openai(recibidas))

    assert resultado.calls_done == 3
    assert resultado.calls_skipped_by_cap == 0
    # Lo que llega al proveedor es la llamada real: los mismos mensajes, los parámetros
    # que no cambian de sentido y un máximo de salida, que es lo que hace cumplible el tope.
    assert recibidas[0]["model"] == BARATO
    assert recibidas[0]["messages"][0] == {"role": "system", "content": SISTEMA}
    assert recibidas[0]["temperature"] == 0
    assert recibidas[0]["max_completion_tokens"] == 20
    assert "response_format" not in recibidas[0]

    # Dos tiradas del mismo conjunto, y la comparación con el coste de las mismas
    # llamadas a los dos lados: el original, 0,002 $ por llamada; el barato, medido.
    backend.guardar()
    cuerpo = backend.get(
        "/api/experiments/compare",
        params={
            "project_id": "local",
            "a": resultado.original_run_id,
            "b": resultado.replay_run_id,
        },
    ).json()
    assert cuerpo["a"]["variant"] == "original (gpt-5.6-terra)"
    assert cuerpo["b"]["variant"] == f"replay {BARATO}"
    assert cuerpo["a"]["cost_usd"] == pytest.approx(0.006)
    assert 0 < cuerpo["b"]["cost_usd"] < cuerpo["a"]["cost_usd"]
    assert cuerpo["b"]["cases"] == 3


def test_lo_reenviado_no_cuenta_como_trafico_real(backend):
    _replay(backend, _openai([]))
    backend.guardar()
    reenviadas = [
        t
        for t in backend.get("/api/traces", params={"project_id": "local", "limit": 50}).json()[
            "traces"
        ]
        if t["root_name"].startswith("eval:")
    ]
    assert len(reenviadas) == 3
    spans = backend.store.get_trace_spans(reenviadas[0]["trace_id"], "local")
    assert EVAL_TAG in spans[0].tags


def test_no_se_pasa_del_tope(backend):
    """Lo peor de una llamada aquí: 900 × 1,3 de entrada y 20 de salida. Con un tope
    que sólo cubre una, se reenvía una y las demás se dicen, no se hacen."""
    tarifa = sdk_replay._request(
        f"http://laplace.test/api/datasets/{backend.conjunto['id']}/replay?model={BARATO}"
    )["target"]
    una = sdk_replay._peor({"input_tokens": 900, "params": {"max_tokens": 20}}, tarifa)
    recibidas: list[dict] = []
    resultado = _replay(backend, _openai(recibidas), max_usd=una * 1.5)

    assert len(recibidas) == 1
    assert resultado.calls_done == 1
    assert resultado.calls_skipped_by_cap == 2
    assert resultado.spent_usd <= una * 1.5
    tirada = backend.get(
        "/api/runs", params={"project_id": "local", "dataset_id": backend.conjunto["id"]}
    ).json()["runs"]
    assert all(t["cases"] == 1 for t in tirada), "los dos lados, con los mismos casos"


def test_sin_permiso_no_se_gasta_nada(backend):
    recibidas: list[dict] = []
    vistos = []
    resultado = _replay(
        backend, _openai(recibidas), confirm=lambda plan: vistos.append(plan) or False
    )
    assert resultado.cancelled is True
    assert recibidas == []
    assert ("POST", "/api/runs") not in backend.peticiones
    # Y lo que se enseñó para pedir permiso dice lo que se iba a hacer.
    plan = vistos[0]
    assert plan.calls == 3 and plan.original_usd == pytest.approx(0.006)
    assert 0 < plan.estimated_usd <= plan.worst_usd
    assert "Tope: 1.00 $" in plan.describe()


def test_un_modelo_sin_tarifa_no_se_reenvia(backend):
    recibidas: list[dict] = []
    with pytest.raises(EvalError, match="no tiene tarifa"):
        _replay(backend, _openai(recibidas), model="modelo-inventado-9")
    assert recibidas == []


def test_un_caso_que_falla_cuenta_como_fallo_y_no_corta(backend):
    recibidas: list[dict] = []
    resultado = _replay(backend, _openai(recibidas, falla_en=2))
    assert len(recibidas) == 3
    assert resultado.cases_failed == 1
    assert resultado.calls_done == 2


def test_a_anthropic_las_instrucciones_van_en_system(backend):
    recibidas: list[dict] = []

    def responder(request):
        cuerpo = json.loads(request.content)
        recibidas.append(cuerpo)
        return httpx2.Response(
            200,
            json={
                "id": "msg_r",
                "type": "message",
                "role": "assistant",
                "model": cuerpo["model"],
                "content": [{"type": "text", "text": "facturación"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 950, "output_tokens": 3},
            },
        )

    cliente = anthropic.Anthropic(
        api_key="sk-de-mentira",
        http_client=httpx2.Client(transport=httpx2.MockTransport(responder)),
    )
    resultado = _replay(backend, cliente, model="claude-haiku-4-5")
    assert resultado.plan.provider == "anthropic"
    assert recibidas[0]["system"] == SISTEMA
    assert [m["role"] for m in recibidas[0]["messages"]] == ["user"]
    assert recibidas[0]["max_tokens"] == 20


def test_el_juez_compara_con_la_respuesta_original(backend, monkeypatch):
    from laplace.schema import Annotation, JudgeRun

    from laplace_backend import api_evals
    from laplace_backend.judge import JudgeConfig

    referencias = []

    def juez(config, trace_id, spans, expected=None):
        referencias.append(expected)
        return Annotation(
            id=f"an_{trace_id}",
            trace_id=trace_id,
            source="llm_judge",
            verdict="pass",
            author="juez",
            created_at=datetime.now(timezone.utc),
            judge=JudgeRun(model="juez", cost_usd=0.0001, prompt_version="v2"),
        )

    monkeypatch.setattr(api_evals, "judge_trace", juez)
    backend.app.state.judge = JudgeConfig(enabled=True, model="juez", api_key="x")
    resultado = _replay(backend, _openai([]))
    assert resultado.judged == 3 and resultado.passed == 3
    assert resultado.judge_cost_usd == pytest.approx(0.0003)
    # La referencia es lo que respondió la llamada original, no la salida del agente.
    assert referencias == ["facturación"] * 3


def test_con_el_juez_apagado_se_dice(backend):
    resultado = _replay(backend, _openai([]))
    assert resultado.judged == 0
    assert "apagado" in resultado.judge_note


def test_la_linea_de_ordenes_no_gasta_sin_permiso(backend, monkeypatch, capsys):
    """Sin nadie delante a quien preguntar y sin `--si`, no se gasta nada."""
    import laplace
    from laplace import cli

    recibidas: list[dict] = []
    monkeypatch.setattr(sdk_replay, "_cliente", lambda proveedor: _openai(recibidas))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # `init` de verdad montaría un exportador global hacia un host que no existe, y
    # las pruebas que vienen detrás lo heredarían. Lo que importa aquí es la orden.
    monkeypatch.setattr(laplace, "init", lambda **kwargs: None)
    codigo = cli.main(
        [
            "replay",
            backend.conjunto["name"],
            "--modelo",
            BARATO,
            "--tope",
            "1",
            "--proyecto",
            "local",
            "--endpoint",
            "http://laplace.test",
        ]
    )
    assert codigo == 1
    assert recibidas == []
    assert "--si" in capsys.readouterr().err

    codigo = cli.main(
        [
            "replay",
            backend.conjunto["name"],
            "--modelo",
            BARATO,
            "--tope",
            "1",
            "--proyecto",
            "local",
            "--endpoint",
            "http://laplace.test",
            "--si",
        ]
    )
    assert codigo == 0
    assert len(recibidas) == 3


def test_lo_peor_de_una_llamada_lleva_margen_y_la_salida_maxima():
    """Otro tokenizador puede contar más tokens de entrada: sin margen, el tope sólo se
    cumpliría si los dos modelos tokenizan igual. Y la salida es la máxima permitida,
    no la que dio el original, que es justo lo que no se sabe."""
    tarifa = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 10.0}
    llamada = {"input_tokens": 1000, "output_tokens": 5, "params": {"max_tokens": 100}}
    assert sdk_replay._peor(llamada, tarifa) == pytest.approx((1300 * 1 + 100 * 10) / 1e6)
    # Sin máximo fijado: cuatro veces lo que respondió, entre 256 y 4096.
    assert sdk_replay._salida_maxima({"output_tokens": 5}) == 256
    assert sdk_replay._salida_maxima({"output_tokens": 500}) == 2000
    assert sdk_replay._salida_maxima({"output_tokens": 5000}) == 4096
