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
from test_replay import (
    FINAL,
    FOTO,
    HERRAMIENTAS,
    PASO,
    PASO_H,
    PASO_I,
    RESULTADO,
    SISTEMA,
    SISTEMA_H,
    _bucle,
    _imagenes,
    _mira,
    _traza,
)

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
    # Y un paso con herramientas (D-205): dos bucles de OpenAI y uno de Anthropic.
    spans += _bucle("th00") + _bucle("th01") + _bucle("th02", "anthropic")
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
        client.post(
            "/api/datasets",
            json={
                "project_id": "local",
                "name": "resolver",
                "filter": {"step_key": PASO_H},
                "limit": 10,
            },
        )
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
    conjunto = cambios.pop("conjunto", backend.conjunto["name"])
    argumentos.update(cambios)
    return sdk_replay.replay_dataset(conjunto, **argumentos)


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


# ---------------------------------------------------------------------------------
# Con herramientas (D-205): los resultados son los grabados; lo demás corta el caso
# ---------------------------------------------------------------------------------


def _openai_con(recibidas: list[dict], mensaje):
    """Un OpenAI falso que contesta lo que diga `mensaje(cuerpo)`."""

    def responder(request):
        cuerpo = json.loads(request.content)
        recibidas.append(cuerpo)
        respuesta = mensaje(cuerpo)
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
                        "message": respuesta,
                        "finish_reason": "tool_calls" if respuesta.get("tool_calls") else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 520, "completion_tokens": 9, "total_tokens": 529},
            },
        )

    return openai.OpenAI(
        api_key="sk-de-mentira",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(responder)),
    )


def _pide(argumentos: str, id_: str = "call_nuevo") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": id_,
                "type": "function",
                "function": {"name": "buscar_factura", "arguments": argumentos},
            }
        ],
    }


def _busca_y_contesta(cuerpo: dict) -> dict:
    """Pide la misma búsqueda que el original, escrita de otra manera, y contesta."""
    if cuerpo["messages"][-1]["role"] == "tool":
        return {"role": "assistant", "content": "Pagada."}
    return _pide('{ "numero":42 }')


def test_con_herramientas_cada_resultado_es_el_grabado(backend):
    recibidas: list[dict] = []
    resultado = _replay(backend, _openai_con(recibidas, _busca_y_contesta), conjunto="resolver")

    # Los tres bucles, dos vueltas cada uno: el de Anthropic también, con sus bloques
    # convertidos a los de OpenAI (D-213).
    assert resultado.plan.excluded == {}
    assert resultado.plan.chains == 3
    assert resultado.calls_done == 6 and resultado.cases_failed == 0
    assert len(recibidas) == 6
    # El de Anthropic, en el formato de OpenAI: herramientas, petición y resultado.
    de_anthropic = [r for r in recibidas if "th02" in str(r["messages"][1])]
    assert de_anthropic[0]["tools"] == HERRAMIENTAS["openai"]
    assert de_anthropic[0]["messages"] == [
        {"role": "system", "content": SISTEMA_H},
        {"role": "user", "content": "Ticket th02: ¿está pagada la factura 42?"},
    ]
    assert de_anthropic[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_nuevo",
        "content": RESULTADO,
    }
    primera, segunda = recibidas[0], recibidas[1]
    assert primera["tools"] == HERRAMIENTAS["openai"]
    assert primera["messages"][0] == {"role": "system", "content": SISTEMA_H}
    assert len(primera["messages"]) == 2
    # La segunda vuelta lleva la petición del modelo nuevo y, como respuesta, lo que
    # grabó la traza: la herramienta de verdad no se ejecuta.
    assert segunda["messages"][-2]["tool_calls"][0]["id"] == "call_nuevo"
    assert segunda["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_nuevo",
        "content": RESULTADO,
    }

    # Los dos lados comparan el bucle entero: las dos vueltas originales (0,0022 $).
    backend.guardar()
    cuerpo = backend.get(
        "/api/experiments/compare",
        params={
            "project_id": "local",
            "a": resultado.original_run_id,
            "b": resultado.replay_run_id,
        },
    ).json()
    assert cuerpo["a"]["cost_usd"] == pytest.approx(0.0066)
    assert cuerpo["b"]["cases"] == 3 and cuerpo["b"]["crashed"] == 0
    reenviada = backend.store.get_trace_spans(resultado.trace_ids[0], "local")
    raiz = next(s for s in reenviada if s.parent_span_id is None)
    assert raiz.metadata["replay.turns"] == 2
    assert raiz.output == "Pagada."


