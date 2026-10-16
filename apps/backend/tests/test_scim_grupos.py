"""Los grupos de SCIM y el rol que dan (D-211).

Lo que no se debe poder:
- que el proveedor de identidad se invente un rol: lo pone un admin en Laplace;
- que un grupo haga a alguien propietario, o le quite el puesto a un propietario;
- meter en un grupo a alguien de otra organización, o tocar los grupos de otra;
- seguir siendo admin después de que el proveedor te saque del grupo de admins.
"""

from __future__ import annotations

import pytest
from test_cuentas import H, _cliente, _invitar_y_aceptar, app  # noqa: F401
from test_empresa import _con_sso, _scim

GRUPO = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _alta(scim, email: str) -> str:
    r = scim.post("/scim/v2/Users", json={"userName": email, "active": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grupo(scim, nombre: str, miembros=(), **extra) -> dict:
    r = scim.post("/scim/v2/Groups", json={
        "schemas": [GRUPO], "displayName": nombre,
        "members": [{"value": m} for m in miembros], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _patch(scim, gid: str, *ops) -> dict:
    r = scim.patch(f"/scim/v2/Groups/{gid}", json={"schemas": [PATCH], "Operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()


def test_los_grupos_dan_el_rol_mayor_y_salir_del_grupo_lo_quita(app, monkeypatch):  # noqa: F811
    admin, org, _ = _con_sso(app, monkeypatch)  # rol por defecto: lector
    cuentas = app.state.cuentas
    scim = _scim(app, admin, org)
    nora = _alta(scim, "nora@ejemplo.com")
    # El proveedor no dice qué rol da un grupo: lo que mande se ignora.
    devs = _grupo(scim, "Devs", [nora], externalId="g1", role="propietario")
    admins = _grupo(scim, "Admins")
    assert cuentas.rol_en(org, nora) == "lector"
    cuentas.poner_miembro(org, nora, "admin")  # a mano, en Laplace
    _patch(scim, admins["id"], {"op": "add", "path": "members", "value": [{"value": nora}]})
    _patch(scim, admins["id"], {"op": "remove", "path": "members", "value": [{"value": nora}]})
    assert cuentas.rol_en(org, nora) == "admin", "sin grupos con rol, los grupos no mandan"
    assert [m["value"] for m in scim.get(f"/scim/v2/Groups/{devs['id']}").json()["members"]] \
        == [nora]
    r = scim.get("/scim/v2/Groups", params={"filter": 'displayName eq "Devs"'})
    assert [g["id"] for g in r.json()["Resources"]] == [devs["id"]]
    assert scim.get("/scim/v2/Groups", params={"filter": 'externalId eq "g1"'}).json()[
        "totalResults"] == 1

    def mapa(gid, rol):
        return admin.put("/api/org/scim-groups",
                         json={"org_id": org, "group_id": gid, "role": rol}, headers=H)

    assert mapa(devs["id"], "propietario").status_code == 422, "un grupo no hace propietarios"
    assert mapa(devs["id"], "miembro").status_code == 200
    assert cuentas.rol_en(org, nora) == "miembro"
    assert mapa(admins["id"], "admin").status_code == 200
    listado = admin.get("/api/org/scim-groups", params={"org_id": org}, headers=H).json()
    assert {g["display_name"]: g["role"] for g in listado["groups"]} == {
        "Devs": "miembro", "Admins": "admin"}

    # Okta: añadir con `path`; en dos grupos, el rol mayor.
    _patch(scim, admins["id"], {"op": "add", "path": "members", "value": [{"value": nora}]})
    assert cuentas.rol_en(org, nora) == "admin"
    # Okta saca con un filtro en el `path`.
    _patch(scim, admins["id"], {"op": "remove", "path": f'members[value eq "{nora}"]'})
    assert cuentas.rol_en(org, nora) == "miembro"
    # Entra ID saca con una lista de valores; sin grupos con rol, el rol por defecto.
    _patch(scim, devs["id"], {"op": "Remove", "path": "members", "value": [{"value": nora}]})
    assert cuentas.rol_en(org, nora) == "lector"
    # Entra ID cambia nombre y miembros con un objeto de valores.
    g = _patch(scim, admins["id"], {"op": "Replace", "value": {
        "displayName": "Administración", "members": [{"value": nora}]}})
    assert g["displayName"] == "Administración" and cuentas.rol_en(org, nora) == "admin"
    # Un `PUT` reemplaza el grupo entero.
    r = scim.put(f"/scim/v2/Groups/{admins['id']}",
                 json={"schemas": [GRUPO], "displayName": "Admins", "members": []})
    assert r.status_code == 200 and r.json()["members"] == []
    assert cuentas.rol_en(org, nora) == "lector"

    # Borrar el grupo le quita lo que daba.
    _patch(scim, devs["id"], {"op": "add", "path": "members", "value": [{"value": nora}]})
    assert cuentas.rol_en(org, nora) == "miembro"
    assert scim.delete(f"/scim/v2/Groups/{devs['id']}").status_code == 204
    assert scim.get(f"/scim/v2/Groups/{devs['id']}").status_code == 404
    assert cuentas.rol_en(org, nora) == "lector"
    assert "scim_rol_grupo" in [a["action"] for a in cuentas.auditoria(org)]


def test_un_grupo_no_toca_a_un_propietario_ni_a_quien_no_gestiona_el_proveedor(
    app, monkeypatch  # noqa: F811
):
    admin, org, _ = _con_sso(app, monkeypatch)
    cuentas = app.state.cuentas
    scim = _scim(app, admin, org)
    ana = cuentas.usuario_por_email("ana@ejemplo.com")[0].id
    lectores = _grupo(scim, "Lectores", [ana])
    assert admin.put("/api/org/scim-groups",
                     json={"org_id": org, "group_id": lectores["id"], "role": "lector"},
                     headers=H).status_code == 200
    assert cuentas.rol_en(org, ana) == "propietario"
    # Pedro entró por invitación: hasta que el proveedor lo meta en un grupo, es de Laplace.
    pedro_c = _invitar_y_aceptar(app, admin, org, "pedro@ejemplo.com", "admin")
    pedro = pedro_c.get("/api/auth/me").json()["user"]["id"]
    _alta(scim, "nora@ejemplo.com")
    assert cuentas.rol_en(org, pedro) == "admin"
    _patch(scim, lectores["id"], {"op": "add", "path": "members", "value": [{"value": pedro}]})
    assert cuentas.rol_en(org, pedro) == "lector"
    # Y una vez que es del proveedor, salir del grupo le deja el rol por defecto.
    _patch(scim, lectores["id"], {"op": "remove", "path": f'members[value eq "{pedro}"]'})
    assert cuentas.rol_en(org, pedro) == "lector"
    # Sólo un admin pone los roles de los grupos.
    assert pedro_c.put("/api/org/scim-groups",
                       json={"org_id": org, "group_id": lectores["id"], "role": "admin"},
                       headers=H).status_code == 403
    assert cuentas.scim_grupo(org, lectores["id"])["role"] == "lector"
    # Y aunque llegara a la base, la base tampoco hace propietarios con un grupo.
    with pytest.raises(ValueError):
        cuentas.rol_de_grupo(org, lectores["id"], "propietario")


def test_la_clave_de_otra_organizacion_no_ve_sus_grupos_ni_mete_a_los_suyos(
    app, monkeypatch  # noqa: F811
):
    admin, org, _ = _con_sso(app, monkeypatch)
    cuentas = app.state.cuentas
    scim = _scim(app, admin, org)
    otra = cuentas.crear_org("Otra")
    marta = cuentas.crear_usuario("marta@otra.com", "Marta", "una-contraseña-larga")
    cuentas.poner_miembro(otra, marta.id, "miembro")
    _, token_otra = cuentas.crear_token_scim(otra, marta.id)
    ajena = _cliente(app)
    ajena.headers["Authorization"] = f"Bearer {token_otra}"

    devs = _grupo(scim, "Devs")
    assert ajena.get(f"/scim/v2/Groups/{devs['id']}").status_code == 404
    assert ajena.delete(f"/scim/v2/Groups/{devs['id']}").status_code == 404
    assert ajena.patch(f"/scim/v2/Groups/{devs['id']}", json={"Operations": [
        {"op": "replace", "path": "displayName", "value": "x"}]}).status_code == 404
    assert ajena.get("/scim/v2/Groups").json()["totalResults"] == 0
    # Meter a alguien de otra organización en un grupo propio es un 400.
    r = scim.patch(f"/scim/v2/Groups/{devs['id']}", json={"Operations": [
        {"op": "add", "path": "members", "value": [{"value": marta.id}]}]})
    assert r.status_code == 400
    r = scim.post("/scim/v2/Groups", json={"displayName": "X", "members": [{"value": marta.id}]})
    assert r.status_code == 400
    # Ni el admin de una organización pone el rol a un grupo de otra.
    grupo_otra = cuentas.scim_crear_grupo(otra, "Suyos", "")
    assert admin.put("/api/org/scim-groups",
                     json={"org_id": org, "group_id": grupo_otra, "role": "admin"},
                     headers=H).status_code == 404
    assert cuentas.rol_en(otra, marta.id) == "miembro"
