"""El botón de replay de la interfaz local (D-214): el endpoint, no la pantalla.

Se reutiliza el backend de `test_replay_sdk.py` (SQLite, el SDK le llega por un puente) y
los clientes de proveedor reales con el transporte HTTP falso. El lanzador de la prueba
hace en el hilo lo que el real hace en un proceso aparte: `replay_proceso.ejecutar`.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest
import test_replay_sdk
from test_replay_sdk import BARATO, _openai

from laplace_backend import config, replay_proceso


def _lanzador(recibidas: list[dict], parada: threading.Event | None = None):
    def lanzar(spec, on_call):
        if parada is not None:
            parada.wait(10)
        return replay_proceso.ejecutar(spec, client=_openai(recibidas), on_call=on_call)

    return lanzar


def _cuerpo(backend, **cambios) -> dict:
    cuerpo = {
        "project_id": "local",
        "dataset": backend.conjunto["name"],
        "model": BARATO,
        "max_usd": 1.0,
    }
    cuerpo.update(cambios)
    return cuerpo


def _esperar(backend, estado: str, segundos: float = 15) -> dict:
    fin = time.time() + segundos
    while time.time() < fin:
        tirada = backend.get("/api/replay/estado", params={"project_id": "local"}).json()["tirada"]
        if tirada and tirada["estado"] == estado:
            return tirada
        time.sleep(0.05)
    raise AssertionError(f"la tirada no llegó a «{estado}»")


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-de-mentira")


def test_el_plan_no_gasta_nada(backend, con_clave):
    recibidas: list[dict] = []
    backend.app.state.lanzador = _lanzador(recibidas)
    r = backend.post("/api/replay/plan", json=_cuerpo(backend))
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["plan"]["calls"] == 3
    assert plan["clave"] is True and plan["variable"] == "OPENAI_API_KEY"
    # Lo que se enseña como «lo peor» es lo que no se pasará: el menor de lo peor y el tope.
    assert plan["peor_usd"] == pytest.approx(min(plan["plan"]["worst_usd"], 1.0))
    assert recibidas == []


def test_lanzar_corre_en_segundo_plano_y_enseña_el_resultado(backend, con_clave):
    recibidas: list[dict] = []
    backend.app.state.lanzador = _lanzador(recibidas)
    plan = backend.post("/api/replay/plan", json=_cuerpo(backend)).json()
    r = backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=plan["peor_usd"]))
    assert r.status_code == 202, r.text
    tirada = _esperar(backend, "hecha")
    assert tirada["resultado"]["calls_done"] == 3
    assert tirada["resultado"]["replay_run_id"].startswith("run_")
    assert len(recibidas) == 3
    # Terminada, se puede lanzar otra.
    assert backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=1.0)).status_code == 202
    _esperar(backend, "hecha")


def test_una_sola_tirada_a_la_vez_por_proyecto(backend, con_clave):
    parada = threading.Event()
    backend.app.state.lanzador = _lanzador([], parada)
    assert backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=1.0)).status_code == 202
    assert backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=1.0)).status_code == 409
    parada.set()
    _esperar(backend, "hecha")


def test_no_gasta_si_lo_peor_es_mayor_que_lo_aceptado(backend, con_clave):
    recibidas: list[dict] = []
    backend.app.state.lanzador = _lanzador(recibidas)
    r = backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=0.000001))
    assert r.status_code == 202
    _esperar(backend, "cancelada")
    assert recibidas == []


def test_sin_clave_en_el_entorno_no_se_lanza(backend, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    backend.app.state.lanzador = _lanzador([])
    plan = backend.post("/api/replay/plan", json=_cuerpo(backend)).json()
    assert plan["clave"] is False and plan["variable"] == "OPENAI_API_KEY"
    r = backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=1.0))
    assert r.status_code == 409


def test_sin_tarifa_no_hay_plan(backend, con_clave):
    backend.app.state.lanzador = _lanzador([])
    r = backend.post("/api/replay/plan", json=_cuerpo(backend, model="modelo-que-no-existe"))
    assert r.status_code == 422
    assert "tarifa" in r.json()["detail"]


def test_en_la_nube_no_hay_boton_ni_endpoint(backend, con_clave, monkeypatch):
    recibidas: list[dict] = []
    backend.app.state.lanzador = _lanzador(recibidas)
    monkeypatch.setenv("LAPLACE_STORE", "clickhouse")
    config.get_settings.cache_clear()
    try:
        estado = backend.get("/api/replay/estado", params={"project_id": "local"})
        assert estado.json() == {"disponible": False, "tirada": None}
        assert backend.post("/api/replay/plan", json=_cuerpo(backend)).status_code == 404
        r = backend.post("/api/replay", json=_cuerpo(backend, acepta_usd=1.0))
        assert r.status_code == 404
        assert recibidas == []
    finally:
        monkeypatch.setenv("LAPLACE_STORE", "sqlite")
        config.get_settings.cache_clear()


def test_el_proceso_aparte_habla_en_json(tmp_path):
    """El lanzador de verdad: sin Laplace al otro lado, el fallo vuelve como `error`."""
    spec = {
        "project_id": "local",
        "dataset": "x",
        "model": BARATO,
        "max_usd": 1.0,
        "endpoint": "http://127.0.0.1:1",
        "plan_only": True,
    }
    with pytest.raises(RuntimeError):
        replay_proceso.lanzar_proceso(spec, lambda n, total: None, python=sys.executable)


# El fixture de `test_replay_sdk.py`, visible en este módulo.
backend = test_replay_sdk.backend