def test_lo_que_no_se_grabo_corta_el_caso_sin_inventar(backend):
    """Otra factura no está grabada: no se sabe qué devolvería, y el caso se para."""
    recibidas: list[dict] = []
    resultado = _replay(
        backend, _openai_con(recibidas, lambda c: _pide('{"numero": 7}')), conjunto="resolver"
    )
    # Una vuelta por caso y ninguna más: no hay resultado que darle. Los tres bucles,
    # también el de Anthropic convertido (D-213).
    assert len(recibidas) == 3
    assert resultado.cases_cut == 3 and resultado.cases_failed == 3
    backend.guardar()
    cuerpo = backend.get(
        "/api/experiments/compare",
        params={
            "project_id": "local",
            "a": resultado.original_run_id,
            "b": resultado.replay_run_id,
        },
    ).json()
    # Cuenta como fallo, no se descarta: descartarlo dejaría fuera justo los casos en
    # que el modelo nuevo se aparta del original.
    assert cuerpo["b"]["cases"] == 3 and cuerpo["b"]["crashed"] == 3
    tirada = backend.app.state.metadata.get_run(resultado.replay_run_id)
    assert "buscar_factura" in tirada.items[0].error
    assert "no grabó" in tirada.items[0].error


def test_si_contesta_sin_pedir_la_herramienta_vale(backend):
    recibidas: list[dict] = []
    resultado = _replay(
        backend,
        _openai_con(recibidas, lambda c: {"role": "assistant", "content": FINAL}),
        conjunto="resolver",
    )
    assert len(recibidas) == 3
    assert resultado.calls_done == 3 and resultado.cases_failed == 0


def test_con_herramientas_a_anthropic(backend):
    recibidas: list[dict] = []

    def responder(request):
        cuerpo = json.loads(request.content)
        recibidas.append(cuerpo)
        if cuerpo["messages"][-1]["role"] == "user" and isinstance(
            cuerpo["messages"][-1]["content"], list
        ):
            contenido = [{"type": "text", "text": "Pagada."}]
            razon = "end_turn"
        else:
            contenido = [
                {
                    "type": "tool_use",
                    "id": "toolu_nuevo",
                    "name": "buscar_factura",
                    "input": {"numero": 42},
                }
            ]
            razon = "tool_use"
        return httpx2.Response(
            200,
            json={
                "id": "msg_r",
                "type": "message",
                "role": "assistant",
                "model": cuerpo["model"],
                "content": contenido,
                "stop_reason": razon,
                "usage": {"input_tokens": 520, "output_tokens": 9},
            },
        )

    cliente = anthropic.Anthropic(
        api_key="sk-de-mentira",
        http_client=httpx2.Client(transport=httpx2.MockTransport(responder)),
    )
    resultado = _replay(backend, cliente, model="claude-haiku-4-5", conjunto="resolver")
    # Las tres: las dos de OpenAI, convertidas a bloques de Anthropic (D-213).
    assert resultado.plan.excluded == {}
    assert resultado.calls_done == 6 and resultado.cases_failed == 0
    for traza in ("th00", "th02"):
        primera, segunda = [r for r in recibidas if traza in str(r["messages"][0])]
        assert primera["system"] == SISTEMA_H
        assert primera["messages"] == [
            {"role": "user", "content": f"Ticket {traza}: ¿está pagada la factura 42?"}
        ]
        assert primera["tools"] == HERRAMIENTAS["anthropic"]
        assert segunda["messages"][-2]["content"][0]["id"] == "toolu_nuevo"
        assert segunda["messages"][-1] == {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_nuevo", "content": RESULTADO}
            ],
        }


