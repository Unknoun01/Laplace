"""SCIM 2.0: el proveedor de identidad da de alta y de baja a la gente (D-182).

Lo que un proveedor (Okta, Entra ID, OneLogin, JumpCloud) necesita para mantener una
organización al día sin que nadie la toque a mano: buscar a alguien por su correo, darlo
de alta, cambiarle el nombre, desactivarlo y borrarlo. Nada más:

* **Sólo `Users`**, sin `Groups`. El rol de quien entra por SCIM es el rol por defecto
  del SSO de la organización, y se cambia en Laplace. Mapear grupos a roles es lo
  siguiente si alguien lo pide.
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


_FILTRO = re.compile(r'^\s*(userName|externalId)\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


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
            "schemas": [ESQUEMA_LISTA], "totalResults": 1, "Resources": [{
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User", "name": "User", "endpoint": "/Users", "schema": ESQUEMA_USUARIO,
            }],
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
        if filter:
            m = _FILTRO.match(filter)
            if not m:
                raise _Error(400, "sólo se filtra por userName o externalId con eq",
                             "invalidFilter")
            campo, valor = m.group(1).lower(), m.group(2)
            if campo == "username":
                usuarios = [u for u in usuarios if u["email"] == valor.strip().lower()]
            else:
                usuarios = [u for u in usuarios if u["external_id"] == valor]
        inicio = max(startIndex, 1)
        cuantos = min(max(count, 0), MAX_POR_PAGINA)
        pagina = usuarios[inicio - 1 : inicio - 1 + cuantos]
        return _respuesta({
            "schemas": [ESQUEMA_LISTA],
            "totalResults": len(usuarios),
            "startIndex": inicio,
            "itemsPerPage": len(pagina),
            "Resources": [_recurso(request, u) for u in pagina],
        })

    return await _con(request, hacer)


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
