# Las fixtures `cerrado` y `app` se importan de sus ficheros y se piden por nombre, que
# es justo lo que F811 toma por una redefinición.
# ruff: noqa: F811
"""Lo menor de la auditoría: P3 (D-131).

Nada de esto se podía explotar para leer datos ajenos, pero cada punto era una forma de
equivocarse sin que nada lo dijera: una documentación abierta, un proyecto escogido en
silencio, un destinatario de correo con cabeceras dentro, conexiones sin cerrar y un
código de configuración que se podía gastar dos veces.
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_auth import _cab, cerrado  # noqa: E402,F401
from test_cuentas import CODIGO, CONTRASENA, H, _cliente, app  # noqa: E402,F401

# ---------------------------------------------------------------------------------
# P3-1: la documentación de la API, sólo en local
# ---------------------------------------------------------------------------------


def test_en_la_nube_no_hay_documentación_pública(cerrado):
    client, _ = cerrado
    for ruta in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(ruta)
        assert r.status_code != 200 or "openapi" not in r.text.lower(), ruta


def test_en_local_la_documentación_sigue_ahí(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "auto")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    try:
        with TestClient(main.app) as client:
            assert client.get("/openapi.json").status_code == 200
    finally:
        config.get_settings.cache_clear()


# ---------------------------------------------------------------------------------
# P3-2: con varios proyectos hay que decir cuál
# ---------------------------------------------------------------------------------


def test_una_identidad_con_varios_proyectos_no_escoge_uno_en_silencio():
    from laplace_backend.auth import AuthError, Identity

    varios = Identity(name="ana", user_id="u1", projects=frozenset({"b", "a"}))
    with pytest.raises(AuthError) as error:
        varios.scope(None)
    assert error.value.status == 400
    assert varios.scope("b") == "b"
    # Con uno solo —una clave de proyecto— sigue siendo el suyo, sin tener que decirlo.
    assert Identity(projects=frozenset({"a"})).scope(None) == "a"


# ---------------------------------------------------------------------------------
# P3-3: el destinatario de las alertas se valida de verdad
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [
        "ana@ejemplo.com\r\nBcc: todos@otro.com",
        "ana@ejemplo.com\nSubject: hola",
        "sin-arroba",
        "ana@sin-punto",
        ",".join(f"p{i}@ejemplo.com" for i in range(6)),
    ],
)
def test_un_destinatario_malo_se_rechaza(cerrado, valor):
    client, claves = cerrado
    r = client.put(
        "/api/alert-settings",
        json={"project_id": "mio", "email_to": valor},
        headers=_cab(claves, "*"),
    )
    assert r.status_code == 400, valor


def test_destinatarios_buenos_se_aceptan(cerrado):
    client, claves = cerrado
    r = client.put(
        "/api/alert-settings",
        json={"project_id": "mio", "email_to": "Ana <ana@ejemplo.com>, bea@ejemplo.es"},
        headers=_cab(claves, "*"),
    )
    assert r.status_code == 200, r.text


def test_un_destinatario_antiguo_con_salto_de_línea_no_tumba_el_envío():
    """Lo guardado antes de validar no puede reventar el ciclo de alertas."""
    from laplace_backend.alerts import EmailNotifier
    from laplace_backend.config import Settings

    correo = EmailNotifier(Settings(smtp_host="127.0.0.1", smtp_port=1, smtp_from="a@b.co"))
    assert correo.send("x@y.co\nBcc: z@w.co", "asunto", "texto") is False


# ---------------------------------------------------------------------------------
# P3-4: el apunte de «último uso» no crece sin fin
# ---------------------------------------------------------------------------------


def test_los_usos_recordados_se_barren(monkeypatch):
    import asyncio

    from laplace_backend import auth

    monkeypatch.setattr(auth, "_MAX_USOS_RECORDADOS", 3)
    medio = auth.AuthMiddleware(
        lambda *a: None,
        required=True,
        metadata_getter=lambda: None,
        cuentas_getter=lambda: type("C", (), {"usar_clave": lambda self, k: None})(),
    )
    medio._usos = {f"viejo{i}": -1000.0 for i in range(3)}
    asyncio.run(medio._apuntar_uso("nuevo"))
    assert set(medio._usos) == {"nuevo"}


# ---------------------------------------------------------------------------------
# P3-5: las conexiones SQLite se cierran
# ---------------------------------------------------------------------------------


def test_las_conexiones_sqlite_quedan_cerradas(tmp_path):
    from laplace_backend.cuentas import SQLiteCuentas
    from laplace_backend.storage.metadata import SQLiteMetadataStore

    for almacen in (SQLiteMetadataStore(tmp_path / "m.db"), SQLiteCuentas(tmp_path / "c.db")):
        with almacen._conn() as conn:
            conn.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ---------------------------------------------------------------------------------
# P3-7: el código de configuración se gasta una vez
# ---------------------------------------------------------------------------------


def test_dos_configuraciones_a_la_vez_crean_un_solo_administrador(app):
    resultados: list[int] = []
    barrera = threading.Barrier(4)

    def configurar(i: int) -> None:
        with _cliente(app) as c:
            barrera.wait()
            r = c.post(
                "/api/auth/setup",
                json={"token": CODIGO, "email": f"admin{i}@ejemplo.com", "password": CONTRASENA},
                headers=H,
            )
            resultados.append(r.status_code)

    hilos = [threading.Thread(target=configurar, args=(i,)) for i in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert resultados.count(200) == 1, resultados
    admins = app.state.cuentas._filas("SELECT COUNT(*) FROM users WHERE is_admin = 1")
    assert admins[0][0] == 1


def test_si_la_configuración_falla_el_código_sigue_valiendo(app):
    with _cliente(app) as c:
        malo = c.post(
            "/api/auth/setup",
            json={"token": CODIGO, "email": "a@ejemplo.com", "password": "corta"},
            headers=H,
        )
        assert malo.status_code == 400
        bueno = c.post(
            "/api/auth/setup",
            json={"token": CODIGO, "email": "a@ejemplo.com", "password": CONTRASENA},
            headers=H,
        )
        assert bueno.status_code == 200, bueno.text
