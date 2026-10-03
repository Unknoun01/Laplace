# Las fixtures `cerrado` y `app` se importan de sus ficheros y se piden por nombre, que
# es justo lo que F811 toma por una redefinición.
# ruff: noqa: F811
"""Los fallos críticos de la auditoría de septiembre de 2026, uno por uno (D-128).

Cada prueba es el ataque o el fallo tal como se reprodujo, y ahora tiene que no
funcionar. Van juntas porque comparten causa —costuras que ninguna prueba recorría: la
propiedad de lo que se pide por id, el almacén de la nube, el cliente HTTP del SDK, el
tamaño de lo que entra y a dónde sale lo que se manda— y porque si una vuelve a fallar
interesa ver las demás al lado.
"""

from __future__ import annotations

import gzip
import http.server
import inspect
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_auth import _cab, cerrado  # noqa: E402,F401
from test_cuentas import (  # noqa: E402,F401
    CONTRASENA,
    H,
    _cliente,
    _configurar,
    _invitar_y_aceptar,
    app,
)

# ---------------------------------------------------------------------------------
# P1-1: /api/alerts no enseña proyectos ajenos
# ---------------------------------------------------------------------------------


def test_alerts_sin_proyecto_sólo_lista_los_que_ve_la_clave(cerrado):
    client, claves = cerrado
    # El dueño de «ajeno» pone un canal: sin él, la ruta ni siquiera los listaría.
    r = client.put(
        "/api/alert-settings",
        json={"project_id": "ajeno", "generic_webhook_url": "https://example.com/h"},
        headers=_cab(claves, "*"),
    )
    assert r.status_code == 200, r.text

    vistos = [p["project_id"] for p in client.get(
        "/api/alerts", headers=_cab(claves, "mio")
    ).json()["projects"]]
    assert "ajeno" not in vistos

    # La de instalación sí los ve todos: el filtro es por identidad, no un recorte fijo.
    todos = [p["project_id"] for p in client.get(
        "/api/alerts", headers=_cab(claves, "*")
    ).json()["projects"]]
    assert "ajeno" in todos


# ---------------------------------------------------------------------------------
# P1-2: una clave de proyecto no gobierna el proyecto
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta", "kwargs"),
    [
        ("delete", "/api/projects", {"params": {"project_id": "mio", "confirm": "mio"}}),
        ("put", "/api/budget", {"json": {"project_id": "mio", "monthly_limit": 5}}),
        (
            "put",
            "/api/alert-settings",
            {"json": {"project_id": "mio", "generic_webhook_url": "https://atacante.example/x"}},
        ),
        ("post", "/api/alert-settings/test", {"params": {"project_id": "mio"}}),
        # Un token que escribe en el repositorio de alguien (D-185).
        (
            "put",
            "/api/github",
            {"json": {"project_id": "mio", "repo": "atacante/repo", "token": "ghp_x"}},
        ),
    ],
)
def test_la_clave_de_un_proyecto_no_hace_escrituras_de_admin(cerrado, metodo, ruta, kwargs):
    client, claves = cerrado
    r = getattr(client, metodo)(ruta, headers=_cab(claves, "mio"), **kwargs)
    assert r.status_code == 403, r.text
    # Y el proyecto sigue ahí.
    trazas = client.get("/api/traces", params={"project_id": "mio"}, headers=_cab(claves, "mio"))
    assert trazas.json()["traces"]


def test_la_clave_de_un_proyecto_sigue_escribiendo_lo_suyo(cerrado):
    """El freno es para lo de admin, no para escribir: anotar sigue funcionando."""
    client, claves = cerrado
    r = client.post(
        "/api/annotations",
        json={"project_id": "mio", "trace_id": "t-mio", "verdict": "pass"},
        headers=_cab(claves, "mio"),
    )
    assert r.status_code == 200, r.text


