"""El Diagnóstico con volumen: ninguna consulta dos veces (D-142).

Con diez millones de spans al día, cada consulta del Diagnóstico pasa del medio segundo,
y el resumen de la ventana se pedía dos veces con los mismos argumentos.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from laplace_backend import insights
from laplace_backend.insights.lecturas import Recordado
from laplace_backend.storage.base import CoverageFacts, Window, WindowSummary

AHORA = datetime.now(timezone.utc)
VENTANA = Window(since=AHORA - timedelta(days=1), until=AHORA, days=1)
ESPERA = 0.2


class Lento:
    """Un almacén en el que cada consulta tarda lo mismo, y que apunta quién pregunta."""

    def __init__(self) -> None:
        self.llamadas: list[str] = []
        self._cerrojo = threading.Lock()

    def _apuntar(self, nombre: str) -> None:
        with self._cerrojo:
            self.llamadas.append(nombre)
        time.sleep(ESPERA)

    def summarize_window(self, project_id, window):
        self._apuntar("summarize_window")
        return WindowSummary(spans=10, traces=2)

    def model_usage(self, project_id, window, min_calls=1):
        self._apuntar("model_usage")
        return []

    def repeated_groups(self, project_id, window, min_repeats=2):
        self._apuntar("repeated_groups")
        return []

    def loop_groups(self, project_id, window, **kwargs):
        self._apuntar("loop_groups")
        return []

    def coverage(self, project_id, window):
        self._apuntar("coverage")
        return CoverageFacts()

    def __getattr__(self, nombre):
        # Cualquier otra lectura del motor: vacía y sin esperar.
        return lambda *a, **k: []


def test_ninguna_consulta_se_hace_dos_veces():
    store = Lento()
    insights.overview(store, "p", VENTANA)
    repetidas = {n for n in store.llamadas if store.llamadas.count(n) > 1}
    assert repetidas == set(), repetidas


def test_un_error_de_una_consulta_llega_igual_que_antes():
    class Roto(Lento):
        def coverage(self, project_id, window):
            raise RuntimeError("clickhouse caído")

    try:
        insights.overview(Roto(), "p", VENTANA)
    except RuntimeError as exc:
        assert "caído" in str(exc)
    else:
        raise AssertionError("el error se tragó")


def test_recordado_devuelve_lo_mismo_que_el_almacen():
    store = Lento()
    envuelto = Recordado(store)
    assert envuelto.summarize_window("p", VENTANA) == WindowSummary(spans=10, traces=2)
    assert envuelto.summarize_window("p", VENTANA) == WindowSummary(spans=10, traces=2)
    assert store.llamadas == ["summarize_window"]


# ---------------------------------------------------------------------------------
# La caché de un minuto (D-142)
# ---------------------------------------------------------------------------------

import importlib  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _app(tmp_path, monkeypatch, cache: str | None):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    if cache is not None:
        monkeypatch.setenv("LAPLACE_OVERVIEW_CACHE_S", cache)
    from laplace_backend import api, config, main

    config.get_settings.cache_clear()
    api.CACHE_DIAGNOSTICO.olvidar()
    importlib.reload(main)
    return TestClient(main.app)


def _mandar(cliente, n: int) -> None:
    from helpers import exporter, otlp_body
    from laplace import manual

    exporter.clear()
    for _ in range(n):
        with manual.llm_span(model="gpt-5.6-luna", system="openai") as llm:
            llm.record_response(input_tokens=1000, output_tokens=10)
    r = cliente.post(
        "/v1/traces", content=otlp_body(), headers={"content-type": "application/x-protobuf"}
    )
    assert r.status_code == 200


def _gasto(cliente) -> float:
    proyecto = cliente.get("/api/projects").json()["projects"][0]["id"]
    return cliente.get("/api/overview", params={"project_id": proyecto}).json()["window_cost_usd"]


@pytest.fixture(autouse=True)
def _ajustes_limpios():
    yield
    from laplace_backend import config

    config.get_settings.cache_clear()


def test_en_la_nube_el_diagnostico_se_sirve_de_la_cache_un_minuto(tmp_path, monkeypatch):
    """Con diez millones de spans al día el Diagnóstico tarda segundos, y se pide al
    abrir el inicio, la lista y cada traza. Durante un minuto se sirve el mismo."""
    with _app(tmp_path, monkeypatch, "60") as cliente:
        _mandar(cliente, 1)
        antes = _gasto(cliente)
        _mandar(cliente, 3)
        assert _gasto(cliente) == antes, "dentro del minuto, el mismo Diagnóstico"


def test_cualquier_cambio_por_la_api_invalida_la_cache(tmp_path, monkeypatch):
    """Marcar un hallazgo, cambiar una tarifa o cargar la demo cambian lo que dice el
    Diagnóstico: no pueden esperar un minuto a verse."""
    with _app(tmp_path, monkeypatch, "60") as cliente:
        _mandar(cliente, 1)
        antes = _gasto(cliente)
        _mandar(cliente, 3)
        proyecto = cliente.get("/api/projects").json()["projects"][0]["id"]
        r = cliente.put("/api/budget", json={"project_id": proyecto, "monthly_limit": 10})
        assert r.status_code == 200
        assert _gasto(cliente) > antes


def test_en_local_no_hay_cache(tmp_path, monkeypatch):
    """Quien acaba de mandar trazas desde su portátil espera verlas ya, y en local el
    volumen no la necesita."""
    with _app(tmp_path, monkeypatch, None) as cliente:
        _mandar(cliente, 1)
        antes = _gasto(cliente)
        _mandar(cliente, 3)
        assert _gasto(cliente) > antes
