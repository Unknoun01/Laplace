# Las fixtures `cerrado` y `app` se importan de sus ficheros y se piden por nombre, que
# es justo lo que F811 toma por una redefinición.
# ruff: noqa: F811
"""La deuda técnica de la auditoría de septiembre de 2026 (D-129).

Menos ataques que en `test_auditoria_p1.py` y más contratos: que los dos almacenes de
trazas prometan lo mismo, que filtrar no mire otros proyectos, que borrar no deje las
cosas a medias, que el SDK no castigue al agente cuando Laplace no contesta. Lo que
necesita ClickHouse o Postgres de verdad se salta solo si no están.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_auth import _cab, _span, cerrado  # noqa: E402,F401
from test_cuentas import (  # noqa: E402,F401
    CONTRASENA,
    H,
    _cliente,
    _configurar,
    _invitar_y_aceptar,
    app,
)

# ---------------------------------------------------------------------------------
# Los dos almacenes de trazas cumplen el mismo contrato
# ---------------------------------------------------------------------------------


def _metodos_de(protocolo) -> list[str]:
    return [n for n, v in vars(protocolo).items() if callable(v) and not n.startswith("_")]


def _metodos_span_store() -> list[str]:
    from laplace_backend.storage.base import SpanStore

    return _metodos_de(SpanStore)


@pytest.mark.parametrize("nombre", _metodos_span_store())
def test_sqlite_y_clickhouse_tienen_la_firma_del_protocolo(nombre):
    """La misma red que la de metadatos (D-128), para el almacén de trazas."""
    from laplace_backend.storage.base import SpanStore
    from laplace_backend.storage.clickhouse import ClickHouseStore
    from laplace_backend.storage.sqlite import SQLiteStore

    esperada = list(inspect.signature(getattr(SpanStore, nombre)).parameters)
    for clase in (SQLiteStore, ClickHouseStore):
        metodo = getattr(clase, nombre, None)
        assert metodo is not None, f"{clase.__name__} no tiene {nombre}"
        assert list(inspect.signature(metodo).parameters) == esperada, (
            f"{clase.__name__}.{nombre} no tiene la firma del protocolo"
        )


def test_lo_que_usan_las_rutas_está_en_el_protocolo():
    """Si un almacén tiene un método público que el otro también tiene, es contrato."""
    from laplace_backend.storage.base import SpanStore
    from laplace_backend.storage.clickhouse import ClickHouseStore
    from laplace_backend.storage.sqlite import SQLiteStore

    def publicos(clase):
        return {
            n
            for n, v in vars(clase).items()
            if (callable(v) or isinstance(v, property)) and not n.startswith("_")
        }

    protocolo = publicos(SpanStore)
    comunes = publicos(SQLiteStore) & publicos(ClickHouseStore)
    assert comunes <= protocolo, f"fuera del protocolo: {sorted(comunes - protocolo)}"


# ---------------------------------------------------------------------------------
# Filtrar no mira otros proyectos
# ---------------------------------------------------------------------------------


def _store(tmp_path):
    from laplace_backend.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "x.db")
    store.migrate()
    return store


def test_los_filtros_de_la_lista_se_quedan_en_el_proyecto(tmp_path):
    from laplace_backend.storage.base import TraceFilter

    store = _store(tmp_path)
    for proyecto, traza in (("a", "t-a"), ("b", "t-b")):
        s = _span(proyecto, traza)
        s.session_id = "sesion-compartida"
        s.status = "error"
        store.insert_spans([s])

    for filtro in (
        {"session_id": "sesion-compartida"},
        {"status": "error"},
        {"model": "gpt-5.6-luna"},
        {"search": "chat"},
    ):
        pagina = store.list_traces(TraceFilter(project_id="a", limit=50, **filtro))
        assert [t.trace_id for t in pagina.traces] == ["t-a"], filtro


def test_buscar_un_guion_bajo_no_es_un_comodín(tmp_path):
    from laplace_backend.storage.base import TraceFilter

    store = _store(tmp_path)
    store.insert_spans([_span("a", "abc123"), _span("a", "a_c999")])
    pagina = store.list_traces(TraceFilter(project_id="a", limit=50, search="a_c"))
    assert [t.trace_id for t in pagina.traces] == ["a_c999"]


def test_modelos_sin_tarifa_en_una_consulta(tmp_path):
    from laplace_backend.storage.base import Window

    store = _store(tmp_path)
    for proyecto, modelo in (("a", "modelo-raro"), ("b", "otro-raro")):
        s = _span(proyecto, f"t-{proyecto}")
        s.llm.request_model = modelo
        s.llm.cost.unknown = True
        store.insert_spans([s])
    ventana = Window(
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        until=datetime(2026, 10, 1, tzinfo=timezone.utc),
        days=30,
    )
    assert store.unpriced_models(["a"], ventana) == ["modelo-raro"]
    assert store.unpriced_models(None, ventana) == ["modelo-raro", "otro-raro"]
    assert store.unpriced_models([], ventana) == []


# ---------------------------------------------------------------------------------
# El juez: varias a la vez, con tope, y en orden
# ---------------------------------------------------------------------------------


def test_el_juez_trabaja_en_paralelo_con_tope_y_conserva_el_orden(cerrado, monkeypatch):
    from laplace.schema import Annotation, JudgeRun

    from laplace_backend import api_evals

    client, claves = cerrado
    ids = [f"t-juez-{i}" for i in range(8)]
    client.app.state.store.insert_spans([_span("mio", t) for t in ids])
    client.app.state.judge = SimpleNamespace(enabled=True, model="falso")

    en_marcha = 0
    maximo = 0
    cerrojo = threading.Lock()

    def juez_falso(config, trace_id, spans, expected=None):
        nonlocal en_marcha, maximo
        with cerrojo:
            en_marcha += 1
            maximo = max(maximo, en_marcha)
        time.sleep(0.15)
        with cerrojo:
            en_marcha -= 1
        return Annotation(
            id=uuid.uuid4().hex,
            trace_id=trace_id,
            source="llm_judge",
            verdict="pass",
            author="juez-falso",
            created_at=datetime.now(timezone.utc),
            judge=JudgeRun(model="falso", cost_usd=0.001, prompt_version="v-prueba"),
        )

    monkeypatch.setattr(api_evals, "judge_trace", juez_falso)
    r = client.post(
        "/api/judge",
        json={"project_id": "mio", "trace_ids": [*ids, "t-ajeno"]},
        headers=_cab(claves, "mio"),
    )
    assert r.status_code == 200, r.text
    datos = r.json()
    assert [a["trace_id"] for a in datos["annotations"]] == ids
    assert [f["trace_id"] for f in datos["failed"]] == ["t-ajeno"]
    assert 1 < maximo <= api_evals.JUDGE_CONCURRENCY


# ---------------------------------------------------------------------------------
# Borrar un proyecto no deja las cosas a medias
# ---------------------------------------------------------------------------------


def test_si_no_se_pueden_borrar_los_metadatos_no_se_borran_las_trazas(cerrado, monkeypatch):
    from laplace_backend.storage.metadata import MetadataUnavailable

    client, claves = cerrado

    def caido(project_id):
        raise MetadataUnavailable("postgres no responde")

    monkeypatch.setattr(client.app.state.metadata, "delete_project_data", caido)
    r = client.delete(
        "/api/projects", params={"project_id": "mio", "confirm": "mio"}, headers=_cab(claves, "*")
    )
    assert r.status_code == 503
    trazas = client.get("/api/traces", params={"project_id": "mio"}, headers=_cab(claves, "*"))
    assert trazas.json()["traces"], "las trazas no pueden irse si lo demás se ha quedado"


# ---------------------------------------------------------------------------------
# Lo que devuelve el servidor
# ---------------------------------------------------------------------------------


def test_toda_respuesta_lleva_las_cabeceras_de_seguridad(cerrado):
    client, claves = cerrado
    for r in (
        client.get("/api/projects", headers=_cab(claves, "mio")),
        client.get("/api/projects"),  # un 401 también
        client.get("/health"),
    ):
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_la_interfaz_acepta_head(cerrado):
    """Next 16 precarga con HEAD; un 405 por cada enlace ensuciaba la consola."""
    client, _ = cerrado
    assert client.head("/").status_code != 405


def test_los_segmentos_de_next_se_buscan_donde_los_deja_el_export():
    from laplace_backend.main import _segmento_next

    pedido = "trazas/__next.trazas.__PAGE__.txt"
    assert _segmento_next(pedido) == "trazas/__next.trazas/__PAGE__.txt"
    assert _segmento_next("__next.__PAGE__.txt") == "__next.__PAGE__.txt"
    assert _segmento_next("trazas/index.txt") == "trazas/index.txt"
    assert _segmento_next("_next/static/x.js") == "_next/static/x.js"


def test_un_prompt_desmesurado_se_rechaza(cerrado):
    client, claves = cerrado
    r = client.post(
        "/api/prompts",
        json={"project_id": "mio", "name": "enorme", "text": "x" * 200_001},
        headers=_cab(claves, "mio"),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------------


def test_una_invitación_se_gasta_una_sola_vez(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        r = admin.post(
            "/api/org/invitations",
            json={"org_id": org, "email": "una@ejemplo.com", "role": "lector"},
            headers=H,
        )
        token = r.json()["link"].split("token=")[1]
    cuentas = app.state.cuentas
    assert cuentas.aceptar(token) is True
    assert cuentas.aceptar(token) is False


def test_aceptar_una_invitación_no_baja_de_rol(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        _invitar_y_aceptar(app, admin, org, "jefa@ejemplo.com", "admin")
        r = admin.post(
            "/api/org/invitations",
            json={"org_id": org, "email": "jefa@ejemplo.com", "role": "lector"},
            headers=H,
        )
        token = r.json()["link"].split("token=")[1]
    with _cliente(app) as jefa:
        r = jefa.post("/api/auth/accept", json={"token": token, "password": CONTRASENA}, headers=H)
        assert r.status_code == 200, r.text
    usuario, _ = app.state.cuentas.usuario_por_email("jefa@ejemplo.com")
    assert app.state.cuentas.rol_en(org, usuario.id) == "admin"


def test_las_sesiones_caducadas_se_purgan(app):
    with _cliente(app) as admin:
        _configurar(admin)
    cuentas = app.state.cuentas
    usuario, _ = cuentas.usuario_por_email("ana@ejemplo.com")
    viejo = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    cuentas._ejecutar(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        ("caducada", usuario.id, viejo, viejo),
    )
    assert cuentas.purgar_caducadas() >= 1
    assert not cuentas._filas("SELECT 1 FROM sessions WHERE token_hash = 'caducada'")


# ---------------------------------------------------------------------------------
# El SDK no castiga al agente cuando Laplace no contesta
# ---------------------------------------------------------------------------------


@pytest.fixture
def prompts_sdk(monkeypatch):
    from laplace import prompts
    from laplace.config import LaplaceConfig

    prompts.clear_cache()
    monkeypatch.setattr(
        prompts, "get_config", lambda: LaplaceConfig(project="p", endpoint="http://laplace.test")
    )
    monkeypatch.setattr(
        prompts, "endpoint", lambda explicit=None: "http://laplace.test"
    )
    yield prompts
    prompts.clear_cache()


def test_tras_un_fallo_no_se_vuelve_a_preguntar_enseguida(prompts_sdk, monkeypatch):
    llamadas = []

    def caido(url, **kw):
        llamadas.append(url)
        raise prompts_sdk.LaplaceHTTPError("no contesta")

    monkeypatch.setattr(prompts_sdk, "request", caido)
    for _ in range(5):
        servido = prompts_sdk.get_prompt("sistema", fallback="texto del código")
        assert servido.source == "fallback"
    assert len(llamadas) == 1


def test_al_caducar_la_copia_pregunta_un_solo_hilo(prompts_sdk, monkeypatch):
    llamadas = []

    def lento(url, **kw):
        llamadas.append(url)
        time.sleep(0.3)
        return {"name": "sistema", "version": 3, "text": "hola", "prompt_id": "pr_1"}

    monkeypatch.setattr(prompts_sdk, "request", lento)
    resultados = []
    hilos = [
        threading.Thread(target=lambda: resultados.append(prompts_sdk.get_prompt("sistema")))
        for _ in range(6)
    ]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(llamadas) == 1
    assert {r.version for r in resultados} == {3}


# ---------------------------------------------------------------------------------
# Contra ClickHouse y Postgres de verdad, si están
# ---------------------------------------------------------------------------------


def test_la_retención_en_clickhouse_es_un_ttl_que_se_pone_y_se_quita():
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    try:
        store.apply_retention(400)
        assert "toIntervalDay(400)" in store._client.command("SHOW CREATE TABLE spans")
    finally:
        store.apply_retention(0)
    assert "TTL toDateTime" not in store._client.command("SHOW CREATE TABLE spans")


def test_el_turno_de_las_alertas_es_de_un_solo_proceso():
    from laplace_backend.config import Settings
    from laplace_backend.storage._pg import cerrar_todos, turno_exclusivo
    from laplace_backend.storage.postgres import PostgresMetadataStore

    settings = Settings()
    if not PostgresMetadataStore(settings).health():
        cerrar_todos()
        pytest.skip("no hay Postgres escuchando")
    try:
        with turno_exclusivo(settings.postgres_dsn, 4242) as primero:
            with turno_exclusivo(settings.postgres_dsn, 4242) as segundo:
                assert (primero, segundo) == (True, False)
        with turno_exclusivo(settings.postgres_dsn, 4242) as después:
            assert después is True
    finally:
        cerrar_todos()
