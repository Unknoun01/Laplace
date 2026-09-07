"""El modo local, montado como lo monta `laplace ui`.

Un solo proceso de Python sirviendo la ingesta, la API y la interfaz contra un fichero
SQLite. Lo que se comprueba aquí es que las tres cosas conviven: que el comodín que
sirve la interfaz no se coma las rutas de la API, que no se pueda salir de su carpeta, y
que la elección de almacén sea de verdad lo único que cambia entre local y nube.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from laplace_backend.config import Settings
from laplace_backend.main import build_store
from laplace_backend.storage.sqlite import SQLiteStore


def test_la_configuracion_elige_el_almacen(tmp_path):
    local = build_store(Settings(store="sqlite", sqlite_path=str(tmp_path / "x.db")))
    assert isinstance(local, SQLiteStore)


def test_por_defecto_sigue_siendo_la_nube():
    """Cambiar el defecto convertiría cualquier despliegue en un SQLite silencioso."""
    assert Settings().store == "clickhouse"


@pytest.fixture
def app_local(tmp_path, monkeypatch):
    """La aplicación tal cual la arranca el CLI: sqlite, sin Postgres."""
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_la_api_y_la_interfaz_conviven_en_el_mismo_puerto(app_local):
    """El comodín de la interfaz se registra DESPUÉS del router.

    FastAPI resuelve por orden de registro: un `/{ruta:path}` declarado antes se
    comería `/api` y `/health`, y el modo local arrancaría con una interfaz bonita
    y sin datos.
    """
    assert app_local.get("/health").status_code == 200
    assert app_local.get("/api/projects").json() == {"projects": []}


def test_la_interfaz_no_deja_salir_de_su_carpeta(app_local):
    """Un servidor de ficheros que acepta `..` es un servidor de ficheros ajenos."""
    for intento in (
        "/../../../../etc/passwd",
        "/..%2f..%2fetc%2fpasswd",
        "/_next/../../../secret.txt",
    ):
        respuesta = app_local.get(intento)
        assert respuesta.status_code in (404, 501), intento
        assert "root:" not in respuesta.text


def test_una_traza_entra_y_sale_por_el_mismo_proceso(app_local):
    """La prueba de que `laplace ui` es el producto entero, no sólo una pantalla."""
    from helpers import exporter, otlp_body
    from laplace import manual

    exporter.clear()
    with manual.llm_span(
        model="gpt-5.6-terra",
        system="openai",
        input_messages=[{"role": "user", "content": "hola"}],
    ) as llm:
        llm.record_response(
            output_messages=[{"role": "assistant", "content": "qué tal"}],
            input_tokens=100,
            output_tokens=10,
        )

    cuerpo = otlp_body()
    respuesta = app_local.post(
        "/v1/traces", content=cuerpo, headers={"content-type": "application/x-protobuf"}
    )
    assert respuesta.status_code == 200

    proyectos = app_local.get("/api/projects").json()["projects"]
    assert len(proyectos) == 1
    trazas = app_local.get("/api/traces", params={"project_id": proyectos[0]["id"]})
    assert trazas.json()["traces"], "la traza tiene que verse por la misma API"
