"""Cuentas de usuario: casi todo son intentos de hacer lo que no se debe poder (D-127).

Entrar sin contraseña, configurar la instalación sin el código, escribir con la cookie
desde otro sitio, que un lector escriba, que alguien de una organización lea los datos de
otra, que una clave revocada o caducada siga sirviendo, dejar una organización sin
propietario. Y lo que sí tiene que funcionar, para que las negativas signifiquen algo.

Corre con la instalación «de nube» montada sobre SQLite: mismas rutas, mismo middleware,
misma autenticación obligatoria. Sin red ni Postgres.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_sqlite_store import _agente  # noqa: E402

CODIGO = "codigo-de-prueba-123"
CONTRASENA = "una-contraseña-larga"
H = {"X-Laplace": "1"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LAPLACE_SETUP_TOKEN", CODIGO)
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as _:
        pass  # arranca una vez para migrar y sembrar
    # Un proyecto con datos de antes de configurar: la primera cuenta lo adopta.
    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    spans = _agente("previo")
    for s in spans:
        s.start_time = ahora
        s.end_time = ahora + timedelta(milliseconds=200)
    main.app.state.store.insert_spans(spans)
    yield main.app
    config.get_settings.cache_clear()


def _cliente(app) -> TestClient:
    return TestClient(app)


def _configurar(c: TestClient) -> dict:
    r = c.post(
        "/api/auth/setup",
        json={"token": CODIGO, "email": "ana@ejemplo.com", "password": CONTRASENA,
              "name": "Ana", "org_name": "Viajes"},
        headers=H,
    )
    assert r.status_code == 200, r.text
    return c.get("/api/auth/me").json()


def _invitar_y_aceptar(app, admin: TestClient, org_id: str, email: str, rol: str) -> TestClient:
    r = admin.post("/api/org/invitations", json={"org_id": org_id, "email": email, "role": rol},
                   headers=H)
    assert r.status_code == 200, r.text
    token = r.json()["link"].split("token=")[1]
    nuevo = _cliente(app)
    r = nuevo.post("/api/auth/accept", json={"token": token, "password": CONTRASENA}, headers=H)
    assert r.status_code == 200, r.text
    return nuevo


# ---------------------------------------------------------------------------------
# Configurar y entrar
# ---------------------------------------------------------------------------------


def test_sin_cuenta_no_se_ve_nada_y_la_pantalla_sabe_que_falta_configurar(app):
    with _cliente(app) as c:
        assert c.get("/api/projects").status_code == 401
        me = c.get("/api/auth/me").json()
        assert me == {"mode": "nube", "user": None, "needs_setup": True, "by_key": False}


def test_la_primera_cuenta_pide_el_codigo_y_sólo_se_crea_una_vez(app):
    with _cliente(app) as c:
        malo = c.post("/api/auth/setup", json={"token": "otro", "email": "x@y.z",
                                               "password": CONTRASENA}, headers=H)
        assert malo.status_code == 403
        me = _configurar(c)
        assert me["user"]["is_admin"] is True
        # El proyecto que ya existía es de la organización nueva: se ve.
        assert [p["id"] for p in c.get("/api/projects").json()["projects"]] == ["previo"]
    with _cliente(app) as otro:
        r = otro.post("/api/auth/setup", json={"token": CODIGO, "email": "z@y.z",
                                               "password": CONTRASENA}, headers=H)
        assert r.status_code == 409, "una instalación configurada no se reconfigura"


def test_entrar_con_contraseña_mala_no_dice_si_el_email_existe_y_frena(app):
    with _cliente(app) as c:
        _configurar(c)
    with _cliente(app) as c:
        existe = c.post("/api/auth/login", json={"email": "ana@ejemplo.com",
                                                 "password": "mala-mala-mala"}, headers=H)
        no_existe = c.post("/api/auth/login", json={"email": "nadie@ejemplo.com",
                                                    "password": "mala-mala-mala"}, headers=H)
        assert existe.status_code == no_existe.status_code == 401
        assert existe.json() == no_existe.json()
        for _ in range(5):
            c.post("/api/auth/login", json={"email": "ana@ejemplo.com", "password": "x" * 12},
                   headers=H)
        frenado = c.post("/api/auth/login", json={"email": "ana@ejemplo.com",
                                                  "password": CONTRASENA}, headers=H)
        assert frenado.status_code == 429, "tras cinco fallos, ni la buena entra un rato"


def test_una_escritura_con_cookie_sin_la_cabecera_propia_no_pasa(app):
    """Es lo que haría un formulario escondido en otro sitio: la cookie viaja sola."""
    with _cliente(app) as c:
        _configurar(c)
        sin = c.put("/api/budget", json={"project_id": "previo", "monthly_limit": 5})
        assert sin.status_code == 403
        con = c.put("/api/budget", json={"project_id": "previo", "monthly_limit": 5}, headers=H)
        assert con.status_code == 200, con.text
        sin_login = _cliente(app).post(
            "/api/auth/login", json={"email": "ana@ejemplo.com", "password": CONTRASENA}
        )
        assert sin_login.status_code == 403, "entrar también pide la cabecera"


# ---------------------------------------------------------------------------------
# Roles y organizaciones
# ---------------------------------------------------------------------------------


def test_cada_rol_puede_lo_suyo_y_nada_más(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        lector = _invitar_y_aceptar(app, admin, org, "lee@ejemplo.com", "lector")
        miembro = _invitar_y_aceptar(app, admin, org, "hace@ejemplo.com", "miembro")

        # Leer, todos.
        for c in (lector, miembro):
            assert c.get("/api/overview", params={"project_id": "previo"}).status_code == 200
        anotar = {"project_id": "previo", "trace_id": "t1", "verdict": "pass"}
        assert lector.post("/api/annotations", json=anotar, headers=H).status_code == 403
        assert miembro.post("/api/annotations", json=anotar, headers=H).status_code == 200
        # Presupuesto y alertas, sólo admin.
        tope = {"project_id": "previo", "monthly_limit": 1}
        assert miembro.put("/api/budget", json=tope, headers=H).status_code == 403
        assert admin.put("/api/budget", json=tope, headers=H).status_code == 200
        # Invitar, sólo admin.
        r = miembro.post("/api/org/invitations",
                         json={"org_id": org, "email": "x@y.z", "role": "admin"}, headers=H)
        assert r.status_code == 403


def test_alguien_de_otra_organizacion_no_ve_nada_de_esta(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        # Otra organización: la crea el admin de la instalación y le da un proyecto.
        cuentas = app.state.cuentas
        otra = cuentas.crear_org("Otra")
        extraño = cuentas.crear_usuario("fuera@otra.com", "", CONTRASENA)
        cuentas.poner_miembro(otra, extraño.id, "propietario")
        cuentas.asignar_proyecto("suyo", otra)

    with _cliente(app) as c:
        assert c.post("/api/auth/login", json={"email": "fuera@otra.com", "password": CONTRASENA},
                      headers=H).status_code == 200
        assert c.get("/api/overview", params={"project_id": "previo"}).status_code == 403
        assert "previo" not in [p["id"] for p in c.get("/api/projects").json()["projects"]]
        assert c.get("/api/org", params={"org_id": org}).status_code == 404
        # Ni crear una clave con el nombre de un proyecto ajeno.
        r = c.post("/api/org/keys", json={"org_id": otra, "project_id": "previo"}, headers=H)
        assert r.status_code == 409


def test_la_organización_no_se_queda_sin_propietario(app):
    with _cliente(app) as admin:
        me = _configurar(admin)
        org, yo = me["orgs"][0]["id"], me["user"]["id"]
        r = admin.put("/api/org/members",
                      json={"org_id": org, "user_id": yo, "role": "admin"}, headers=H)
        assert r.status_code == 409
        r = admin.delete("/api/org/members", params={"org_id": org, "user_id": yo}, headers=H)
        assert r.status_code == 409


def test_quitar_a_alguien_le_cierra_la_sesion_ya_abierta(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        miembro = _invitar_y_aceptar(app, admin, org, "sale@ejemplo.com", "miembro")
        assert miembro.get("/api/projects").status_code == 200
        uid = next(m["user_id"] for m in admin.get("/api/org", params={"org_id": org}).json()
                   ["members"] if m["email"] == "sale@ejemplo.com")
        assert admin.delete("/api/org/members", params={"org_id": org, "user_id": uid},
                            headers=H).status_code == 200
        assert miembro.get("/api/projects").status_code == 401


def test_cambiar_la_contraseña_cierra_las_demas_sesiones(app):
    with _cliente(app) as a:
        _configurar(a)
    with _cliente(app) as b:
        entrar = {"email": "ana@ejemplo.com", "password": CONTRASENA}
        assert b.post("/api/auth/login", json=entrar, headers=H).status_code == 200
        with _cliente(app) as a2:
            a2.post("/api/auth/login", json={"email": "ana@ejemplo.com", "password": CONTRASENA},
                    headers=H)
            r = a2.post("/api/auth/password",
                        json={"current": CONTRASENA, "new": "otra-contraseña-larga"}, headers=H)
            assert r.status_code == 200, r.text
            assert a2.get("/api/projects").status_code == 200, "la de quien la cambia sigue"
        assert b.get("/api/projects").status_code == 401, "la otra se cierra"


# ---------------------------------------------------------------------------------
# Claves desde la interfaz
# ---------------------------------------------------------------------------------


def test_el_admin_de_una_organizacion_crea_el_primer_proyecto_de_la_suya(app):
    """No sólo el de la instalación: el proyecto nuevo todavía no es de nadie, y aun así
    su admin tiene que poder darle una clave. Y alguien de otra organización, no."""
    with _cliente(app) as admin:
        _configurar(admin)
    cuentas = app.state.cuentas
    org = cuentas.crear_org("Pequeña")
    jefa = cuentas.crear_usuario("jefa@peq.com", "", CONTRASENA)
    cuentas.poner_miembro(org, jefa.id, "admin")
    with _cliente(app) as c:
        c.post("/api/auth/login", json={"email": "jefa@peq.com", "password": CONTRASENA},
               headers=H)
        r = c.post("/api/org/keys", json={"org_id": org, "project_id": "suyo-nuevo"}, headers=H)
        assert r.status_code == 200, r.text
        assert "suyo-nuevo" in c.get("/api/org", params={"org_id": org}).json()["projects"]
        otra = cuentas.crear_org("Ajena")
        r = c.post("/api/org/keys", json={"org_id": otra, "project_id": "x"}, headers=H)
        assert r.status_code == 404, "en una organización que no es la tuya, ni existe"


def test_una_clave_creada_en_la_interfaz_sirve_para_su_proyecto_y_se_puede_revocar(app):
    with _cliente(app) as admin:
        org = _configurar(admin)["orgs"][0]["id"]
        r = admin.post("/api/org/keys", json={"org_id": org, "project_id": "nuevo",
                                              "name": "ingesta"}, headers=H)
        assert r.status_code == 200, r.text
        clave, key_id = r.json()["key"], r.json()["id"]
        # El proyecto nuevo ya es de la organización y la clave aparece, sin su valor.
        vista = admin.get("/api/org", params={"org_id": org}).json()
        assert "nuevo" in vista["projects"]
        assert clave not in str(vista)

    agente = TestClient(app)
    cab = {"Authorization": f"Bearer {clave}"}
    assert agente.get("/api/traces", params={"project_id": "nuevo"}, headers=cab).status_code == 200
    usada = app.state.cuentas.claves(["nuevo"])[0]
    assert usada["last_used_at"], "usar la clave tiene que dejar su último uso apuntado"
    ajeno = agente.get("/api/traces", params={"project_id": "previo"}, headers=cab)
    assert ajeno.status_code == 403

    with _cliente(app) as admin:
        admin.post("/api/auth/login", json={"email": "ana@ejemplo.com", "password": CONTRASENA},
                   headers=H)
        assert admin.delete("/api/org/keys", params={"org_id": org, "key_id": key_id},
                            headers=H).status_code == 200
    assert agente.get("/api/traces", params={"project_id": "nuevo"}, headers=cab).status_code == 401


def test_una_clave_caducada_no_sirve(app):
    with _cliente(app) as admin:
        _configurar(admin)
    cuentas = app.state.cuentas
    _, clave = cuentas.crear_clave(
        "previo", "vieja", "ana", datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    r = TestClient(app).get("/api/traces", params={"project_id": "previo"},
                            headers={"Authorization": f"Bearer {clave}"})
    assert r.status_code == 401
    assert "caducado" in r.json()["detail"]


# ---------------------------------------------------------------------------------
# El modo local sigue sin cuentas
# ---------------------------------------------------------------------------------


def test_en_local_no_hay_cuentas_ni_hace_falta_entrar(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "l.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.delenv("LAPLACE_AUTH_REQUIRED", raising=False)
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        assert c.get("/api/auth/me").json() == {"mode": "local", "user": None}
        assert c.get("/api/projects").status_code == 200
        assert c.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * 12},
                      headers=H).status_code == 404
    config.get_settings.cache_clear()