def test_con_herramientas_no_se_pasa_del_tope(backend):
    """Un caso se empieza sólo si cabe entero en lo que queda: lo peor de una
    conversación con una petición grabada son dos vueltas."""
    tarifa = sdk_replay._request(
        f"http://laplace.test/api/datasets/{backend.conjunto['id']}/replay?model={BARATO}"
    )["target"]
    una = sdk_replay._peor_cadena(
        {"input_tokens": 600, "output_tokens": 10, "params": {"max_tokens": 20},
         "recorded": [{}]},
        tarifa,
    )
    recibidas: list[dict] = []
    resultado = _replay(
        backend,
        _openai_con(recibidas, _busca_y_contesta),
        conjunto="resolver",
        max_usd=una * 1.5,
    )
    assert len(recibidas) == 2
    assert resultado.calls_skipped_by_cap == 4


def test_lo_peor_de_una_conversacion():
    """N peticiones grabadas son como mucho N + 1 vueltas; cada una con la entrada de la
    última vuelta original con margen, más la salida máxima de cada vuelta anterior."""
    tarifa = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 10.0}
    cadena = {
        "input_tokens": 1000,
        "output_tokens": 5,
        "params": {"max_tokens": 100},
        "recorded": [{}, {}],
    }
    entrada = 1300 + 1400 + 1500
    assert sdk_replay._peor_cadena(cadena, tarifa) == pytest.approx((entrada + 3 * 1000) / 1e6)


def _grabada(nombre: str, argumentos, turno: int, resultado: str = "") -> dict:
    return {"name": nombre, "arguments": argumentos, "turn": turno, "result": resultado}


def test_las_peticiones_se_casan_por_nombre_argumentos_y_orden():
    a = ("buscar", {"n": 1})
    b = ("buscar", {"n": 2})
    c = ("pagar", {"n": 1})
    grabadas = [
        _grabada("buscar", '{"n": 1}', 0, "r1"),
        _grabada("buscar", {"n": 2}, 0, "r2"),
        _grabada("pagar", '{"n":1}', 1, "r3"),
    ]
    casar = sdk_replay._casar
    assert [g["result"] for g in casar([a, b], grabadas, 0)] == ["r1", "r2"]
    # Lo que el original pidió a la vez se puede pedir en otro orden…
    assert [g["result"] for g in casar([b, a], grabadas, 0)] == ["r2", "r1"]
    # …y la secuencia vale aunque se reparta distinto entre respuestas.
    assert [g["result"] for g in casar([a, b, c], grabadas, 0)] == ["r1", "r2", "r3"]
    assert [g["result"] for g in casar([b, c], grabadas, 1)] == ["r2", "r3"]
    # Lo que el original pidió en vueltas distintas, no se reordena.
    assert casar([c, b], grabadas, 1) is None
    # Saltarse una, otros argumentos o pedir de más: no está grabado.
    assert casar([c], grabadas, 0) is None
    assert casar([("buscar", {"n": 3})], grabadas, 0) is None
    assert casar([c, a], grabadas, 2) is None


def test_la_misma_peticion_repetida_toma_los_resultados_en_orden():
    a = ("leer", {})
    grabadas = [_grabada("leer", "{}", 0, "primero"), _grabada("leer", "{}", 1, "segundo")]
    assert sdk_replay._casar([a], grabadas, 0)[0]["result"] == "primero"
    assert sdk_replay._casar([a], grabadas, 1)[0]["result"] == "segundo"
    assert sdk_replay._casar([a], grabadas, 2) is None


