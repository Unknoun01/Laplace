"""El webhook de GitHub: al fusionar el PR de Laplace, el hallazgo queda arreglado (D-191).

La ruta no pide clave de Laplace —la llama GitHub—, así que lo que la protege es la firma
(`X-Hub-Signature-256`, HMAC-SHA256 del cuerpo). Con la GitHub App, el secreto es el de
la instalación (`LAPLACE_GITHUB_WEBHOOK_SECRET`); con un token, el del proyecto, que se
genera al conectar el repositorio. Marcar como arreglado no se cree a ciegas: es la
frontera desde la que el seguimiento (D-123) compara antes y después.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from test_bot_arreglos import (  # noqa: F401 - `api` es una fixture
    AGENTE_BUCLE_REPO,
    _conectar,
    _hallazgo,
    _sembrar_bucle,
    api,
)

from laplace_backend import github_bot as gb

FUSIONADO = "2026-10-03T10:00:00Z"


def _evento(numero: int, *, merged: bool = True, repo: str = "acme/agentes",
            rama: str = "", accion: str = "closed") -> dict:
    return {
        "action": accion,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": numero,
            "merged": merged,
            "merged_at": FUSIONADO if merged else None,
            "html_url": f"https://github.com/{repo}/pull/{numero}",
            "head": {"ref": rama},
        },
    }


def _mandar(c, datos: dict, secreto: str, evento: str = "pull_request"):
    cuerpo = json.dumps(datos).encode()
    firma = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return c.post(
        "/api/github/webhook",
        content=cuerpo,
        headers={"X-GitHub-Event": evento, "X-Hub-Signature-256": firma,
                 "Content-Type": "application/json"},
    )


@pytest.fixture
def con_pr(api, monkeypatch):  # noqa: F811 - `api` es la fixture importada
    """Un proyecto con un bucle, el repositorio conectado y su PR abierto."""
    c, app = api
    _sembrar_bucle(app.state.store, "p")
    _conectar(c, monkeypatch, {"agente/bucle.py": AGENTE_BUCLE_REPO})
    hallazgo = _hallazgo(c, "bucle")
    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": hallazgo["id"]})
    assert r.status_code == 200, r.text
    secreto = c.get("/api/github", params={"project_id": "p"}).json()["webhook_secret"]
    assert secreto
    return c, app, hallazgo, r.json()["number"], secreto


def _estado(app, finding_id: str) -> dict | None:
    return app.state.metadata.get_setting("p", f"finding:{finding_id}")


def test_al_fusionar_el_pr_el_hallazgo_queda_arreglado(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    r = _mandar(c, _evento(numero, rama=gb.rama_para(hallazgo["id"])), secreto)
    assert r.status_code == 200, r.text
    assert r.json()["fixed"] == [hallazgo["id"]]
    estado = _estado(app, hallazgo["id"])
    assert estado["status"] == "arreglado"
    # Desde cuándo se mide: la hora de la fusión, no la de llegada del aviso.
    assert estado["at"].startswith("2026-10-03T10:00:00")
    assert f"#{numero}" in estado["note"]
    pr = app.state.metadata.get_setting("p", gb.PREFIJO_PR + hallazgo["id"])
    assert pr["state"] == "merged"

    # Y el Diagnóstico lo enseña como arreglado.
    ficha = c.get(f"/api/findings/{hallazgo['id']}", params={"project_id": "p", "days": 7})
    assert ficha.json()["state"] in ("arreglado", "reaparecido")


def test_sin_firma_o_con_otra_no_se_marca_nada(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    sin = c.post("/api/github/webhook", json=_evento(numero),
                 headers={"X-GitHub-Event": "pull_request"})
    assert sin.status_code == 401
    otra = _mandar(c, _evento(numero), "no-es-el-secreto")
    assert otra.status_code == 401
    assert _estado(app, hallazgo["id"]) is None


def test_la_firma_es_del_cuerpo_entero(con_pr):
    """Firmar un cuerpo y mandar otro no vale: la firma no se reaprovecha."""
    c, app, hallazgo, numero, secreto = con_pr
    bueno = json.dumps(_evento(numero + 1)).encode()
    firma = "sha256=" + hmac.new(secreto.encode(), bueno, hashlib.sha256).hexdigest()
    r = c.post("/api/github/webhook", content=json.dumps(_evento(numero)).encode(),
               headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": firma})
    assert r.status_code == 401
    assert _estado(app, hallazgo["id"]) is None


def test_cerrado_sin_fusionar_no_es_un_arreglo(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    r = _mandar(c, _evento(numero, merged=False), secreto)
    assert r.status_code == 200
    assert r.json()["fixed"] == []
    assert _estado(app, hallazgo["id"]) is None
    assert app.state.metadata.get_setting("p", gb.PREFIJO_PR + hallazgo["id"])["state"] == "closed"


def test_un_pr_que_no_es_de_laplace_no_toca_nada(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    r = _mandar(c, _evento(numero + 7), secreto)
    assert r.json()["fixed"] == []
    # El mismo número en otra rama tampoco: los números se reutilizan si se borra el repo.
    r = _mandar(c, _evento(numero, rama="feature/otra-cosa"), secreto)
    assert r.json()["fixed"] == []
    assert _estado(app, hallazgo["id"]) is None


def test_otro_repositorio_con_el_mismo_numero_no_cuenta(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    r = _mandar(c, _evento(numero, repo="otra/cosa"), secreto)
    assert r.status_code == 401  # nadie de este Laplace tiene ese repositorio
    assert _estado(app, hallazgo["id"]) is None


def test_lo_ignorado_sigue_ignorado(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    c.post("/api/finding-state",
           json={"project_id": "p", "finding_id": hallazgo["id"], "status": "ignorado"})
    _mandar(c, _evento(numero), secreto)
    assert _estado(app, hallazgo["id"])["status"] == "ignorado"


def test_con_la_app_vale_el_secreto_de_la_instalacion(con_pr, monkeypatch):
    c, app, hallazgo, numero, _ = con_pr
    monkeypatch.setenv(gb.ENV_WEBHOOK_SECRET, "secreto-de-la-app")
    r = _mandar(c, _evento(numero), "secreto-de-la-app")
    assert r.status_code == 200, r.text
    assert _estado(app, hallazgo["id"])["status"] == "arreglado"


def test_el_ping_de_github_contesta(con_pr):
    c, _, _, _, secreto = con_pr
    r = _mandar(c, {"zen": "Keep it simple.", "repository": {"full_name": "acme/agentes"}},
                secreto, evento="ping")
    assert r.status_code == 200


def test_el_secreto_del_proyecto_se_genera_una_vez_y_se_conserva(con_pr):
    c, _, _, _, secreto = con_pr
    c.put("/api/github", json={"project_id": "p", "repo": "acme/agentes", "base_branch": "main"})
    assert c.get("/api/github", params={"project_id": "p"}).json()["webhook_secret"] == secreto

