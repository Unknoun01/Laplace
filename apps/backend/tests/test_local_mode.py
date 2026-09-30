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


def test_la_interfaz_del_arbol_gana_a_la_copia_del_paquete(tmp_path, monkeypatch):
    """El orden de `_ui_dir()` es una regla, no una casualidad.

    Al revés, una copia vieja dejada por `scripts/build_ui.py` tapa cualquier
    `next build` posterior sin decir nada, y se depura contra una interfaz que no es la
    que se acaba de escribir. Pasó una vez y no se nota hasta que se pierde media hora.
    """
    from laplace_backend import main

    arbol = tmp_path / "arbol"
    paquete = tmp_path / "paquete"
    for carpeta, marca in ((arbol, "recién construida"), (paquete, "copia vieja")):
        carpeta.mkdir()
        (carpeta / "index.html").write_text(marca, encoding="utf-8")

    monkeypatch.setattr(main, "_ui_candidates", lambda: [arbol, paquete])
    assert main._ui_dir() == arbol

    # Y si sólo existe la del paquete —que es el caso de un wheel instalado— se usa esa.
    (arbol / "index.html").unlink()
    assert main._ui_dir() == paquete


def test_se_avisa_cuando_hay_dos_copias_construidas(tmp_path, monkeypatch, caplog):
    """La copia que NO se usa es la que va a confundir a alguien dentro de tres semanas."""
    import logging

    from laplace_backend import main

    arbol = tmp_path / "arbol"
    paquete = tmp_path / "paquete"
    for carpeta in (arbol, paquete):
        carpeta.mkdir()
        (carpeta / "index.html").write_text("x", encoding="utf-8")

    monkeypatch.setattr(main, "_ui_candidates", lambda: [arbol, paquete])
    with caplog.at_level(logging.INFO, logger="laplace"):
        main._log_ui_dir()

    assert f"interfaz servida desde {arbol}" in caplog.text
    assert "NO se está usando" in caplog.text
    assert str(paquete) in caplog.text


def test_una_traza_se_abre_con_el_id_corto_que_ensena_la_interfaz(app_local):
    """La lista enseña los 12 primeros caracteres del id, y pegarlos en la URL daba
    «No encontramos esa traza». Con el proyecto dicho, un prefijo único basta; sin
    proyecto no se busca por prefijo, porque sería rastrear la instalación entera."""
    from helpers import exporter, otlp_body
    from laplace import manual

    exporter.clear()
    with manual.llm_span(model="gpt-5.6-terra", system="openai") as llm:
        llm.record_response(input_tokens=10, output_tokens=2)
    app_local.post(
        "/v1/traces", content=otlp_body(), headers={"content-type": "application/x-protobuf"}
    )
    proyecto = app_local.get("/api/projects").json()["projects"][0]["id"]
    completo = app_local.get("/api/traces", params={"project_id": proyecto}).json()[
        "traces"
    ][0]["trace_id"]

    corta = app_local.get(f"/api/traces/{completo[:12]}", params={"project_id": proyecto})
    assert corta.status_code == 200, corta.text
    assert corta.json()["summary"]["trace_id"] == completo

    assert app_local.get(f"/api/traces/{completo[:12]}").status_code == 404
    assert app_local.get(
        "/api/traces/ffffffffffff", params={"project_id": proyecto}
    ).status_code == 404


def test_la_salud_en_local_no_dice_que_hay_clickhouse(app_local):
    """En local no hay ClickHouse ni Postgres, y `/health` decía `true` para los dos
    porque contestaba con la salud de SQLite bajo esos nombres."""
    salud = app_local.get("/health").json()
    assert salud["store"] == "sqlite" and salud["store_ok"] is True
    assert salud["clickhouse"] is None and salud["postgres"] is None


def test_la_salud_en_la_nube_nombra_clickhouse_y_postgres():
    """La otra mitad: con ClickHouse y Postgres de verdad, `/health` los nombra y dice
    que responden. Se salta si no están."""
    import asyncio
    from types import SimpleNamespace

    import pytest

    from laplace_backend import api
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore
    from laplace_backend.storage.postgres import PostgresMetadataStore

    ajustes = Settings()
    store, metadata = ClickHouseStore(ajustes), PostgresMetadataStore(ajustes)
    if not (store.health() and metadata.health()):
        pytest.skip("no hay ClickHouse y Postgres escuchando")
    peticion = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(store=store, metadata=metadata))
    )
    salud = asyncio.run(api.health(peticion))
    assert salud["store"] == "clickhouse" and salud["metadata"] == "postgres"
    assert salud["clickhouse"] is True and salud["postgres"] is True