def test_la_linea_de_ordenes_dice_los_cortados(backend, monkeypatch, capsys):
    import laplace
    from laplace import cli

    monkeypatch.setattr(
        sdk_replay, "_cliente", lambda proveedor: _openai_con([], lambda c: _pide("{}"))
    )
    monkeypatch.setattr(laplace, "init", lambda **kwargs: None)
    argumentos = ["replay", "resolver", "--modelo", BARATO, "--tope", "1", "--si"]
    argumentos += ["--proyecto", "local", "--endpoint", "http://laplace.test"]
    assert cli.main(argumentos) == 0
    salida = capsys.readouterr().out
    assert "usan herramientas" in salida
    assert "3 se han cortado" in salida


def test_repetir_una_peticion_mas_veces_de_las_grabadas_corta(backend):
    """El resultado grabado vale una vez: la segunda búsqueda igual no está grabada."""

    def dos_veces(cuerpo: dict) -> dict:
        hechas = sum(1 for m in cuerpo["messages"] if m["role"] == "tool")
        return _pide('{"numero": 42}') if hechas < 2 else {"role": "assistant", "content": "x"}

    recibidas: list[dict] = []
    resultado = _replay(backend, _openai_con(recibidas, dos_veces), conjunto="resolver")
    assert resultado.cases_cut == 3
    assert len(recibidas) == 6


def test_el_plan_cuenta_las_conversaciones_enteras(backend):
    tarifa = sdk_replay._request(
        f"http://laplace.test/api/datasets/{backend.conjunto['id']}/replay?model={BARATO}"
    )["target"]
    resultado = _replay(
        backend, _openai_con([], _busca_y_contesta), conjunto="resolver", confirm=lambda p: False
    )
    plan = resultado.plan
    assert plan.calls == 6 and plan.cases == 3
    assert plan.original_usd == pytest.approx(0.0066)
    assert plan.estimated_usd == pytest.approx(3 * sdk_replay._coste(1100, 18, tarifa))
    cadena = {"input_tokens": 600, "output_tokens": 10, "params": {"max_tokens": 20},
              "recorded": [{}]}
    assert plan.worst_usd == pytest.approx(3 * sdk_replay._peor_cadena(cadena, tarifa))


def test_el_juez_compara_la_conversacion_con_la_respuesta_final(backend, monkeypatch):
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
            judge=JudgeRun(model="juez", prompt_version="v2"),
        )

    monkeypatch.setattr(api_evals, "judge_trace", juez)
    backend.app.state.judge = JudgeConfig(enabled=True, model="juez", api_key="x")
    _replay(backend, _openai_con([], _busca_y_contesta), conjunto="resolver")
    assert referencias == [FINAL, FINAL, FINAL]


# ---------------------------------------------------------------------------------
# Imágenes, otros proveedores y la API Responses (D-213)
# ---------------------------------------------------------------------------------


def _anthropic_con(recibidas: list[dict]):
    """Un Anthropic falso que contesta siempre lo mismo."""

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
                "usage": {"input_tokens": 950, "output_tokens": 3},
            },
        )

    return anthropic.Anthropic(
        api_key="sk-de-mentira",
        http_client=httpx2.Client(transport=httpx2.MockTransport(responder)),
    )


def _conjunto_de(backend, spans: list[Span], paso: str) -> str:
    backend.store.insert_spans(spans)
    backend.post(
        "/api/datasets",
        json={"project_id": "local", "name": paso, "filter": {"step_key": paso}, "limit": 10},
    )
    return paso


