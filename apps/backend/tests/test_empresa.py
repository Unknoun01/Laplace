"""Verificación de correo, SSO por OIDC y SCIM (D-182).

Como en `test_cuentas.py`, casi todo son intentos de lo que no se debe poder:
- entrar por SSO sin el navegador que lo empezó, o con un `state` gastado;
- colar un `id_token` de otro emisor, para otro cliente, caducado o con otro `nonce`;
- que el proveedor de una organización entre en la cuenta de alguien de otra;
- reclamar `gmail.com`, o que una organización se ponga sus propios dominios;
- seguir entrando con contraseña cuando la organización obliga a SSO;
- que una clave de SCIM de una organización vea a la gente de otra;
- dejar a una organización sin propietario por SCIM;
- mandar invitaciones en nombre de un correo sin verificar.

El proveedor de identidad y el servidor de correo son falsos: se sustituyen el GET, el
POST y el `mailer`. Lo demás es la instalación de nube de verdad, sobre SQLite.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import uuid

import pytest
from fastapi.testclient import TestClient
from test_cuentas import CONTRASENA, H, _cliente, _configurar, _invitar_y_aceptar, app  # noqa: F401

from laplace_backend import sso
from laplace_backend.config import Settings

EMISOR = "https://idp.ejemplo.com"
CLIENTE = "laplace-cliente"


# ---------------------------------------------------------------------------------
# Lo falso: el correo y el proveedor
# ---------------------------------------------------------------------------------


class Buzon:
    def __init__(self) -> None:
        self.cartas: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, text: str) -> bool:
        self.cartas.append((to, subject, text))
        return True

    def enlace(self, para: str) -> str:
        texto = next(t for to, _, t in reversed(self.cartas) if to == para)
        return next(p for p in texto.split() if "token=" in p).split("token=")[1]


@pytest.fixture
def buzon(app, monkeypatch):  # noqa: F811
    b = Buzon()
    monkeypatch.setattr(type(app.state.alerts), "mailer", property(lambda self: b))
    return b


def _jwt(claims: dict) -> str:
    def parte(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{parte({'alg': 'RS256'})}.{parte(claims)}.firma"


class Proveedor:
    """Un OIDC falso. `claims` se mezcla con lo que haría falta para que todo cuadre."""

    def __init__(self, monkeypatch) -> None:
        self.claims: dict = {}
        self.canjes: list[dict] = []
        self.nonce = ""
        sso._DESCUBIERTOS.clear()
        monkeypatch.setattr(sso, "_get", self._get)
        monkeypatch.setattr(sso, "_post", self._post)

    def _get(self, url: str) -> dict:
        if url != f"{EMISOR}/.well-known/openid-configuration":
            raise sso.SSOError("proveedor", f"404 {url}")
        return {
            "issuer": EMISOR,
            "authorization_endpoint": f"{EMISOR}/authorize",
            "token_endpoint": f"{EMISOR}/token",
        }

    def _post(self, url, datos, usuario, secreto) -> dict:
        assert (usuario, secreto) == (CLIENTE, "secreto"), "el canje va con el secreto"
        self.canjes.append(datos)
        base = {
            "iss": EMISOR, "aud": CLIENTE, "sub": "sub-luis", "email": "luis@ejemplo.com",
            "email_verified": True, "name": "Luis", "nonce": self.nonce,
            "iat": time.time(), "exp": time.time() + 300,
        }
        return {"id_token": _jwt({**base, **self.claims}), "access_token": "x"}


def _org(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["orgs"][0]["id"]


def _con_sso(app, monkeypatch, enforce=False):  # noqa: F811
    """La instalación configurada, con SSO en su organización y `ejemplo.com` suyo."""
    proveedor = Proveedor(monkeypatch)
    admin = _cliente(app)
    _configurar(admin)
    org = _org(admin)
    r = admin.put("/api/org/sso/domains", json={"org_id": org, "domains": ["Ejemplo.com"]},
                  headers=H)
    assert r.status_code == 200, r.text
    r = admin.put(
        "/api/org/sso",
        json={"org_id": org, "issuer": EMISOR + "/", "client_id": CLIENTE,
              "client_secret": "secreto", "default_role": "lector", "enforce": enforce},
        headers=H,
    )
    assert r.status_code == 200, r.text
    return admin, org, proveedor


def _entrar(app, proveedor, email="luis@ejemplo.com", cliente=None, claims=None):  # noqa: F811
    """El viaje completo: empezar, ir al proveedor y volver. Devuelve la redirección."""
    c = cliente or _cliente(app)
    r = c.post("/api/auth/sso/start", json={"email": email, "next": "/trazas"}, headers=H)
    assert r.status_code == 200, r.text
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["url"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENTE]
    proveedor.nonce = q["nonce"][0]
    proveedor.claims = claims or {}
    vuelta = c.get(
        "/api/auth/sso/callback", params={"code": "codigo", "state": q["state"][0]},
        follow_redirects=False,
    )
    return c, vuelta, q["state"][0]


# ---------------------------------------------------------------------------------
# Verificar el correo
# ---------------------------------------------------------------------------------


def test_sin_servidor_de_correo_no_se_puede_verificar_y_se_dice(app):  # noqa: F811
    c = _cliente(app)
    yo = _configurar(c)["user"]
    assert yo["email_verified"] is False and yo["can_verify"] is False
    assert c.post("/api/auth/verify/send", headers=H).status_code == 503


def test_el_enlace_verifica_una_vez(app, buzon):  # noqa: F811
    c = _cliente(app)
    _configurar(c)
    assert c.get("/api/auth/me").json()["user"]["can_verify"] is True
    r = c.post("/api/auth/verify/send", headers=H)
    assert r.json()["sent"] is True
    token = buzon.enlace("ana@ejemplo.com")
    otro = _cliente(app)  # el enlace se abre donde sea: no hace falta sesión
    assert otro.post("/api/auth/verify", json={"token": token}, headers=H).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["email_verified"] is True
    assert otro.post("/api/auth/verify", json={"token": token}, headers=H).status_code == 404
    assert otro.post("/api/auth/verify", json={"token": "x"}, headers=H).status_code == 404
    # Un enlace verifica el correo al que se mandó: si el correo cambia, ya no vale.
    cuentas = app.state.cuentas
    beto = cuentas.crear_usuario("beto@ejemplo.com", "Beto", CONTRASENA)
    enlace = cuentas.token_verificacion(beto.id, beto.email)
    cuentas._ejecutar("UPDATE users SET email = ? WHERE id = ?", ("beto@otro.com", beto.id))
    assert cuentas.verificar(enlace) is None and not cuentas.verificado(beto.id)


def test_sin_verificar_no_se_manda_nada_en_tu_nombre(app, buzon):  # noqa: F811
    admin = _cliente(app)
    _configurar(admin)
    org = _org(admin)
    r = admin.post("/api/org/invitations",
                   json={"org_id": org, "email": "eva@otra.com", "role": "lector"}, headers=H)
    assert r.json()["emailed"] is False and r.json()["not_emailed_reason"]
    a_mano = r.json()["link"].split("token=")[1]
    assert buzon.cartas == [], "el enlace sale para mandarlo a mano, pero no se manda"
    admin.post("/api/auth/verify/send", headers=H)
    admin.post("/api/auth/verify", json={"token": buzon.enlace("ana@ejemplo.com")}, headers=H)
    r = admin.post("/api/org/invitations",
                   json={"org_id": org, "email": "rosa@otra.com", "role": "lector"}, headers=H)
    assert r.json()["emailed"] is True
    # Quien acepta un enlace que le llegó por correo tiene ese correo...
    rosa = _cliente(app)
    rosa.post("/api/auth/accept",
              json={"token": buzon.enlace("rosa@otra.com"), "password": CONTRASENA}, headers=H)
    assert rosa.get("/api/auth/me").json()["user"]["email_verified"] is True
    # ...y quien acepta uno que se mandó a mano, no se sabe.
    eva = _cliente(app)
    eva.post("/api/auth/accept", json={"token": a_mano, "password": CONTRASENA}, headers=H)
    assert eva.get("/api/auth/me").json()["user"]["email_verified"] is False


# ---------------------------------------------------------------------------------
# SSO
# ---------------------------------------------------------------------------------


def test_entrar_por_sso_crea_la_cuenta_verificada_con_el_rol_por_defecto(app, monkeypatch):  # noqa: F811
    _, org, proveedor = _con_sso(app, monkeypatch)
    c, vuelta, _ = _entrar(app, proveedor)
    assert vuelta.status_code == 303 and vuelta.headers["location"] == "/trazas"
    yo = c.get("/api/auth/me").json()
    assert yo["user"]["email"] == "luis@ejemplo.com" and yo["user"]["email_verified"] is True
    assert yo["orgs"] == [{"id": org, "name": "Viajes", "role": "lector"}]
    assert proveedor.canjes[0]["code_verifier"], "el canje lleva el PKCE"
    # Sin contraseña: ninguna sirve, tampoco una vacía.
    for clave in ("", "!sso", CONTRASENA):
        r = _cliente(app).post("/api/auth/login",
                               json={"email": "luis@ejemplo.com", "password": clave}, headers=H)
        assert r.status_code == 401
    # La segunda vez entra por su `sub`, aunque el proveedor cambie el correo.
    c2, vuelta, _ = _entrar(app, proveedor, claims={"email": "luis.garcia@ejemplo.com"})
    assert vuelta.status_code == 303
    assert c2.get("/api/auth/me").json()["user"]["email"] == "luis@ejemplo.com"


@pytest.mark.parametrize(
    ("claims", "motivo"),
    [
        ({"iss": "https://malo.com"}, "token"),
        ({"aud": "otro-cliente"}, "token"),
        ({"aud": [CLIENTE, "otro"]}, "token"),  # varias audiencias sin azp
        ({"exp": time.time() - 3600}, "token"),
        ({"nonce": "otro"}, "token"),
        ({"email_verified": False}, "correo"),
        ({"email": "luis@otra.com"}, "dominio"),
        ({"email": ""}, "correo"),
    ],
)
def test_un_id_token_que_no_cuadra_no_entra(app, monkeypatch, claims, motivo):  # noqa: F811
    _, _, proveedor = _con_sso(app, monkeypatch)
    c, vuelta, _ = _entrar(app, proveedor, claims=claims)
    assert vuelta.headers["location"] == f"/entrar?sso_error={motivo}"
    assert c.get("/api/auth/me").json()["user"] is None


def test_el_state_es_de_un_solo_uso_y_de_su_navegador(app, monkeypatch):  # noqa: F811
    _, _, proveedor = _con_sso(app, monkeypatch)
    c = _cliente(app)
    r = c.post("/api/auth/sso/start", json={"email": "luis@ejemplo.com"}, headers=H)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["url"]).query)
    proveedor.nonce = q["nonce"][0]
    # Otro navegador con el state bueno: es el ataque de meterte en una sesión ajena.
    ajeno = _cliente(app)
    v = ajeno.get("/api/auth/sso/callback", params={"code": "c", "state": q["state"][0]},
                  follow_redirects=False)
    assert v.headers["location"] == "/entrar?sso_error=estado"
    # Y el intento lo ha gastado: el bueno ya no entra con él.
    v = c.get("/api/auth/sso/callback", params={"code": "c", "state": q["state"][0]},
              follow_redirects=False)
    assert v.headers["location"] == "/entrar?sso_error=estado"
    # Un `next` a otro sitio se queda en casa.
    r = c.post("/api/auth/sso/start", json={"email": "luis@ejemplo.com",
                                             "next": "//malo.com/x"}, headers=H)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["url"]).query)
    proveedor.nonce = q["nonce"][0]
    v = c.get("/api/auth/sso/callback", params={"code": "c", "state": q["state"][0]},
              follow_redirects=False)
    assert v.headers["location"] == "/"


def test_el_proveedor_de_una_organizacion_no_entra_en_cuentas_de_otra(app, monkeypatch):  # noqa: F811
    admin, org, proveedor = _con_sso(app, monkeypatch)
    cuentas = app.state.cuentas
    # Marta tiene cuenta, con correo del dominio, pero es de otra organización.
    otra = cuentas.crear_org("Otra")
    marta = cuentas.crear_usuario("marta@ejemplo.com", "Marta", CONTRASENA)
    cuentas.poner_miembro(otra, marta.id, "propietario")
    marta_idp = {"sub": "sub-marta", "email": "marta@ejemplo.com"}
    c, vuelta, _ = _entrar(app, proveedor, claims=marta_idp)
    assert vuelta.headers["location"] == "/entrar?sso_error=sin_invitacion"
    # Ana sí es de la organización: se enlaza a su cuenta.
    c, vuelta, _ = _entrar(app, proveedor, claims={"sub": "sub-ana", "email": "ana@ejemplo.com"})
    assert vuelta.status_code == 303
    assert c.get("/api/auth/me").json()["user"]["email"] == "ana@ejemplo.com"
    # Y a quien quitan de la organización no vuelve a entrar sola por SSO.
    _entrar(app, proveedor)
    luis = cuentas.usuario_por_email("luis@ejemplo.com")[0]
    cuentas.quitar_miembro(org, luis.id)
    _, vuelta, _ = _entrar(app, proveedor)
    assert vuelta.headers["location"] == "/entrar?sso_error=fuera"


def test_los_dominios_los_pone_la_instalacion_y_no_los_publicos(app, monkeypatch):  # noqa: F811
    admin, org, _ = _con_sso(app, monkeypatch)
    jefa = _invitar_y_aceptar(app, admin, org, "jefa@ejemplo.com", "admin")
    r = jefa.put("/api/org/sso/domains", json={"org_id": org, "domains": ["otra.com"]},
                 headers=H)
    assert r.status_code == 403, "una organización no se pone sus propios dominios"
    r = admin.put("/api/org/sso/domains", json={"org_id": org, "domains": ["gmail.com"]},
                  headers=H)
    assert r.status_code == 400
    cuentas = app.state.cuentas
    otra = cuentas.crear_org("Otra")
    with pytest.raises(ValueError):
        cuentas.poner_dominios(otra, ["ejemplo.com"], "x")
    # La jefa, admin de la organización, sí configura su SSO; el secreto no vuelve.
    r = jefa.get("/api/org/sso", params={"org_id": org})
    assert r.status_code == 200 and r.json()["has_secret"] is True
    assert "secreto" not in r.text
    r = jefa.put("/api/org/sso", json={"org_id": org, "issuer": EMISOR, "client_id": CLIENTE,
                                       "client_secret": None}, headers=H)
    assert cuentas.config_sso(org).client_secret == "secreto", "None conserva el secreto"
    r = jefa.put("/api/org/sso", json={"org_id": org, "issuer": "https://no-existe.com",
                                       "client_id": CLIENTE}, headers=H)
    assert r.status_code == 400, "el emisor se comprueba al guardarlo"
    lector = _invitar_y_aceptar(app, admin, org, "lee@ejemplo.com", "lector")
    assert lector.get("/api/org/sso", params={"org_id": org}).status_code == 403


def test_si_la_organizacion_obliga_a_sso_la_contrasena_no_sirve(app, monkeypatch):  # noqa: F811
    admin, org, _ = _con_sso(app, monkeypatch)
    _invitar_y_aceptar(app, admin, org, "eva@ejemplo.com", "miembro")
    _invitar_y_aceptar(app, admin, org, "rui@fuera.com", "miembro")
    r = admin.put("/api/org/sso", json={"org_id": org, "issuer": EMISOR, "client_id": CLIENTE,
                                        "enforce": True}, headers=H)
    assert r.status_code == 200, r.text

    def entra(email: str) -> int:
        return _cliente(app).post(
            "/api/auth/login", json={"email": email, "password": CONTRASENA}, headers=H
        ).status_code

    assert entra("eva@ejemplo.com") == 403
    assert entra("rui@fuera.com") == 200, "un correo de fuera del dominio sigue con contraseña"
    assert entra("ana@ejemplo.com") == 200, "la administradora de la instalación también"


def test_un_correo_sin_sso_no_tiene_adonde_ir(app, monkeypatch):  # noqa: F811
    _con_sso(app, monkeypatch)
    r = _cliente(app).post("/api/auth/sso/start", json={"email": "x@nadie.com"}, headers=H)
    assert r.status_code == 404


# ---------------------------------------------------------------------------------
# SCIM
# ---------------------------------------------------------------------------------


def _scim(app, admin: TestClient, org: str) -> TestClient:  # noqa: F811
    r = admin.post("/api/org/scim-tokens", json={"org_id": org}, headers=H)
    assert r.status_code == 200 and r.json()["token"].startswith("lpscim_")
    c = _cliente(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    c.token_id = r.json()["id"]  # type: ignore[attr-defined]
    return c


def test_scim_da_de_alta_desactiva_y_da_de_baja(app, monkeypatch):  # noqa: F811
    admin, org, _ = _con_sso(app, monkeypatch)
    cuentas = app.state.cuentas
    scim = _scim(app, admin, org)
    assert _cliente(app).get("/scim/v2/Users").status_code == 401
    assert scim.get("/scim/v2/ServiceProviderConfig").json()["patch"]["supported"] is True

    nuevo = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
             "userName": "Nora@Ejemplo.com", "name": {"givenName": "Nora", "familyName": "Vidal"},
             "externalId": "00u1", "active": True}
    r = scim.post("/scim/v2/Users", json=nuevo)
    assert r.status_code == 201, r.text
    nora = r.json()
    assert nora["userName"] == "nora@ejemplo.com" and nora["active"] is True
    assert cuentas.rol_en(org, nora["id"]) == "lector", "el rol por defecto del SSO"
    assert cuentas.verificado(nora["id"]), "un dominio de la organización"
    assert scim.post("/scim/v2/Users", json=nuevo).status_code == 409

    r = scim.get("/scim/v2/Users", params={"filter": 'userName eq "nora@ejemplo.com"'})
    assert r.json()["totalResults"] == 1
    assert scim.get("/scim/v2/Users", params={"filter": 'externalId eq "00u1"'}).json()[
        "totalResults"] == 1
    assert scim.get("/scim/v2/Users", params={"filter": "title pr"}).status_code == 400

    # Okta desactiva con `path`; Entra ID, con un objeto de valores.
    r = scim.patch(f"/scim/v2/Users/{nora['id']}", json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert r.json()["active"] is False and cuentas.rol_en(org, nora["id"]) is None
    assert scim.get(f"/scim/v2/Users/{nora['id']}").json()["active"] is False
    r = scim.patch(f"/scim/v2/Users/{nora['id']}", json={
        "Operations": [{"op": "Replace", "value": {"active": "True", "displayName": "Nora V."}}]})
    assert r.json()["active"] is True and r.json()["displayName"] == "Nora V."
    assert cuentas.rol_en(org, nora["id"]) == "lector"

    assert scim.delete(f"/scim/v2/Users/{nora['id']}").status_code == 204
    assert scim.get(f"/scim/v2/Users/{nora['id']}").status_code == 404
    assert cuentas.usuario(nora["id"]) is not None, "la cuenta no se borra: puede ser de otras"

    # Ana es la única propietaria: SCIM no la puede sacar.
    ana = cuentas.usuario_por_email("ana@ejemplo.com")[0]
    r = scim.patch(f"/scim/v2/Users/{ana.id}",
                   json={"Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert r.status_code == 409 and cuentas.rol_en(org, ana.id) == "propietario"

    r = admin.delete("/api/org/scim-tokens", params={"org_id": org, "id": scim.token_id},
                     headers=H)
    assert r.status_code == 200, r.text
    assert scim.get("/scim/v2/Users").status_code == 401, "revocada no sirve"


def test_la_clave_de_scim_de_una_organizacion_no_ve_otra(app, monkeypatch):  # noqa: F811
    admin, org, _ = _con_sso(app, monkeypatch)
    cuentas = app.state.cuentas
    otra = cuentas.crear_org("Otra")
    marta = cuentas.crear_usuario("marta@otra.com", "Marta", CONTRASENA)
    cuentas.poner_miembro(otra, marta.id, "propietario")
    scim = _scim(app, admin, org)
    assert scim.get(f"/scim/v2/Users/{marta.id}").status_code == 404
    assert scim.delete(f"/scim/v2/Users/{marta.id}").status_code == 404
    emails = [u["userName"] for u in scim.get("/scim/v2/Users").json()["Resources"]]
    assert "marta@otra.com" not in emails
    assert cuentas.rol_en(otra, marta.id) == "propietario"


def test_en_local_no_hay_scim(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "l.db"))
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        assert c.get("/scim/v2/Users", headers={"Authorization": "Bearer lpscim_x"}).status_code \
            == 404
    config.get_settings.cache_clear()


# ---------------------------------------------------------------------------------
# Los dos almacenes
# ---------------------------------------------------------------------------------


def _recorrido(cuentas, sufijo: str) -> dict:
    org = cuentas.crear_org(f"Org {sufijo}")
    u = cuentas.crear_usuario(f"ana-{sufijo}@ej{sufijo}.com", "Ana", CONTRASENA)
    cuentas.poner_miembro(org, u.id, "propietario")
    antes = cuentas.verificado(u.id)
    token = cuentas.token_verificacion(u.id, u.email)
    verificado = cuentas.verificar(token) == u.id and cuentas.verificado(u.id)
    cuentas.poner_dominios(org, [f"ej{sufijo}.com"], u.id)
    cuentas.guardar_sso(org, EMISOR, CLIENTE, "s", "miembro", True)
    cuentas.guardar_estado("st", org, "n", "v", "nav", "/x")
    estado = cuentas.gastar_estado("st", "nav")
    gastado = cuentas.gastar_estado("st", "nav")
    persona = sso.Persona(subject=f"s-{sufijo}", email=f"luis-{sufijo}@ej{sufijo}.com", name="L")
    luis, motivo = cuentas.usuario_sso(org, persona, "lector")
    _, token_scim = cuentas.crear_token_scim(org, u.id)
    nora, ya = cuentas.scim_alta(org, f"nora-{sufijo}@ej{sufijo}.com", "N", "e1", "miembro",
                                 True)
    cuentas.scim_activar(org, nora.id, False, "miembro")
    return {
        "org": org,
        "antes": antes,
        "verificado": verificado,
        "estado": estado,
        "gastado": gastado,
        "luis_rol": cuentas.rol_en(org, luis.id),
        "motivo": motivo,
        "obligado": cuentas.sso_obligado(u.id, u.email),
        "scim_org": cuentas.org_de_token_scim(token_scim) == org,
        "ya": ya,
        "nora": [(x["email"], x["active"]) for x in cuentas.scim_usuarios(org)
                 if x["email"].startswith("nora")],
    }


def test_lo_mismo_en_postgres_y_en_sqlite(tmp_path):
    from laplace_backend.cuentas import PostgresCuentas, SQLiteCuentas
    from laplace_backend.storage.metadata import SQLiteMetadataStore
    from laplace_backend.storage.postgres import PostgresMetadataStore

    meta = PostgresMetadataStore(Settings())
    if not meta.health():
        pytest.skip("no hay Postgres escuchando")
    meta.migrate()
    nube = PostgresCuentas(Settings().postgres_dsn)
    nube.migrate()
    SQLiteMetadataStore(tmp_path / "c.db").migrate()
    local = SQLiteCuentas(tmp_path / "c.db")
    local.migrate()
    sufijo = uuid.uuid4().hex[:8]
    a, b = _recorrido(nube, sufijo), _recorrido(local, sufijo + "l")
    try:
        for clave in ("antes", "verificado", "gastado", "luis_rol", "motivo", "obligado",
                      "scim_org", "ya"):
            assert a[clave] == b[clave], clave
        assert a["estado"]["next"] == "/x" and a["gastado"] is None and a["verificado"]
        assert a["luis_rol"] == "lector" and a["obligado"] is True
        assert a["nora"] == [(f"nora-{sufijo}@ej{sufijo}.com", False)]
    finally:
        for tabla in ("sso_configs", "sso_domains", "sso_identities", "scim_tokens",
                      "scim_users", "memberships"):
            nube._ejecutar(f"DELETE FROM {tabla} WHERE org_id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM orgs WHERE id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM users WHERE email LIKE ?", (f"%-{sufijo}@ej{sufijo}.com",))