def test_la_clave_de_instalación_sí_puede_lo_de_admin(cerrado):
    client, claves = cerrado
    r = client.put(
        "/api/budget", json={"project_id": "mio", "monthly_limit": 5}, headers=_cab(claves, "*")
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------------
# P1-3: lo que se pide por id se comprueba contra la identidad
# ---------------------------------------------------------------------------------


def _conjunto(client, claves, proyecto: str) -> str:
    r = client.post(
        "/api/datasets",
        json={"project_id": proyecto, "name": f"de {proyecto}"},
        headers=_cab(claves, proyecto),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_un_conjunto_ajeno_por_id_no_existe(cerrado):
    client, claves = cerrado
    ajeno = _conjunto(client, claves, "ajeno")
    assert client.get(f"/api/datasets/{ajeno}", headers=_cab(claves, "mio")).status_code == 404
    assert client.get(f"/api/datasets/{ajeno}", headers=_cab(claves, "ajeno")).status_code == 200


def test_no_se_registra_una_tirada_sobre_un_conjunto_ajeno(cerrado):
    client, claves = cerrado
    ajeno = _conjunto(client, claves, "ajeno")
    r = client.post(
        "/api/runs",
        json={"project_id": "mio", "dataset_id": ajeno, "variant": "A"},
        headers=_cab(claves, "mio"),
    )
    assert r.status_code == 404


def test_no_se_comparan_tiradas_ajenas(cerrado):
    client, claves = cerrado
    ajeno = _conjunto(client, claves, "ajeno")
    ids = []
    for variante in ("A", "B"):
        r = client.post(
            "/api/runs",
            json={"project_id": "ajeno", "dataset_id": ajeno, "variant": variante},
            headers=_cab(claves, "ajeno"),
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    r = client.get(
        "/api/experiments/compare",
        params={"project_id": "mio", "a": ids[0], "b": ids[1]},
        headers=_cab(claves, "mio"),
    )
    assert r.status_code == 404


def test_no_se_anota_una_traza_de_otro_proyecto(cerrado):
    """Ni se crea, ni se pisa la que ya tenía su dueño."""
    client, claves = cerrado
    suya = client.post(
        "/api/annotations",
        json={"project_id": "ajeno", "trace_id": "t-ajeno", "verdict": "pass"},
        headers=_cab(claves, "ajeno"),
    )
    assert suya.status_code == 200, suya.text

    intruso = client.post(
        "/api/annotations",
        json={"project_id": "mio", "trace_id": "t-ajeno", "verdict": "fail"},
        headers=_cab(claves, "mio"),
    )
    assert intruso.status_code == 404

    traza = client.get("/api/traces/t-ajeno", headers=_cab(claves, "ajeno")).json()
    assert [a["verdict"] for a in traza["annotations"]] == ["pass"]


def test_las_anotaciones_por_id_de_traza_van_acotadas(cerrado):
    client, claves = cerrado
    client.post(
        "/api/annotations",
        json={"project_id": "ajeno", "trace_id": "t-ajeno", "verdict": "fail"},
        headers=_cab(claves, "ajeno"),
    )
    r = client.get(
        "/api/annotations", params={"trace_ids": "t-ajeno"}, headers=_cab(claves, "mio")
    )
    assert r.status_code == 200
    assert r.json()["annotations"] == {}


# ---------------------------------------------------------------------------------
# P1-4: los tres almacenes de metadatos aceptan lo mismo
# ---------------------------------------------------------------------------------


def _metodos_del_protocolo() -> list[str]:
    from laplace_backend.storage.metadata import MetadataStore as Protocolo

    return [
        nombre
        for nombre, valor in vars(Protocolo).items()
        if callable(valor) and not nombre.startswith("_")
    ]


@pytest.mark.parametrize("nombre", _metodos_del_protocolo())
def test_postgres_y_el_nulo_tienen_la_firma_del_protocolo(nombre):
    """El `TypeError` sólo-en-la-nube de `get_prompt(id, alcance)` no puede volver.

    Las rutas se prueban contra SQLite; Postgres no corre en estas pruebas. Esta es la red
    que faltaba: si una implementación no acepta lo que el protocolo promete, falla aquí
    y no en producción.
    """
    from laplace_backend.storage.metadata import MetadataStore as Protocolo
    from laplace_backend.storage.metadata import NullMetadataStore, SQLiteMetadataStore
    from laplace_backend.storage.postgres import PostgresMetadataStore

    esperada = list(inspect.signature(getattr(Protocolo, nombre)).parameters)
    for clase in (SQLiteMetadataStore, PostgresMetadataStore, NullMetadataStore):
        metodo = getattr(clase, nombre, None)
        assert metodo is not None, f"{clase.__name__} no tiene {nombre}"
        assert list(inspect.signature(metodo).parameters) == esperada, (
            f"{clase.__name__}.{nombre}{inspect.signature(metodo)} no es "
            f"{nombre}{inspect.signature(getattr(Protocolo, nombre))}"
        )


# ---------------------------------------------------------------------------------
# P1-5: el SDK manda la clave a la API
# ---------------------------------------------------------------------------------


def test_el_cliente_http_del_sdk_manda_la_clave(monkeypatch):
    from laplace import _http
    from laplace.config import LaplaceConfig

    monkeypatch.setattr(_http, "get_config", lambda: LaplaceConfig(api_key="lp_secreta"))
    vistas = {}

    class _Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def falso(peticion, timeout):
        vistas.update({k.lower(): v for k, v in peticion.header_items()})
        vistas["timeout"] = timeout
        return _Respuesta()

    monkeypatch.setattr(_http.urllib.request, "urlopen", falso)
    _http.request("http://laplace.test/api/prompts/resolve?x=1", timeout=5.0)
    assert vistas["authorization"] == "Bearer lp_secreta"
    assert vistas["timeout"] == 5.0


# ---------------------------------------------------------------------------------
# P1-6: tope al tamaño de lo que entra
# ---------------------------------------------------------------------------------


@pytest.fixture
def pequeño(cerrado, monkeypatch):
    """La misma instalación con un tope de 1 MiB, para no mover gigas en una prueba."""
    import importlib

    from fastapi.testclient import TestClient

    from laplace_backend import config, main

    client, claves = cerrado
    monkeypatch.setenv("LAPLACE_MAX_BODY_MB", "1")
    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as nuevo:
        yield nuevo, claves


def test_un_cuerpo_que_pasa_del_tope_es_un_413(pequeño):
    client, claves = pequeño
    r = client.post(
        "/v1/traces",
        content=b"\0" * (2 * 1024 * 1024),
        headers={**_cab(claves, "mio"), "content-type": "application/x-protobuf"},
    )
    assert r.status_code == 413


def test_una_bomba_gzip_no_se_descomprime_entera(pequeño):
    """Unos pocos KiB por el cable que serían 64 MiB en memoria: se corta en el tope."""
    client, claves = pequeño
    bomba = gzip.compress(b"\0" * (64 * 1024 * 1024))
    assert len(bomba) < 1024 * 1024
    r = client.post(
        "/v1/traces",
        content=bomba,
        headers={
            **_cab(claves, "mio"),
            "content-type": "application/x-protobuf",
            "content-encoding": "gzip",
        },
    )
    assert r.status_code == 413


def test_un_lote_normal_comprimido_sigue_entrando(pequeño):
    from helpers import otlp_body

    client, claves = pequeño
    r = client.post(
        "/v1/traces",
        # Los spans de prueba van al proyecto `test-project`: con la clave de instalación.
        content=gzip.compress(otlp_body()),
        headers={
            **_cab(claves, "*"),
            "content-type": "application/x-protobuf",
            "content-encoding": "gzip",
        },
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------------
# P1-7: los webhooks no llegan a la red interna
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.5/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "http://127.0.0.1:8000/api/projects",
        "http://example.com/hook",
        "ftp://example.com/hook",
    ],
)
def test_en_la_nube_no_se_guarda_un_webhook_interno(url):
    from laplace_backend.alerts import webhook_valido

    assert not webhook_valido(url)


def test_en_local_el_webhook_de_la_propia_máquina_vale():
    from laplace_backend.alerts import webhook_valido

    assert webhook_valido("http://127.0.0.1:5678/webhook", permitir_local=True)
    assert webhook_valido("http://localhost:5678/webhook", permitir_local=True)
    # Pero la red interna, ni en local.
    assert not webhook_valido("https://10.0.0.5/hook", permitir_local=True)


def test_un_nombre_público_que_resuelve_a_una_ip_interna_no_se_usa(monkeypatch):
    from laplace_backend import alerts

    monkeypatch.setattr(
        alerts.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))],
    )
    assert alerts.webhook_valido("https://parece-publico.example/hook")
    assert "no pública" in alerts.resolver_destino("https://parece-publico.example/hook")[0]