def test_las_imagenes_se_convierten_al_formato_de_destino(backend):
    nombre = _conjunto_de(
        backend, _mira("ti00") + _mira("ti01", "anthropic") + _mira("ti02", "responses"), PASO_I
    )
    recibidas: list[dict] = []
    resultado = _replay(
        backend, _anthropic_con(recibidas), model="claude-haiku-4-5", conjunto=nombre
    )
    assert resultado.plan.excluded == {} and resultado.calls_done == 3
    assert resultado.plan.images == 6
    # Las tres, en bloques de Anthropic: la de base64 con su tipo, la otra por URL.
    for cuerpo in recibidas:
        assert cuerpo["system"] == SISTEMA
        assert cuerpo["messages"] == [{"role": "user", "content": _imagenes("anthropic")}]

    recibidas = []
    _replay(backend, _openai(recibidas), conjunto=nombre)
    # A OpenAI: la de Chat, tal cual; las otras dos, convertidas y sin `detail`, que
    # Anthropic no tiene y Responses lleva en otro sitio.
    sin_detalle = [
        {**b, "image_url": {"url": b["image_url"]["url"]}} if "image_url" in b else b
        for b in _imagenes("openai")
    ]
    assert sorted(json.dumps(r["messages"][1]["content"]) for r in recibidas) == sorted(
        json.dumps(c) for c in (_imagenes("openai"), sin_detalle, sin_detalle)
    )


def test_el_plan_dice_que_no_sabe_lo_que_cuestan_las_imagenes(backend):
    nombre = _conjunto_de(backend, _mira("ti00"), PASO_I)
    resultado = _replay(backend, None, conjunto=nombre, confirm=lambda plan: False)
    texto = resultado.plan.describe()
    assert "2 imágenes" in texto and "no se sabe" in texto
    assert "6000 tokens por imagen" in texto


def test_lo_peor_reserva_tokens_por_imagen():
    tarifa = {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 10.0}
    llamada = {"input_tokens": 1000, "params": {"max_tokens": 100}, "images": 2}
    assert sdk_replay._peor(llamada, tarifa) == pytest.approx((1300 + 12000 + 1000) / 1e6)
    # En una conversación, en cada vuelta: las imágenes van en todas.
    cadena = {**llamada, "recorded": [{}]}
    entrada = (1300 + 12000) + (1300 + 12000 + 100)
    assert sdk_replay._peor_cadena(cadena, tarifa) == pytest.approx((entrada + 2000) / 1e6)


def test_una_conversacion_de_la_api_responses_va_por_chat_completions(backend):
    nombre = _conjunto_de(backend, _bucle("thr0", "responses", paso="resolver_r"), "resolver_r")
    recibidas: list[dict] = []
    resultado = _replay(backend, _openai_con(recibidas, _busca_y_contesta), conjunto=nombre)
    assert resultado.plan.excluded == {}
    assert resultado.calls_done == 2 and resultado.cases_failed == 0
    primera, segunda = recibidas
    assert primera["tools"] == HERRAMIENTAS["openai"]
    assert primera["messages"] == [
        {"role": "system", "content": SISTEMA_H},
        {"role": "user", "content": "Ticket thr0: ¿está pagada la factura 42?"},
    ]
    assert segunda["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_nuevo",
        "content": RESULTADO,
    }


def test_lo_que_no_tiene_equivalente_no_se_reenvia_y_se_dice(backend):
    """Un resultado marcado como error y un razonamiento en la historia: Chat
    Completions no tiene dónde ponerlos. A Anthropic, que los grabó, sí van."""
    error = {
        "type": "tool_result",
        "tool_use_id": "call_a",
        "content": "no existe",
        "is_error": True,
    }
    con_error = _bucle(
        "tq00", "anthropic", paso="pensar", entre=[{"role": "user", "content": [error]}]
    )
    pensada = _bucle("tq01", "anthropic", paso="pensar")
    for span in pensada[1:]:
        assert span.llm is not None
        span.llm.input_messages[1:1] = [
            {"role": "user", "content": "Antes de nada."},
            {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "…", "signature": "x"}],
            },
        ]
    normal = _bucle("tq02", "anthropic", paso="pensar")
    nombre = _conjunto_de(backend, con_error + pensada + normal, "pensar")

    plan = _replay(backend, None, conjunto=nombre, confirm=lambda p: False).plan
    assert plan.excluded == {"sin_equivalente": 4} and plan.chains == 1
    texto = plan.describe()
    assert "`thinking`" in texto and "`is_error`" in texto

    plan = _replay(
        backend, None, conjunto=nombre, model="claude-haiku-4-5", confirm=lambda p: False
    ).plan
    assert plan.excluded == {} and plan.chains == 3


