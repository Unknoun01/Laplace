"""Retención por proyecto y borrado de una persona o un cliente (D-173)."""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from laplace.schema import Span

from laplace_backend import retencion
from laplace_backend.config import Settings
from laplace_backend.storage.metadata import SQLiteMetadataStore
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=5)


def _traza(project: str, dias: float, *, user: str = "", customer: str = "") -> list[Span]:
    """Una raíz con su persona y su cliente, y una llamada hija que no los lleva."""
    trace = uuid.uuid4().hex
    inicio = AHORA - timedelta(days=dias)
    raiz = Span(
        span_id=uuid.uuid4().hex[:16], trace_id=trace, project_id=project, name="atender",
        type="agent", status="ok", start_time=inicio,
        end_time=inicio + timedelta(seconds=1), duration_ms=1000.0,
        user_id=user or None, customer_id=customer or None,
    )
    hija = raiz.model_copy(update={
        "span_id": uuid.uuid4().hex[:16], "parent_span_id": raiz.span_id, "name": "chat",
        "type": "llm", "user_id": None, "customer_id": None,
    })
    return [raiz, hija]


def _almacenes(tmp_path):
    from laplace_backend.storage.clickhouse import ClickHouseStore

    local = SQLiteStore(tmp_path / "r.db")
    local.migrate()
    yield "sqlite", local
    nube = ClickHouseStore(Settings())
    if nube.health():
        nube.migrate()
        yield "clickhouse", nube


def _cuantos(store, project: str) -> int:
    return sum(p.span_count for p in store.list_projects() if p.project_id == project)


@pytest.mark.parametrize("cual", ["sqlite", "clickhouse"])
def test_borrar_una_persona_se_lleva_sus_trazas_enteras(tmp_path, cual):
    """El id lo lleva la raíz: dejar las llamadas hijas sería dejar justo su contenido."""
    store = dict(_almacenes(tmp_path)).get(cual)
    if store is None:
        pytest.skip("no hay ClickHouse escuchando")
    project = f"sujeto-{uuid.uuid4().hex[:8]}"
    store.insert_spans(
        _traza(project, 1, user="ana") + _traza(project, 2, user="ana")
        + _traza(project, 1, user="luis", customer="acme")
    )
    try:
        assert store.delete_subject(project, user_id="ana") == 2
        assert _cuantos(store, project) == 2, "sólo quedan la raíz y la hija de luis"
        assert store.delete_subject(project, customer_id="acme") == 1
        assert _cuantos(store, project) == 0
        with pytest.raises(ValueError):
            store.delete_subject(project)
    finally:
        store.delete_project(project)


@pytest.mark.parametrize("cual", ["sqlite", "clickhouse"])
def test_la_retencion_de_un_proyecto_no_toca_otro(tmp_path, cual):
    store = dict(_almacenes(tmp_path)).get(cual)
    if store is None:
        pytest.skip("no hay ClickHouse escuchando")
    uno, otro = f"ret-{uuid.uuid4().hex[:8]}", f"ret-{uuid.uuid4().hex[:8]}"
    store.insert_spans(_traza(uno, 40) + _traza(uno, 1) + _traza(otro, 40))
    try:
        store.delete_project_before(uno, AHORA - timedelta(days=30))
        assert _cuantos(store, uno) == 2
        assert _cuantos(store, otro) == 2
    finally:
        store.delete_project(uno)
        store.delete_project(otro)


def test_manda_la_retencion_mas_corta():
    assert retencion.dias_efectivos(7, 30) == 7
    assert retencion.dias_efectivos(90, 30) == 30, "un proyecto no guarda más que la instalación"
    assert retencion.dias_efectivos(0, 30) == 30
    assert retencion.dias_efectivos(0, 0) == 0


def test_el_bucle_de_fondo_la_aplica_una_vez_al_dia(tmp_path):
    store = SQLiteStore(tmp_path / "b.db")
    store.migrate()
    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    store.insert_spans(_traza("p", 10) + _traza("p", 1))
    assert retencion.aplicar_si_toca(store, meta, "p") is False, "sin días, nada"
    meta.set_setting("p", retencion.CLAVE, {"days": 7})
    assert retencion.aplicar_si_toca(store, meta, "p", ahora=AHORA) is True
    assert _cuantos(store, "p") == 2
    assert retencion.aplicar_si_toca(store, meta, "p", ahora=AHORA + timedelta(hours=3)) is False
    assert retencion.aplicar_si_toca(store, meta, "p", ahora=AHORA + timedelta(days=1)) is True


@pytest.fixture
def api(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    store.insert_spans(_traza("local", 1, user="ana") + _traza("local", 1, user="luis"))
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_RETENTION_DAYS", "30")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_la_api_de_retencion(api):
    r = api.put("/api/retention", json={"project_id": "local", "days": 90}).json()
    assert (r["days"], r["installation_days"], r["effective_days"]) == (90, 30, 30)
    r = api.put("/api/retention", json={"project_id": "local", "days": 7}).json()
    assert r["effective_days"] == 7
    r = api.put("/api/retention", json={"project_id": "local", "days": 0}).json()
    assert (r["days"], r["effective_days"]) == (0, 30)


def test_la_api_de_borrar_una_persona_pide_confirmarla(api):
    mal = api.delete(
        "/api/subjects", params={"project_id": "local", "user_id": "ana", "confirm": "luis"}
    )
    assert mal.status_code == 400
    ambos = api.delete(
        "/api/subjects",
        params={"project_id": "local", "user_id": "ana", "customer_id": "x", "confirm": "ana"},
    )
    assert ambos.status_code == 400
    bien = api.delete(
        "/api/subjects", params={"project_id": "local", "user_id": "ana", "confirm": "ana"}
    )
    assert bien.json() == {"project_id": "local", "deleted_traces": 1}
    quedan = api.get("/api/traces", params={"project_id": "local"}).json()["traces"]
    assert len(quedan) == 1