def test_la_api_rechaza_guardar_un_webhook_interno(cerrado):
    client, claves = cerrado
    r = client.put(
        "/api/alert-settings",
        json={"project_id": "mio", "generic_webhook_url": "https://10.0.0.5/hook"},
        headers=_cab(claves, "*"),
    )
    assert r.status_code == 400


def test_el_notificador_no_sigue_redirecciones():
    """Un webhook público que redirige a la red interna no llega a ella."""
    from laplace_backend.alerts import _SIN_REDIRECCIONES

    class Redirige(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            # Leer el cuerpo antes de contestar. Si no, el socket se cierra con datos sin
            # leer, Windows manda un RST y el cliente ve «conexión anulada» en vez del
            # 302: fallaba una de cada catorce veces, y era la prueba inestable que la
            # hoja de ruta tenía sin identificar (D-140).
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()

        def log_message(self, *a):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Redirige)
    hilo = threading.Thread(target=servidor.handle_request, daemon=True)
    hilo.start()
    try:
        peticion = urllib.request.Request(
            f"http://127.0.0.1:{servidor.server_port}/h", data=b"{}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            _SIN_REDIRECCIONES.open(peticion, timeout=5)
        assert error.value.code == 302
    finally:
        servidor.server_close()


# ---------------------------------------------------------------------------------
# P1-8: la IP que cuenta no la pone el cliente
# ---------------------------------------------------------------------------------


def test_cambiar_x_forwarded_for_no_salta_el_freno_por_ip(app):
    with _cliente(app) as c:
        _configurar(c)
    with _cliente(app) as atacante:
        for i in range(5):
            r = atacante.post(
                "/api/auth/login",
                json={"email": f"victima{i}@ejemplo.com", "password": "no-es-esta-no"},
                headers={**H, "X-Forwarded-For": f"203.0.113.{i}"},
            )
            assert r.status_code == 401
        r = atacante.post(
            "/api/auth/login",
            json={"email": "otra@ejemplo.com", "password": "no-es-esta-no"},
            headers={**H, "X-Forwarded-For": "203.0.113.99"},
        )
        assert r.status_code == 429


def test_proxy_de_confianza_por_ip_rango_y_nombre():
    from laplace_backend.api_cuentas import es_proxy_de_confianza

    assert es_proxy_de_confianza("10.0.0.7", frozenset({"10.0.0.7"}))
    assert es_proxy_de_confianza("172.18.0.3", frozenset({"172.16.0.0/12"}))
    assert es_proxy_de_confianza("127.0.0.1", frozenset({"localhost"}))
    assert not es_proxy_de_confianza("203.0.113.5", frozenset({"10.0.0.0/8", "localhost"}))
    assert not es_proxy_de_confianza("testclient", frozenset({"10.0.0.0/8"}))


def test_aceptar_una_invitación_tiene_el_mismo_freno_que_entrar(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        _invitar_y_aceptar(app, admin, org, "bea@ejemplo.com", "lector")
        # Otra invitación al mismo email: la cuenta existe, así que se pide su contraseña.
        r = admin.post(
            "/api/org/invitations",
            json={"org_id": org, "email": "bea@ejemplo.com", "role": "miembro"},
            headers=H,
        )
        token = r.json()["link"].split("token=")[1]
    with _cliente(app) as intruso:
        for _ in range(5):
            r = intruso.post(
                "/api/auth/accept", json={"token": token, "password": "adivinando-1"}, headers=H
            )
            assert r.status_code == 403
        r = intruso.post(
            "/api/auth/accept", json={"token": token, "password": CONTRASENA}, headers=H
        )
        assert r.status_code == 429


# ---------------------------------------------------------------------------------
# P1-9: un Postgres que tarda en arrancar no deja la instalación muerta
# ---------------------------------------------------------------------------------


def test_con_postgres_caído_al_arrancar_no_se_instala_el_almacén_nulo():
    from laplace_backend.config import Settings
    from laplace_backend.storage.metadata import MetadataUnavailable, NullMetadataStore
    from laplace_backend.storage.postgres import PostgresMetadataStore, build_metadata_store

    settings = Settings(
        store="clickhouse",
        postgres_dsn="postgresql://nadie:nada@127.0.0.1:1/nada?connect_timeout=1",
    )
    almacen = build_metadata_store(settings)
    assert isinstance(almacen, PostgresMetadataStore)
    assert not isinstance(almacen, NullMetadataStore)
    # Mientras no está, lo dice como «no hay dónde», que las rutas convierten en 503.
    try:
        with pytest.raises(MetadataUnavailable):
            almacen.api_key_by_hash("x")
    finally:
        # El pool de ese DSN seguiría reintentando contra un puerto cerrado.
        from laplace_backend.storage._pg import cerrar_todos

        cerrar_todos()


def test_la_preparación_de_metadatos_se_reintenta_hasta_que_sale():
    from types import SimpleNamespace

    from laplace_backend.config import Settings
    from laplace_backend.main import PreparacionMetadatos

    class Intermitente:
        def __init__(self):
            self.fallos = 2
            self.migrado = False

        def migrate(self):
            if self.fallos:
                self.fallos -= 1
                raise RuntimeError("todavía no")
            self.migrado = True

        def get_setting(self, *_):
            return {}

    meta = Intermitente()
    app_falsa = SimpleNamespace(state=SimpleNamespace(metadata=meta))
    settings = Settings(store="sqlite", auth_required="false")
    preparacion = PreparacionMetadatos(app_falsa, settings)
    assert preparacion.intentar() is False
    assert preparacion.intentar() is False
    assert preparacion.intentar() is True
    assert meta.migrado
