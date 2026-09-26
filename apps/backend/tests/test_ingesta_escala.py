"""La ingesta con volumen: lo que cuesta cada lote más allá de escribirlo (Fase 4).

Con un agente de verdad llegan cientos de lotes por minuto, y cada uno hacía tres cosas
de más: traducir el protobuf dentro del bucle de eventos —bloqueando cualquier otra
petición mientras tanto—, escribir en la base de metadatos que el proyecto existe —una
vez por lote, siempre el mismo—, y una inserción síncrona en ClickHouse que crea una
parte nueva por lote (D-142).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from laplace_backend.storage.metadata import SQLiteMetadataStore


@pytest.fixture
def app_local(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, main
    config.get_settings.cache_clear()


def _lote() -> bytes:
    from helpers import exporter, otlp_body
    from laplace import manual

    exporter.clear()
    with manual.llm_span(model="gpt-5.6-luna", system="openai") as llm:
        llm.record_response(input_tokens=10, output_tokens=2)
    return otlp_body()


def test_traducir_el_lote_no_bloquea_el_bucle_de_eventos(app_local, monkeypatch):
    """Traducir diez mil spans son decenas de milisegundos de CPU: en el bucle de
    eventos, ninguna otra petición avanza mientras tanto."""
    cliente, main = app_local
    from laplace_backend import api

    original = api.parse_spans
    hilos: list[bool] = []

    def espia(peticion):
        try:
            asyncio.get_running_loop()
            hilos.append(True)
        except RuntimeError:
            hilos.append(False)
        return original(peticion)

    monkeypatch.setattr(api, "parse_spans", espia)
    r = cliente.post(
        "/v1/traces", content=_lote(), headers={"content-type": "application/x-protobuf"}
    )
    assert r.status_code == 200
    assert hilos == [False], "parse_spans corrió dentro del bucle de eventos"


def test_el_proyecto_se_registra_una_vez_y_no_en_cada_lote(tmp_path, monkeypatch):
    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    escrituras: list[str] = []
    conectar = meta._conn

    def contando():
        escrituras.append("x")
        return conectar()

    monkeypatch.setattr(meta, "_conn", contando)
    for _ in range(5):
        meta.ensure_project("agente")
    assert len(escrituras) == 1


def test_un_proyecto_borrado_se_vuelve_a_registrar(tmp_path):
    """Si la caché sobreviviera al borrado, el proyecto no volvería a aparecer aunque
    le siguieran llegando trazas."""
    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    meta.ensure_project("agente")
    meta.delete_project_data("agente")
    meta.ensure_project("agente")
    assert [p.id for p in meta.list_projects()] == ["agente"]


def test_postgres_tambien_registra_una_vez(monkeypatch):
    from laplace_backend.config import Settings
    from laplace_backend.storage.postgres import PostgresMetadataStore

    meta = PostgresMetadataStore(Settings())
    if not meta.health():
        pytest.skip("no hay Postgres escuchando")
    conectar = meta._connect
    escrituras: list[str] = []

    def contando():
        escrituras.append("x")
        return conectar()

    monkeypatch.setattr(meta, "_connect", contando)
    for _ in range(5):
        meta.ensure_project("carga-registro-unico")
    assert len(escrituras) == 1
    meta.delete_project_data("carga-registro-unico")


def test_clickhouse_inserta_en_asincrono_y_espera_la_confirmacion(monkeypatch):
    """`async_insert` junta los lotes pequeños en el servidor en vez de crear una parte
    por lote; `wait_for_async_insert` hace que el 200 de la ingesta siga significando
    «guardado», que es lo que permite al exportador no reintentar."""
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()

    ajustes: list[dict] = []
    cliente = store._client
    insertar = cliente.insert

    def espia(*args, **kwargs):
        ajustes.append(kwargs.get("settings") or {})
        return insertar(*args, **kwargs)

    monkeypatch.setattr(cliente, "insert", espia)
    from datetime import datetime, timedelta, timezone

    from laplace.schema import Span

    ahora = datetime.now(timezone.utc)
    span = Span(
        span_id="c" * 16,
        trace_id="d" * 32,
        project_id="carga-async",
        name="x",
        start_time=ahora - timedelta(seconds=1),
        end_time=ahora,
    )
    try:
        store.insert_spans([span])
        assert ajustes and ajustes[0].get("async_insert") == 1
        assert ajustes[0].get("wait_for_async_insert") == 1
        # Y se lee en cuanto vuelve: esperar la confirmación es justo eso.
        assert [s.span_id for s in store.get_trace_spans("d" * 32, "carga-async")] == ["c" * 16]
    finally:
        store.delete_project("carga-async")


#: Las columnas con el contenido en crudo: son casi todo el disco (D-142).
PAYLOADS = (
    "input_messages",
    "output_messages",
    "llm_params",
    "tool_arguments",
    "tool_output",
    "retrieval_documents",
    "input_payload",
    "output_payload",
    "metadata",
    "events",
    "attributes",
)


def test_los_payloads_se_comprimen_con_zstd():
    """Con la prueba de carga, ZSTD(3) deja estas columnas en menos de la mitad que el
    LZ4 por defecto. Se leen sólo al abrir una traza, así que el coste de descomprimir
    no toca a las pantallas que agregan."""
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    filas = store._client.query(
        "SELECT name, compression_codec FROM system.columns "
        "WHERE database = currentDatabase() AND table = 'spans'"
    ).result_rows
    codecs = dict(filas)
    for columna in PAYLOADS:
        assert "ZSTD(3)" in codecs[columna], (columna, codecs[columna])
