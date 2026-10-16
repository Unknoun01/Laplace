"""SCIM 2.0: el proveedor de identidad da de alta y de baja a la gente (D-182).

Lo que un proveedor (Okta, Entra ID, OneLogin, JumpCloud) necesita para mantener una
organización al día sin que nadie la toque a mano: buscar a alguien por su correo, darlo
de alta, cambiarle el nombre, desactivarlo y borrarlo. Nada más:

* **`Users` y `Groups`.** El rol de quien entra por SCIM es el rol por defecto del SSO
  de la organización. Los grupos los mete y saca el proveedor, pero el rol que da cada
  uno lo pone un admin en Laplace (D-211): en varios, el mayor; en ninguno, el de por
  defecto; nunca propietario.
* **Desactivar es quitar la pertenencia**, no borrar la cuenta. La persona puede ser de
  otras organizaciones, y lo que pasa en una no puede tocar las demás. Sus datos de
  auditoría se quedan. Su sesión sigue abierta, pero ya no ve nada de esta organización:
  los roles se leen en cada petición.
* **No se queda nadie sin propietario.** Desactivar al último propietario es un 409.
* **La autenticación es una clave por organización** (`lpscim_…`), que se crea en la
  interfaz y se guarda como hash. Esta ruta está fuera de `/api` y el middleware no la
  mira: la comprueba ella entera.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/scim/v2")

ESQUEMA_USUARIO = "urn:ietf:params:scim:schemas:core:2.0:User"
ESQUEMA_GRUPO = "urn:ietf:params:scim:schemas:core:2.0:Group"
ESQUEMA_LISTA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ESQUEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
ESQUEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
TIPO = "application/scim+json"
MAX_POR_PAGINA = 200


class _Error(Exception):
    def __init__(self, status: int, detalle: str, tipo: str = "") -> None:
        super().__init__(detalle)
        self.status, self.detalle, self.tipo = status, detalle, tipo


def _respuesta(datos: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(datos, status_code=status, media_type=TIPO)


def _error(exc: _Error) -> JSONResponse:
    cuerpo = {"schemas": [ESQUEMA_ERROR], "status": str(exc.status), "detail": exc.detalle}
    if exc.tipo:
        cuerpo["scimType"] = exc.tipo
    return _respuesta(cuerpo, exc.status)


def _org(request: Request) -> tuple[Any, str]:
    """La organización de la clave, o 401. Sin cuentas (modo local), 404."""
    cuentas = getattr(request.app.state, "cuentas", None)
    if cuentas is None or not request.app.state.settings.auth_enforced:
        raise _Error(404, "SCIM no está disponible en esta instalación")
    cabecera = request.headers.get("authorization", "")
    token = cabecera[7:].strip() if cabecera.lower().startswith("bearer ") else ""
    org_id = cuentas.org_de_token_scim(token) if token else None
    if org_id is None:
        raise _Error(401, "clave de SCIM no válida")
    return cuentas, org_id


def _rol(cuentas: Any, org_id: str) -> str:
    config = cuentas.config_sso(org_id)
    return config.default_role if config else "miembro"


def _recurso(request: Request, u: dict[str, Any]) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    recurso: dict[str, Any] = {
        "schemas": [ESQUEMA_USUARIO],
        "id": u["id"],
        "userName": u["email"],
        "displayName": u["name"] or u["email"],
        "name": {"formatted": u["name"]},
        "emails": [{"value": u["email"], "primary": True, "type": "work"}],
        "active": u["active"],
        "meta": {
            "resourceType": "User",
            "created": u["created_at"],
            "location": f"{base}/scim/v2/Users/{u['id']}",
        },
    }
    if u.get("external_id"):
        recurso["externalId"] = u["external_id"]
    return recurso


_FILTRO = re.compile(r'^\s*(\w+)\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


def _filtro(filtro: str, campos: dict[str, str]) -> tuple[str, str] | None:
    """`campo eq "valor"` con un campo de `campos` (en minúsculas → clave), o 400."""
    if not filtro:
        return None
    m = _FILTRO.match(filtro)
    if not m or m.group(1).lower() not in campos:
        raise _Error(400, f"sólo se filtra con eq por {', '.join(campos)}", "invalidFilter")
    return campos[m.group(1).lower()], m.group(2)


def _nombre(datos: dict[str, Any]) -> str:
    nombre = datos.get("name") if isinstance(datos.get("name"), dict) else {}
    return str(
        datos.get("displayName")
        or nombre.get("formatted")
        or " ".join(p for p in (nombre.get("givenName"), nombre.get("familyName")) if p)
        or ""
    ).strip()


def _email(datos: dict[str, Any]) -> str:
    correos = datos.get("emails") if isinstance(datos.get("emails"), list) else []
    primario = next((e for e in correos if isinstance(e, dict) and e.get("primary")), None)
    candidato = datos.get("userName") or (primario or (correos[0] if correos else {})).get(
        "value"
    )
    return str(candidato or "").strip().lower()


def _activo(valor: Any) -> bool:
    if isinstance(valor, str):
        return valor.strip().lower() != "false"
    return bool(valor)


async def sincronizar_roles(cuentas: Any, org_id: str, user_ids: set[str]) -> None:
    """El rol que dan los grupos, a quien le cambiaron (D-211), con su apunte."""
    for uid in sorted(user_ids):
        nuevo = await run_in_threadpool(cuentas.sincronizar_rol, org_id, uid)
        if nuevo:
            await run_in_threadpool(
                cuentas.anotar, org_id, "", "scim_rol_grupo", f"{uid}: {nuevo}", ""
            )


async def _con(request: Request, hacer) -> Response:
    try:
        cuentas, org_id = await run_in_threadpool(_org, request)
        return await hacer(cuentas, org_id)
    except _Error as exc:
        return _error(exc)


# ---------------------------------------------------------------------------------
# Lo que el proveedor pregunta antes de nada
# ---------------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def service_provider_config(request: Request) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        return _respuesta({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_POR_PAGINA},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [{
                "type": "oauthbearertoken", "name": "Clave de SCIM",
                "description": "La clave lpscim_ de la organización, como Bearer.",
            }],
        })

    return await _con(request, hacer)


@router.get("/ResourceTypes")
async def resource_types(request: Request) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        return _respuesta({
            "schemas": [ESQUEMA_LISTA], "totalResults": 2, "Resources": [{
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": tipo, "name": tipo, "endpoint": f"/{tipo}s", "schema": esquema,
            } for tipo, esquema in (("User", ESQUEMA_USUARIO), ("Group", ESQUEMA_GRUPO))],
        })

    return await _con(request, hacer)


# ---------------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------------


@router.get("/Users")
async def list_users(
    request: Request, filter: str = "", startIndex: int = 1, count: int = 100  # noqa: A002, N803
) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        usuarios = await run_in_threadpool(cuentas.scim_usuarios, org_id)
        filtro = _filtro(filter, {"username": "email", "externalid": "external_id"})
        if filtro:
            campo, valor = filtro
            valor = valor.strip().lower() if campo == "email" else valor
            usuarios = [u for u in usuarios if u[campo] == valor]
        return _lista(usuarios, startIndex, count, lambda u: _recurso(request, u))

    return await _con(request, hacer)


def _lista(todos: list, inicio: int, cuantos: int, recurso) -> JSONResponse:
    inicio = max(inicio, 1)
    pagina = todos[inicio - 1 : inicio - 1 + min(max(cuantos, 0), MAX_POR_PAGINA)]
    return _respuesta({
        "schemas": [ESQUEMA_LISTA],
        "totalResults": len(todos),
        "startIndex": inicio,
        "itemsPerPage": len(pagina),
        "Resources": [recurso(x) for x in pagina],
    })


async def _uno(cuentas: Any, org_id: str, user_id: str) -> dict[str, Any]:
    u = await run_in_threadpool(cuentas.scim_usuario, org_id, user_id)
    if u is None:
        raise _Error(404, "no existe en esta organización")
    return u


@router.get("/Users/{user_id}")
async def get_user(request: Request, user_id: str) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        return _respuesta(_recurso(request, await _uno(cuentas, org_id, user_id)))

    return await _con(request, hacer)


async def _cuerpo(request: Request) -> dict[str, Any]:
    try:
        datos = await request.json()
    except ValueError as exc:
        raise _Error(400, "el cuerpo no es JSON", "invalidSyntax") from exc
    if not isinstance(datos, dict):
        raise _Error(400, "el cuerpo no es un objeto", "invalidSyntax")
    return datos


@router.post("/Users")
async def create_user(request: Request) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        datos = await _cuerpo(request)
        email = _email(datos)
        if "@" not in email:
            raise _Error(400, "userName tiene que ser un correo", "invalidValue")
        usuario, ya = await run_in_threadpool(
            cuentas.scim_alta, org_id, email, _nombre(datos),
            str(datos.get("externalId") or ""), _rol(cuentas, org_id),
            _activo(datos.get("active", True)),
        )
        if ya:
            raise _Error(409, f"{email} ya está en la organización", "uniqueness")
        await run_in_threadpool(cuentas.anotar, org_id, "", "scim_alta", email, "")
        await sincronizar_roles(cuentas, org_id, {usuario.id})
        return _respuesta(_recurso(request, await _uno(cuentas, org_id, usuario.id)), 201)

    return await _con(request, hacer)


async def _poner_activo(cuentas: Any, org_id: str, user_id: str, activo: bool) -> None:
    hecho = await run_in_threadpool(
        cuentas.scim_activar, org_id, user_id, activo, _rol(cuentas, org_id)
    )
    if not hecho:
        raise _Error(409, "es el último propietario de la organización", "mutability")
    await run_in_threadpool(
        cuentas.anotar, org_id, "", "scim_activar" if activo else "scim_desactivar", user_id, ""
    )
    await sincronizar_roles(cuentas, org_id, {user_id})


@router.put("/Users/{user_id}")
async def replace_user(request: Request, user_id: str) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        actual = await _uno(cuentas, org_id, user_id)
        datos = await _cuerpo(request)
        nombre = _nombre(datos)
        if nombre and nombre != actual["name"]:
            await run_in_threadpool(cuentas.renombrar, user_id, nombre)
        activo = _activo(datos.get("active", True))
        if activo != actual["active"]:
            await _poner_activo(cuentas, org_id, user_id, activo)
        return _respuesta(_recurso(request, await _uno(cuentas, org_id, user_id)))

    return await _con(request, hacer)


@router.patch("/Users/{user_id}")
async def patch_user(request: Request, user_id: str) -> JSONResponse:
    """Las dos formas en que llegan los cambios: con `path` (Okta) y sin él, con un
    objeto de valores (Entra ID). Lo que no sea el nombre o `active` se ignora."""

    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        actual = await _uno(cuentas, org_id, user_id)
        datos = await _cuerpo(request)
        operaciones = datos.get("Operations")
        if not isinstance(operaciones, list):
            raise _Error(400, "faltan las Operations", "invalidSyntax")
        cambios: dict[str, Any] = {}
        for op in operaciones:
            if not isinstance(op, dict) or str(op.get("op", "")).lower() not in (
                "replace", "add"
            ):
                continue
            ruta, valor = str(op.get("path") or ""), op.get("value")
            if ruta:
                cambios[ruta] = valor
            elif isinstance(valor, dict):
                cambios.update(valor)
        if "active" in cambios and _activo(cambios["active"]) != actual["active"]:
            await _poner_activo(cuentas, org_id, user_id, _activo(cambios["active"]))
        nombre = _nombre({
            "displayName": cambios.get("displayName"),
            "name": {
                "formatted": cambios.get("name.formatted"),
                "givenName": cambios.get("name.givenName"),
                "familyName": cambios.get("name.familyName"),
                **(cambios["name"] if isinstance(cambios.get("name"), dict) else {}),
            },
        })
        if nombre and nombre != actual["name"]:
            await run_in_threadpool(cuentas.renombrar, user_id, nombre)
        return _respuesta(_recurso(request, await _uno(cuentas, org_id, user_id)))

    return await _con(request, hacer)


@router.delete("/Users/{user_id}")
async def delete_user(request: Request, user_id: str) -> Any:
    async def hacer(cuentas: Any, org_id: str) -> Response:
        await _uno(cuentas, org_id, user_id)
        if not await run_in_threadpool(cuentas.scim_baja, org_id, user_id):
            raise _Error(409, "es el último propietario de la organización", "mutability")
        await run_in_threadpool(cuentas.anotar, org_id, "", "scim_baja", user_id, "")
        return Response(status_code=204)

    return await _con(request, hacer)


# ---------------------------------------------------------------------------------
# Groups (D-211): el proveedor dice quién está en cada uno; el rol, Laplace.
# ---------------------------------------------------------------------------------


def _recurso_grupo(request: Request, g: dict[str, Any]) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    recurso: dict[str, Any] = {
        "schemas": [ESQUEMA_GRUPO],
        "id": g["id"],
        "displayName": g["display_name"],
        "members": [
            {"value": uid, "$ref": f"{base}/scim/v2/Users/{uid}"} for uid in g["members"]
        ],
        "meta": {
            "resourceType": "Group",
            "created": g["created_at"],
            "location": f"{base}/scim/v2/Groups/{g['id']}",
        },
    }
    if g["external_id"]:
        recurso["externalId"] = g["external_id"]
    return recurso


async def _grupo(cuentas: Any, org_id: str, group_id: str) -> dict[str, Any]:
    g = await run_in_threadpool(cuentas.scim_grupo, org_id, group_id)
    if g is None:
        raise _Error(404, "no existe en esta organización")
    return g


def _ids(valor: Any) -> set[str]:
    if not isinstance(valor, list):
        raise _Error(400, "members tiene que ser una lista", "invalidValue")
    return {str(m["value"]) for m in valor if isinstance(m, dict) and m.get("value")}


async def _de_la_org(cuentas: Any, org_id: str, nuevos: set[str]) -> None:
    """Sólo gente de la organización: un `id` de otra es un 400 (D-211)."""
    if nuevos:
        suyos = {u["id"] for u in await run_in_threadpool(cuentas.scim_usuarios, org_id)}
        if nuevos - suyos:
            raise _Error(400, "un miembro no es de esta organización", "invalidValue")


async def _guardar_grupo(
    request: Request, cuentas: Any, org_id: str, group_id: str, nombre: str,
    external_id: str, miembros: set[str], antes: set[str],
) -> dict[str, Any]:
    if not nombre.strip():
        raise _Error(400, "falta el displayName", "invalidValue")
    await _de_la_org(cuentas, org_id, miembros - antes)
    cambiados = await run_in_threadpool(
        cuentas.scim_editar_grupo, org_id, group_id, nombre.strip(), external_id, miembros
    )
    await sincronizar_roles(cuentas, org_id, cambiados)
    return _recurso_grupo(request, await _grupo(cuentas, org_id, group_id))


@router.get("/Groups")
async def list_groups(
    request: Request, filter: str = "", startIndex: int = 1, count: int = 100  # noqa: A002, N803
) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        grupos = await run_in_threadpool(cuentas.scim_grupos, org_id)
        filtro = _filtro(filter, {"displayname": "display_name", "externalid": "external_id"})
        if filtro:
            grupos = [g for g in grupos if g[filtro[0]] == filtro[1]]
        return _lista(grupos, startIndex, count, lambda g: _recurso_grupo(request, g))

    return await _con(request, hacer)


@router.get("/Groups/{group_id}")
async def get_group(request: Request, group_id: str) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        return _respuesta(_recurso_grupo(request, await _grupo(cuentas, org_id, group_id)))

    return await _con(request, hacer)


@router.post("/Groups")
async def create_group(request: Request) -> JSONResponse:
    """Lo que el proveedor mande sobre roles se ignora: el rol lo pone Laplace."""

    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        datos = await _cuerpo(request)
        nombre = str(datos.get("displayName") or "").strip()
        if not nombre:
            raise _Error(400, "falta el displayName", "invalidValue")
        miembros = _ids(datos.get("members", []))
        external_id = str(datos.get("externalId") or "")
        # Antes de crearlo, para no dejar un grupo a medias.
        await _de_la_org(cuentas, org_id, miembros)
        group_id = await run_in_threadpool(cuentas.scim_crear_grupo, org_id, nombre, external_id)
        await run_in_threadpool(cuentas.anotar, org_id, "", "scim_grupo_alta", nombre, "")
        recurso = await _guardar_grupo(
            request, cuentas, org_id, group_id, nombre, external_id, miembros, set()
        )
        return _respuesta(recurso, 201)

    return await _con(request, hacer)


@router.put("/Groups/{group_id}")
async def replace_group(request: Request, group_id: str) -> JSONResponse:
    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        actual = await _grupo(cuentas, org_id, group_id)
        datos = await _cuerpo(request)
        return _respuesta(await _guardar_grupo(
            request, cuentas, org_id, group_id,
            str(datos.get("displayName") or actual["display_name"]),
            str(datos.get("externalId") or actual["external_id"]),
            _ids(datos.get("members", [])), set(actual["members"]),
        ))

    return await _con(request, hacer)


_MIEMBRO = re.compile(r'^members\[\s*value\s+eq\s+"([^"]*)"\s*\]$', re.IGNORECASE)


@router.patch("/Groups/{group_id}")
async def patch_group(request: Request, group_id: str) -> JSONResponse:
    """Okta añade y quita con `path` (`members[value eq "…"]` para quitar); Entra ID quita
    con una lista de valores, y cambia con un objeto de valores sin `path`."""

    async def hacer(cuentas: Any, org_id: str) -> JSONResponse:
        actual = await _grupo(cuentas, org_id, group_id)
        datos = await _cuerpo(request)
        operaciones = datos.get("Operations")
        if not isinstance(operaciones, list):
            raise _Error(400, "faltan las Operations", "invalidSyntax")
        nombre, external_id = actual["display_name"], actual["external_id"]
        miembros = set(actual["members"])
        for op in operaciones:
            if not isinstance(op, dict):
                continue
            tipo, ruta, valor = str(op.get("op", "")).lower(), str(op.get("path") or ""), op.get(
                "value")
            pares = valor.items() if not ruta and isinstance(valor, dict) else [(ruta, valor)]
            for campo, v in pares:
                quitar = _MIEMBRO.match(campo)
                if quitar and tipo == "remove":
                    miembros.discard(quitar.group(1))
                elif campo.lower() == "members":
                    if tipo == "remove":
                        miembros -= _ids(v) if v is not None else set(miembros)
                    elif tipo == "replace":
                        miembros = _ids(v)
                    elif tipo == "add":
                        miembros |= _ids(v)
                elif campo.lower() == "displayname" and tipo in ("replace", "add"):
                    nombre = str(v or "")
                elif campo.lower() == "externalid" and tipo in ("replace", "add"):
                    external_id = str(v or "")
        return _respuesta(await _guardar_grupo(
            request, cuentas, org_id, group_id, nombre, external_id, miembros,
            set(actual["members"]),
        ))

    return await _con(request, hacer)


@router.delete("/Groups/{group_id}")
async def delete_group(request: Request, group_id: str) -> Any:
    async def hacer(cuentas: Any, org_id: str) -> Response:
        g = await _grupo(cuentas, org_id, group_id)
        antes = await run_in_threadpool(cuentas.scim_borrar_grupo, org_id, group_id)
        await run_in_threadpool(
            cuentas.anotar, org_id, "", "scim_grupo_baja", g["display_name"], ""
        )
        await sincronizar_roles(cuentas, org_id, antes)
        return Response(status_code=204)

    return await _con(request, hacer)
