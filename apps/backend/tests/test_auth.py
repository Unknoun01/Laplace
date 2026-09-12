"""Autenticación: lo que se comprueba aquí es lo que **no** debe poder hacerse.

Un test que confirma que una clave buena funciona no protege de nada; lo que protege es
intentar activamente lo que no debe pasar. Así que casi todo este fichero son intentos:
ingerir con la clave de otro proyecto, leer trazas ajenas, abrir una traza por su id sin
decir el proyecto, listar los proyectos de la instalación con una clave de uno solo,
llegar sin credencial, usar una clave revocada.

Y uno que no es un intento sino una red: `test_ninguna_ruta_nueva_nace_abierta` recorre
las rutas registradas y exige que todas exijan credencial salvo una lista blanca corta y
escrita. Es lo que hace que esto siga siendo verdad dentro de seis meses, cuando alguien
añada un endpoint y no se acuerde de nada de esto.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from helpers import otlp_body
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import auth
from laplace_backend.config import Settings
from laplace_backend.storage.metadata import SQLiteMetadataStore, new_id
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _span(project: str, trace: str) -> Span:
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name="chat gpt-5.6-luna",
        type="llm",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=200),
        duration_ms=200.0,
        step_key="k",
        step_label="responder",
        step_hint="Eres un asistente.",
    )
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        cost=Cost(total_usd=0.001),
    )
    return span


@pytest.fixture
def cerrado(tmp_path, monkeypatch):
    """Una instalación **con** autenticación, con dos proyectos y tres claves.

    Se usa SQLite para no necesitar ni ClickHouse ni Postgres: lo que se prueba aquí es
    la autenticación, que es el mismo código en los dos modos. El modo local no pide
    clave por defecto, así que se fuerza con `LAPLACE_AUTH_REQUIRED=true`, que es
    exactamente lo que haría alguien que quisiera exponer el modo local.
    """
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    store.insert_spans([_span("mio", "t-mio"), _span("ajeno", "t-ajeno")])

    meta = SQLiteMetadataStore(db)
    meta.migrate()
    claves = {}
    for proyecto in ("mio", "ajeno", auth.ALL_PROJECTS):
        clave = auth.generate_key()
        key_id = new_id("ak")
        meta.create_api_key(key_id, proyecto, auth.hash_key(clave), f"clave de {proyecto}")
        claves[proyecto] = {"clave": clave, "id": key_id}

    # Una revocada, para comprobar que deja de servir.
    revocada = auth.generate_key()
    revocada_id = new_id("ak")
    meta.create_api_key(revocada_id, "mio", auth.hash_key(revocada), "la que se filtró")
    meta.revoke_api_key(revocada_id)
    claves["revocada"] = {"clave": revocada, "id": revocada_id}

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "true")

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, claves
    config.get_settings.cache_clear()


def _cab(claves: dict, cual: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {claves[cual]['clave']}"}


# ---------------------------------------------------------------------------------
# Sin credencial no se pasa
# ---------------------------------------------------------------------------------


def test_sin_credencial_no_se_lee_nada(cerrado):
    client, _ = cerrado
    for ruta, params in (
        ("/api/projects", {}),
        ("/api/traces", {"project_id": "mio"}),
        ("/api/overview", {"project_id": "mio"}),
        ("/api/panel", {"project_id": "mio"}),
        ("/api/prompts", {"project_id": "mio"}),
        ("/api/datasets", {"project_id": "mio"}),
    ):
        respuesta = client.get(ruta, params=params)
        assert respuesta.status_code == 401, ruta
        assert "clave de API" in respuesta.json()["detail"]
        # Y se dice cómo, que es lo que distingue un 401 útil de uno mudo.
        assert respuesta.headers.get("WWW-Authenticate") == "Bearer"


def test_sin_credencial_no_se_escribe_nada(cerrado):
    client, _ = cerrado
    respuesta = client.post(
        "/api/annotations",
        json={"project_id": "mio", "trace_id": "t-mio", "verdict": "pass"},
    )
    assert respuesta.status_code == 401


def test_sin_credencial_no_se_ingiere(cerrado):
    client, _ = cerrado
    respuesta = client.post(
        "/v1/traces",
        content=b"",
        headers={"content-type": "application/x-protobuf"},
    )
    assert respuesta.status_code == 401


def test_una_clave_inventada_no_vale(cerrado):
    client, _ = cerrado
    respuesta = client.get(
        "/api/projects", headers={"Authorization": "Bearer lp_" + "0" * 64}
    )
    assert respuesta.status_code == 401
    assert "inválida" in respuesta.json()["detail"]


def test_una_clave_revocada_deja_de_servir(cerrado):
    client, claves = cerrado
    respuesta = client.get("/api/projects", headers=_cab(claves, "revocada"))
    assert respuesta.status_code == 401
    assert "revocada" in respuesta.json()["detail"]


def test_la_salud_se_puede_consultar_sin_clave(cerrado):
    """El orquestador la llama antes de que nadie tenga clave, y no dice nada de nadie."""
    client, _ = cerrado
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------------
# Con credencial, pero de otro proyecto
# ---------------------------------------------------------------------------------


def test_no_se_leen_las_trazas_de_un_proyecto_ajeno(cerrado):
    client, claves = cerrado
    respuesta = client.get(
        "/api/traces", params={"project_id": "ajeno"}, headers=_cab(claves, "mio")
    )
    assert respuesta.status_code == 403
    # Y el mensaje no confirma si ese proyecto existe: contestar distinto para uno que
    # existe y otro que no convierte la API en un directorio de los demás.
    assert respuesta.json()["detail"] == "esta clave no tiene acceso a ese proyecto"
    inventado = client.get(
        "/api/traces", params={"project_id": "no-existe"}, headers=_cab(claves, "mio")
    )
    assert inventado.json()["detail"] == respuesta.json()["detail"]


def test_no_se_abre_una_traza_ajena_por_su_id(cerrado):
    """El agujero más fácil de dejar: `project_id` es opcional al abrir una traza, así
    que sin acotar la consulta busca ese id en toda la instalación."""
    client, claves = cerrado
    respuesta = client.get("/api/traces/t-ajeno", headers=_cab(claves, "mio"))
    assert respuesta.status_code == 404, "la traza de otro no existe para esta clave"

    # Con la clave de su dueño sí se abre: el 404 de arriba es alcance, no un fallo.
    propia = client.get("/api/traces/t-ajeno", headers=_cab(claves, "ajeno"))
    assert propia.status_code == 200


def test_la_lista_de_proyectos_no_es_un_directorio_de_clientes(cerrado):
    client, claves = cerrado
    mios = client.get("/api/projects", headers=_cab(claves, "mio")).json()["projects"]
    assert [p["id"] for p in mios] == ["mio"]

    # La clave de instalación sí los ve todos: es para lo que existe.
    todos = client.get("/api/projects", headers=_cab(claves, "*")).json()["projects"]
    assert {p["id"] for p in todos} == {"mio", "ajeno"}


def test_la_lista_de_trazas_sin_proyecto_se_acota_a_la_clave(cerrado):
    """Sin `project_id` en la petición, una clave de un proyecto no puede acabar
    leyendo la lista entera de la instalación."""
    client, claves = cerrado
    trazas = client.get("/api/traces", headers=_cab(claves, "mio")).json()["traces"]
    assert {t["project_id"] for t in trazas} == {"mio"}


def test_no_se_escribe_en_un_proyecto_ajeno(cerrado):
    """El `project_id` va en el cuerpo, no en la URL: es el caso que se olvida."""
    client, claves = cerrado
    respuesta = client.post(
        "/api/annotations",
        json={"project_id": "ajeno", "trace_id": "t-ajeno", "verdict": "fail"},
        headers=_cab(claves, "mio"),
    )
    assert respuesta.status_code == 403


def test_no_se_crea_un_prompt_en_un_proyecto_ajeno(cerrado):
    client, claves = cerrado
    respuesta = client.post(
        "/api/prompts",
        json={"project_id": "ajeno", "name": "colado", "text": "hola"},
        headers=_cab(claves, "mio"),
    )
    assert respuesta.status_code == 403


def test_el_cuerpo_llega_entero_al_endpoint_despues_de_comprobarlo(cerrado):
    """El middleware lee el cuerpo para comprobar el proyecto. Si no lo devolviera bien,
    todas las escrituras se quedarían esperando un cuerpo ya consumido."""
    client, claves = cerrado
    respuesta = client.post(
        "/api/annotations",
        json={"project_id": "mio", "trace_id": "t-mio", "verdict": "pass",
              "comment": "el cuerpo tiene que llegar entero"},
        headers=_cab(claves, "mio"),
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["comment"] == "el cuerpo tiene que llegar entero"


# ---------------------------------------------------------------------------------
# La ingesta ata el proyecto
# ---------------------------------------------------------------------------------


def test_no_se_ingiere_en_el_proyecto_de_otro(cerrado, monkeypatch):
    """Los spans traen su proyecto dentro del protobuf. Si la clave es de otro, se
    rechaza el lote entero en vez de reetiquetarlo: reetiquetar escondería una
    configuración mal puesta durante semanas."""
    from helpers import exporter
    from laplace import decorators

    exporter.clear()
    with decorators.span("paso", type="agent"):
        pass
    cuerpo = otlp_body()  # el proveedor de las pruebas emite en el proyecto `test-project`

    respuesta = client_post(cerrado, cuerpo, "mio")
    assert respuesta.status_code == 403
    assert "test-project" in respuesta.json()["detail"]
    assert "laplace.init()" in respuesta.json()["detail"]


def test_la_clave_de_instalacion_puede_ingerir_de_cualquiera(cerrado):
    from helpers import exporter
    from laplace import decorators

    exporter.clear()
    with decorators.span("paso", type="agent"):
        pass
    respuesta = client_post(cerrado, otlp_body(), "*")
    assert respuesta.status_code == 200


def client_post(cerrado, cuerpo: bytes, cual: str):
    client, claves = cerrado
    return client.post(
        "/v1/traces",
        content=cuerpo,
        headers={
            "content-type": "application/x-protobuf",
            **_cab(claves, cual),
        },
    )


# ---------------------------------------------------------------------------------
# La red que sobrevive a los próximos seis meses
# ---------------------------------------------------------------------------------


def test_ninguna_ruta_nueva_nace_abierta(cerrado):
    """Recorre las rutas registradas y exige credencial en todas menos en la lista
    blanca. Si alguien añade un endpoint dentro de seis meses, este test le dice que
    está abierto **antes** de que lo esté en producción.
    """
    client, _ = cerrado
    from laplace_backend import main

    abiertas = []
    for ruta in main.app.routes:
        camino = getattr(ruta, "path", "")
        if not camino.startswith(("/api", "/v1/traces")):
            continue
        if camino in auth.PUBLIC_PATHS:
            continue
        # Se pide con un método que la ruta acepte y sin credencial ninguna.
        metodos = getattr(ruta, "methods", {"GET"}) - {"HEAD", "OPTIONS"}
        metodo = "GET" if "GET" in metodos else sorted(metodos)[0]
        # Los parámetros de camino se rellenan con algo cualquiera: lo que se comprueba
        # es que ni siquiera se llega a mirar qué son.
        concreto = camino.replace("{trace_id}", "x").replace("{finding_id:path}", "x")
        concreto = concreto.replace("{prompt_id}", "x").replace("{dataset_id}", "x")
        concreto = concreto.replace("{annotation_id}", "x").replace("{run_id}", "x")
        respuesta = client.request(metodo, concreto, json={} if metodo != "GET" else None)
        if respuesta.status_code != 401:
            abiertas.append(f"{metodo} {camino} -> {respuesta.status_code}")

    assert not abiertas, (
        "estas rutas no piden credencial y no están en la lista blanca: " + "; ".join(abiertas)
    )


def test_el_preflight_de_cors_pasa_pero_no_trae_datos(cerrado):
    """El navegador manda el `OPTIONS` **sin** credencial a propósito. Si se le pidiera,
    toda petición desde otro origen moriría antes de mandarse. No abre nada: un preflight
    no devuelve datos, sólo qué métodos se permiten."""
    client, _ = cerrado
    respuesta = client.options(
        "/api/projects",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert respuesta.status_code < 400
    assert "projects" not in respuesta.text


def test_la_lista_blanca_es_corta_y_explicita():
    """Si esta lista crece, que sea con un diff que alguien tenga que aprobar."""
    assert auth.PUBLIC_PATHS == frozenset({"/health"})


# ---------------------------------------------------------------------------------
# El modo local: exención escrita, no descuido
# ---------------------------------------------------------------------------------


def test_en_local_no_se_pide_clave_a_proposito():
    """La diferencia entre una exención escrita y un descuido es que la primera se
    puede leer, apagar y probar. Ésta es la prueba."""
    assert Settings(store="sqlite").auth_enforced is False
    assert Settings(store="clickhouse").auth_enforced is True
    # Y se puede cerrar el modo local sin tocar código, que es lo que hay que hacer
    # antes de exponerlo fuera de una máquina.
    assert Settings(store="sqlite", auth_required="true").auth_enforced is True
    # Abrirlo en la nube se puede, pero es una decisión explícita (y el log grita).
    assert Settings(store="clickhouse", auth_required="false").auth_enforced is False


def test_si_no_se_puede_verificar_la_clave_se_deniega():
    """La autenticación no se degrada como el resto del producto. Sin base de claves no
    hay forma de saber si una clave es buena, y «no lo sé» se lee como «no»."""
    from laplace_backend.storage.metadata import NullMetadataStore

    with pytest.raises(auth.AuthError) as excinfo:
        auth.resolve(NullMetadataStore(), auth.generate_key())
    assert excinfo.value.status == 503


# ---------------------------------------------------------------------------------
# Las claves en sí
# ---------------------------------------------------------------------------------


def test_la_clave_en_claro_no_se_guarda_nunca(tmp_path):
    import sqlite3

    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    clave = auth.generate_key()
    meta.create_api_key("ak_1", "p", auth.hash_key(clave), "la de prod")

    with sqlite3.connect(tmp_path / "m.db") as conn:
        crudo = str(conn.execute("SELECT * FROM api_keys").fetchall())
    assert clave not in crudo
    assert auth.hash_key(clave) in crudo


def test_dos_claves_nunca_coinciden():
    assert len({auth.generate_key() for _ in range(200)}) == 200
    assert auth.generate_key().startswith("lp_")


def test_la_clave_se_puede_mandar_en_las_dos_cabeceras(cerrado):
    """`Authorization: Bearer` es lo estándar; `x-api-key` lo mandan varias herramientas
    de terceros. Lo que no se acepta es la clave en la URL, que acaba en los logs de
    cualquier proxy por el que pase."""
    client, claves = cerrado
    directa = client.get("/api/projects", headers={"x-api-key": claves["mio"]["clave"]})
    assert directa.status_code == 200

    en_la_url = client.get("/api/projects", params={"api_key": claves["mio"]["clave"]})
    assert en_la_url.status_code == 401