def test_la_tabla_de_conversion():
    convertir = sdk_replay._convertir
    pide = [
        {"id": i, "type": "function", "function": {"name": "f", "arguments": f'{{"x": {n}}}'}}
        for i, n in (("a", 1), ("b", 2))
    ]
    chat = [
        {"role": "developer", "content": "S"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "miro", "tool_calls": pide},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": "rb"}]},
        {"role": "user", "content": "¿y?"},
    ]
    # A Anthropic: los resultados seguidos, en un solo mensaje y delante del texto.
    assert convertir(chat, "openai", "anthropic") == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "miro"},
                {"type": "tool_use", "id": "a", "name": "f", "input": {"x": 1}},
                {"type": "tool_use", "id": "b", "name": "f", "input": {"x": 2}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "ra"},
                {"type": "tool_result", "tool_use_id": "b", "content": "rb"},
                {"type": "text", "text": "¿y?"},
            ],
        },
    ]
    # Y de vuelta, lo que había.
    assert convertir(convertir(chat, "openai", "anthropic"), "anthropic", "openai") == [
        {"role": "system", "content": "S"},
        *chat[1:4],
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
        chat[5],
    ]
    # Responses: cada petición y cada resultado, un elemento; en Chat, juntos.
    responses = [
        {"role": "system", "content": "S"},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "mira"},
                {"type": "input_image", "image_url": FOTO},
            ],
        },
        *({"type": "function_call", "call_id": t["id"], **t["function"]} for t in pide),
        {"type": "function_call_output", "call_id": "a", "output": "ra"},
        {"type": "function_call_output", "call_id": "b", "output": "rb"},
    ]
    assert convertir(responses, "responses", "openai") == [
        {"role": "system", "content": "S"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "mira"},
                {"type": "image_url", "image_url": {"url": FOTO}},
            ],
        },
        {"role": "assistant", "content": None, "tool_calls": pide},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
    ]
    # Las herramientas declaradas, en los tres formatos.
    for desde in ("openai", "anthropic", "responses"):
        for hacia in ("openai", "anthropic"):
            convertidas = sdk_replay._convertir_herramientas(HERRAMIENTAS[desde], desde, hacia)
            assert convertidas == HERRAMIENTAS[hacia]


_IMAGEN_EN_RESULTADO = {
    "type": "tool_result",
    "tool_use_id": "a",
    "content": [{"type": "image", "source": {"type": "url", "url": FOTO}}],
}
_ARGUMENTOS_ROTOS = {"id": "a", "type": "function", "function": {"name": "f", "arguments": "{x"}}


@pytest.mark.parametrize(
    ("mensajes", "desde", "hacia", "dice"),
    [
        (
            [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}],
            "openai",
            "anthropic",
            "mitad",
        ),
        (
            [{"role": "assistant", "content": None, "tool_calls": [_ARGUMENTOS_ROTOS]}],
            "openai",
            "anthropic",
            "JSON",
        ),
        (
            [{"role": "user", "content": [_IMAGEN_EN_RESULTADO]}],
            "anthropic",
            "openai",
            "imagen",
        ),
        ([{"type": "reasoning", "id": "rs_1", "summary": []}], "responses", "openai", "reasoning"),
        ([{"role": "user", "content": "a", "name": "ana"}], "openai", "anthropic", "`name`"),
    ],
)
def test_lo_que_no_tiene_equivalente_no_se_convierte(mensajes, desde, hacia, dice):
    with pytest.raises(sdk_replay._SinEquivalente, match=dice):
        sdk_replay._convertir(mensajes, desde, hacia)
