"""Los mensajes de error de la API, en el idioma de la petición (Fase 5, D-149).

La web enseña el `detail` de un error tal cual en sus formularios —una contraseña
corta, una invitación caducada, un prompt que ya existe—, así que un error en español
es una frase en español en mitad de una pantalla en chino. Dos pruebas:

* **Guardia sobre el código**, como `test_sin_frases_sueltas.py` pero para las rutas:
  ninguna frase escrita a mano en un `HTTPException`, un `AuthError`, un cuerpo con
  `"detail"` ni en lo que devuelven los validadores que acaban en uno. Lee el código,
  así que cubre también los errores que ninguna prueba provoca.
* **Peticiones de verdad** en varios idiomas: el middleware del idioma tiene que ir por
  fuera del de autenticación y del tope de tamaño, que contestan sin llegar a la ruta.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re

import pytest
from fastapi.testclient import TestClient

from laplace_backend import (
    api,
    api_ajustes,
    api_cuentas,
    api_evals,
    api_prompts,
    auth,
    cuentas,
    limites,
    main,
)
from laplace_backend.ingest import otlp

MODULOS = (
    api, api_ajustes, api_cuentas, api_evals, api_prompts, auth, cuentas, limites, main, otlp
)

#: Dos palabras seguidas en minúscula: lo que distingue una frase de un nombre de campo.
FRASE = re.compile(r"[a-záéíóúñ]{2,} [a-záéíóúñ]{2,}", re.I)
#: Las llamadas cuyo texto acaba delante del usuario.
ERRORES = {"HTTPException", "AuthError", "CuerpoDemasiadoGrande"}
#: Funciones que devuelven un motivo que la ruta pasa tal cual a `detail`.
VALIDADORES = {"validar_contrasena", "motivo_correo_invalido"}


def _literales(nodo: ast.AST) -> list[ast.Constant]:
    return [
        n
        for n in ast.walk(nodo)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and FRASE.search(n.value)
    ]


def _frases(modulo) -> list[str]:
    arbol = ast.parse(inspect.getsource(modulo))
    malas: list[ast.Constant] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            funcion = nodo.func
            nombre = (
                funcion.attr if isinstance(funcion, ast.Attribute) else getattr(funcion, "id", "")
            )
            if nombre in ERRORES:
                for arg in [*nodo.args, *(k.value for k in nodo.keywords)]:
                    malas += _literales(arg)
        elif isinstance(nodo, ast.Dict):
            for clave, valor in zip(nodo.keys, nodo.values, strict=True):
                if isinstance(clave, ast.Constant) and clave.value == "detail":
                    malas += _literales(valor)
        elif isinstance(nodo, ast.FunctionDef) and nodo.name in VALIDADORES:
            for ret in ast.walk(nodo):
                if isinstance(ret, ast.Return) and ret.value is not None:
                    malas += _literales(ret.value)
    return [f"{modulo.__name__}:{n.lineno}: {n.value[:70]!r}" for n in malas]


def test_ningun_error_escrito_a_mano():
    malas = [m for modulo in MODULOS for m in _frases(modulo)]
    assert malas == [], (
        "estos errores saldrían en español en cualquier idioma; pásalos por "
        "`textos/*.json` con t(): " + "\n".join(malas)
    )


def test_la_guardia_ve_un_error_escrito_a_mano():
    """La guardia tiene que morder: un módulo de mentira con un error en español."""

    class Falso:
        __name__ = "falso"

    fuente = (
        "def f():\n"
        "    raise HTTPException(status_code=404, detail='ese prompt no existe')\n"
        "def validar_contrasena(x):\n"
        "    return f'la contraseña es corta'\n"
    )
    original = inspect.getsource
    try:
        inspect.getsource = lambda m: fuente if m is Falso else original(m)
        assert len(_frases(Falso)) == 2
    finally:
        inspect.getsource = original


# ---------------------------------------------------------------------------------
# Peticiones de verdad
# ---------------------------------------------------------------------------------


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


@pytest.fixture
def cerrado(tmp_path, monkeypatch):
    """Con autenticación: el error lo da el middleware, no una ruta."""
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "true")
    from laplace_backend import config

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


@pytest.mark.parametrize(
    ("lengua", "esperado"),
    [
        ("es", "traza no encontrada"),
        ("en", "trace not found"),
        ("pt-BR", "trace não encontrado"),
        ("fr", "trace introuvable"),
        ("zh-CN", "未找到该追踪"),
    ],
)
def test_una_ruta_contesta_en_el_idioma_pedido(cliente, lengua, esperado):
    r = cliente.get("/api/traces/no-existe", headers={"Accept-Language": lengua})
    assert r.status_code == 404
    assert r.json()["detail"] == esperado


def test_sin_idioma_pedido_sigue_en_espanol(cliente):
    """Un script sin cabeceras recibe lo de siempre (D-147)."""
    r = cliente.get("/api/traces/no-existe")
    assert r.json()["detail"] == "traza no encontrada"


def test_el_middleware_de_autenticacion_tambien_traduce(cerrado):
    r = cerrado.get("/api/projects", headers={"Accept-Language": "en"})
    assert r.status_code == 401
    assert r.json()["detail"].startswith("sign in, or send an API key")
    r = cerrado.get(
        "/api/projects", headers={"Authorization": "Bearer lp_falsa"}, params={"lang": "fr"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "identifiant invalide"
    # La web distinguía este caso comparando el texto en español; ahora lee el código.
    assert r.json()["code"] == "credencial_invalida"


def test_el_tope_de_tamano_tambien_traduce(cliente):
    tope = main.app.state.settings.max_body_bytes
    r = cliente.post(
        "/v1/traces",
        content=b"x" * (tope + 1),
        headers={"Accept-Language": "zh", "Content-Type": "application/x-protobuf"},
    )
    assert r.status_code == 413
    assert "MiB" in r.json()["detail"] and "请求超过" in r.json()["detail"]


def test_los_motivos_de_una_contrasena_se_traducen():
    from laplace_backend import idioma

    with idioma.usar("en"):
        assert cuentas.validar_contrasena("corta") == "the password needs at least 10 characters"
        assert api_ajustes.motivo_correo_invalido("a\nb@c.com").startswith("the email address")


def test_el_estado_del_juez_se_traduce(cliente):
    r = cliente.get("/api/judge", headers={"Accept-Language": "en"})
    assert r.json()["detail"].startswith("The judge is off")
