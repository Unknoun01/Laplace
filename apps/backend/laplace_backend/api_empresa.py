"""API de empresa: verificar el correo, entrar por SSO y las claves de SCIM (D-182).

Las rutas siguen las dos familias de `api_cuentas.py`: `/api/auth/*` atiende a quien
todavía no tiene sesión (empezar y terminar un SSO, gastar un enlace de verificación) y
`/api/org/*` es de personas con sesión, con el rol comprobado en esa organización.
SCIM, que habla el proveedor de identidad y no una persona, vive en `scim.py`. SAML
(D-211), en `saml.py`: aquí sólo empieza y vuelve, con el mismo `state` que OIDC.
"""

from __future__ import annotations

import logging
import secrets
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import saml, sso
from .api_cuentas import _cuentas, _exigir_org, _ip, _local, _poner_cookie, _usuario
from .scim import sincronizar_roles
from .textos import t

logger = logging.getLogger("laplace.api.empresa")

router = APIRouter(prefix="/api")


def _base(request: Request) -> str:
    """La URL pública: la de los enlaces de las invitaciones y los avisos."""
    return (
        request.app.state.settings.alerts_base_url.rstrip("/")
        or str(request.base_url).rstrip("/")
    )


def _mailer(request: Request) -> Any:
    alertas = getattr(request.app.state, "alerts", None)
    return getattr(alertas, "mailer", None) if alertas is not None else None


def _seguro(siguiente: str) -> str:
    """Sólo rutas propias: un `next` a otro sitio haría del SSO un redirector abierto."""
    if not siguiente.startswith("/") or siguiente.startswith("//") or "\\" in siguiente:
        return "/"
    return siguiente


# ---------------------------------------------------------------------------------
# Verificar el correo
# ---------------------------------------------------------------------------------


def mandar_verificacion(request: Request, usuario: Any) -> bool:
    """Manda el enlace si la instalación tiene correo. Falso si no se pudo."""
    mailer = _mailer(request)
    if mailer is None:
        return False
    token = _cuentas(request).token_verificacion(usuario.id, usuario.email)
    enlace = f"{_base(request)}/verificar?token={urllib.parse.quote(token)}"
    return bool(
        mailer.send(
            usuario.email,
            t("verificar.asunto"),
            t("verificar.cuerpo", enlace=enlace),
        )
    )


@router.post("/auth/verify/send")
async def verify_send(request: Request) -> dict[str, Any]:
    usuario = await run_in_threadpool(_usuario, request)
    cuentas = _cuentas(request)
    if await run_in_threadpool(cuentas.verificado, usuario.id):
        return {"ok": True, "verified": True, "sent": False}
    if _mailer(request) is None:
        raise HTTPException(status_code=503, detail=t("error.sin_correo"))
    # Mismo freno que entrar: si no, esto manda correos sin límite a quien sea.
    frenos = request.app.state.frenos
    clave = f"verificar:{usuario.id}"
    if await run_in_threadpool(frenos.bloqueado, clave):
        raise HTTPException(status_code=429, detail=t("error.demasiados_intentos"))
    await run_in_threadpool(frenos.fallo, clave)
    enviado = await run_in_threadpool(mandar_verificacion, request, usuario)
    return {"ok": enviado, "verified": False, "sent": enviado}


class VerifyIn(BaseModel):
    token: str = Field(max_length=200)


@router.post("/auth/verify")
async def verify(request: Request, body: VerifyIn) -> dict[str, Any]:
    if _local(request):
        raise HTTPException(status_code=404, detail=t("error.local_sin_cuentas"))
    user_id = await run_in_threadpool(_cuentas(request).verificar, body.token)
    if user_id is None:
        raise HTTPException(status_code=404, detail=t("error.enlace_verificacion"))
    return {"ok": True}


# ---------------------------------------------------------------------------------
# SSO: empezar y terminar
# ---------------------------------------------------------------------------------


class SSOStartIn(BaseModel):
    email: str = Field(max_length=254)
    next: str = Field(default="/", max_length=500)


@router.post("/auth/sso/start")
async def sso_start(request: Request, response: Response, body: SSOStartIn) -> dict[str, Any]:
    """Adónde ir a entrar con el proveedor de la organización de ese correo.

    Por el dominio del correo. Si no hay organización con ese dominio y SSO, 404, y la
    pantalla ofrece la contraseña: que un dominio tenga SSO no es un secreto (la propia
    pantalla de entrar de la organización lo dice), pero tampoco se lista.
    """
    if _local(request):
        raise HTTPException(status_code=404, detail=t("error.local_sin_cuentas"))
    cuentas = _cuentas(request)
    dominio = sso.dominio_de(body.email)
    org_id = await run_in_threadpool(cuentas.org_por_dominio, dominio)
    config = await run_in_threadpool(cuentas.config_sso, org_id) if org_id else None
    es_saml = config is not None and config.protocol == "saml"
    if config is None or not (config.idp_cert if es_saml else config.client_secret):
        raise HTTPException(status_code=404, detail=t("error.sin_sso"))
    if es_saml and not saml.disponible():
        raise HTTPException(status_code=503, detail=t("error.saml_sin_xmlsec"))
    try:
        if es_saml:
            # La petición SAML no lleva nonce ni PKCE: su `ID` va en la columna del nonce
            # y tiene que volver en el `InResponseTo`.
            state = secrets.token_urlsafe(32)
            url, request_id = await run_in_threadpool(
                saml.empezar, config, _base(request), state
            )
            inicio = sso.Inicio(url=url, state=state, nonce=request_id, verifier="")
        else:
            inicio = await run_in_threadpool(
                sso.empezar, config, f"{_base(request)}/api/auth/sso/callback",
                body.email.strip(),
            )
    except sso.SSOError as exc:
        logger.warning("sso: no se pudo empezar para %s: %s", org_id, exc)
        raise HTTPException(status_code=502, detail=t("error.sso_proveedor")) from exc
    navegador = secrets.token_urlsafe(32)
    await run_in_threadpool(
        cuentas.guardar_estado,
        inicio.state, config.org_id, inicio.nonce, inicio.verifier, navegador,
        _seguro(body.next),
    )
    segura = _base(request).startswith("https://")
    # SAML vuelve con un POST desde el proveedor, otro sitio: con `Lax` el navegador no
    # mandaría la cookie. `None` exige `Secure`, así que en http se queda en `Lax`.
    response.set_cookie(
        sso.COOKIE_ESTADO, navegador, max_age=sso.DURACION_ESTADO_S, httponly=True,
        secure=segura, samesite="none" if es_saml and segura else "lax", path="/api/auth/sso",
    )
    return {"url": inicio.url}


@router.get("/auth/sso/callback")
async def sso_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    """La vuelta del proveedor. Siempre redirige: a donde iba, o a entrar con el motivo."""
    if _local(request):
        return _fuera("local")
    cuentas = _cuentas(request)
    navegador = request.cookies.get(sso.COOKIE_ESTADO, "")
    estado = await run_in_threadpool(cuentas.gastar_estado, state, navegador) if state else None
    if estado is None:
        return _fuera("estado")
    if error or not code:
        return _fuera("cancelado")
    config = await run_in_threadpool(cuentas.config_sso, estado["org_id"])
    if config is None or config.protocol != "oidc":
        return _fuera("estado")
    try:
        persona = await run_in_threadpool(
            lambda: sso.terminar(
                config, code=code, verifier=estado["verifier"], nonce=estado["nonce"],
                redirect_uri=f"{_base(request)}/api/auth/sso/callback",
            )
        )
    except sso.SSOError as exc:
        return await _rechazo(request, config.org_id, exc)
    return await _abrir(request, config, persona, estado["next"])


def _fuera(codigo: str) -> RedirectResponse:
    r = RedirectResponse(f"/entrar?sso_error={codigo}", status_code=303)
    r.delete_cookie(sso.COOKIE_ESTADO, path="/api/auth/sso")
    return r


async def _rechazo(request: Request, org_id: str, exc: sso.SSOError) -> RedirectResponse:
    logger.warning("sso: %s rechazado: %s", org_id, exc)
    await run_in_threadpool(
        _cuentas(request).anotar, org_id, "", "sso_rechazado", exc.codigo, _ip(request)
    )
    return _fuera(exc.codigo)


async def _abrir(request: Request, config: Any, persona: Any, siguiente: str) -> RedirectResponse:
    """Lo común a OIDC y SAML una vez que el proveedor dijo quién es (D-182)."""
    cuentas = _cuentas(request)
    usuario, motivo = await run_in_threadpool(
        cuentas.usuario_sso, config.org_id, persona, config.default_role
    )
    if usuario is None:
        await run_in_threadpool(
            cuentas.anotar, config.org_id, "", "sso_rechazado", f"{motivo}: {persona.email}",
            _ip(request),
        )
        return _fuera(motivo)
    token = await run_in_threadpool(
        cuentas.abrir_sesion, usuario.id, request.headers.get("user-agent", "")
    )
    await run_in_threadpool(
        cuentas.anotar, config.org_id, usuario.id, "login_sso", "", _ip(request)
    )
    respuesta = RedirectResponse(_seguro(siguiente), status_code=303)
    _poner_cookie(request, respuesta, token)
    respuesta.delete_cookie(sso.COOKIE_ESTADO, path="/api/auth/sso")
    return respuesta


@router.post("/auth/sso/saml/acs")
async def saml_acs(request: Request) -> RedirectResponse:
    """La vuelta de SAML: un formulario que el navegador manda desde el proveedor.

    Sin la cabecera propia (viene de otro sitio), así que está en `PUBLIC_PATHS`: lo que
    la protege es la firma, el `state` de un solo uso atado a la cookie de este navegador,
    el `InResponseTo` de esa petición y el `ID` de la aserción gastado (D-211)."""
    if _local(request):
        return _fuera("local")
    cuerpo = urllib.parse.parse_qs((await request.body())[:500_000].decode("latin-1"))
    relay = (cuerpo.get("RelayState") or [""])[0]
    respuesta_saml = (cuerpo.get("SAMLResponse") or [""])[0]
    cuentas = _cuentas(request)
    navegador = request.cookies.get(sso.COOKIE_ESTADO, "")
    estado = await run_in_threadpool(cuentas.gastar_estado, relay, navegador) if relay else None
    if estado is None:
        return _fuera("estado")
    config = await run_in_threadpool(cuentas.config_sso, estado["org_id"])
    if config is None or config.protocol != "saml":
        return _fuera("estado")
    try:
        persona, asercion, caduca = await run_in_threadpool(
            saml.terminar, config, _base(request), respuesta_saml, estado["nonce"]
        )
        if not await run_in_threadpool(cuentas.gastar_asercion, config.org_id, asercion, caduca):
            raise sso.SSOError("token", f"aserción repetida: {asercion}")
    except sso.SSOError as exc:
        return await _rechazo(request, config.org_id, exc)
    return await _abrir(request, config, persona, estado["next"])


# ---------------------------------------------------------------------------------
# SSO y SCIM de una organización
# ---------------------------------------------------------------------------------


def _estado_sso(request: Request, org_id: str) -> dict[str, Any]:
    cuentas = _cuentas(request)
    config = cuentas.config_sso(org_id)
    base = _base(request)
    return {
        "configured": config is not None,
        "protocol": config.protocol if config else "oidc",
        "issuer": config.issuer if config else "",
        "client_id": config.client_id if config else "",
        # El secreto no vuelve nunca: sólo si hay uno.
        "has_secret": bool(config and config.client_secret),
        "default_role": config.default_role if config else "miembro",
        "enforce": bool(config and config.enforce),
        "domains": cuentas.dominios(org_id),
        "redirect_uri": f"{base}/api/auth/sso/callback",
        # D-211: lo que el administrador pega en su proveedor SAML, y si se puede.
        "sso_url": config.sso_url if config else "",
        "idp_cert": config.idp_cert if config else "",
        "saml_available": saml.disponible(),
        "saml_entity_id": saml.entity_id(base),
        "saml_acs_url": saml.acs(base),
        "scim_url": f"{base}/scim/v2",
        "scim_tokens": cuentas.tokens_scim(org_id),
    }


@router.get("/org/sso")
async def get_sso(request: Request, org_id: str) -> dict[str, Any]:
    await run_in_threadpool(_exigir_org, request, org_id, "admin")
    return await run_in_threadpool(_estado_sso, request, org_id)


class SSOIn(BaseModel):
    org_id: str
    issuer: str = Field(max_length=500)
    client_id: str = Field(max_length=500)
    #: `None` conserva el que había.
    client_secret: str | None = Field(default=None, max_length=2000)
    default_role: str = Field(default="miembro", pattern="^(lector|miembro|admin)$")
    enforce: bool = False
    protocol: str = Field(default="oidc", pattern="^(oidc|saml)$")
    #: SAML (D-211): `issuer` es el entityID del proveedor.
    sso_url: str = Field(default="", max_length=1000)
    idp_cert: str = Field(default="", max_length=20000)


@router.put("/org/sso")
async def put_sso(request: Request, body: SSOIn) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    issuer = body.issuer.strip().rstrip("/")
    if body.protocol == "saml":
        if not saml.disponible():
            raise HTTPException(status_code=400, detail=t("error.saml_sin_xmlsec"))
        # Un entityID es un identificador, no una URL que se pide: se guarda tal cual.
        issuer = body.issuer.strip()
        try:
            saml.comprobar_config(issuer, body.sso_url.strip(), body.idp_cert)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=t("error.saml_config", detalle=str(exc))
            ) from exc
    else:
        # Se comprueba ya, contra el proveedor: un emisor mal escrito se descubre aquí y
        # no el día que alguien intenta entrar.
        try:
            await run_in_threadpool(sso.descubrir, issuer)
        except sso.SSOError as exc:
            raise HTTPException(
                status_code=400, detail=t("error.sso_emisor", detalle=str(exc))
            ) from exc
    cuentas = _cuentas(request)
    if body.enforce and not await run_in_threadpool(cuentas.dominios, body.org_id):
        raise HTTPException(status_code=400, detail=t("error.sso_sin_dominios"))
    secreto = body.client_secret.strip() if body.client_secret else None
    await run_in_threadpool(
        lambda: cuentas.guardar_sso(
            body.org_id, issuer, body.client_id.strip(), secreto, body.default_role,
            body.enforce, protocolo=body.protocol, sso_url=body.sso_url.strip(),
            cert=body.idp_cert.strip(),
        )
    )
    await run_in_threadpool(
        cuentas.anotar, body.org_id, usuario.id, "configurar_sso", issuer, _ip(request)
    )
    return await run_in_threadpool(_estado_sso, request, body.org_id)


@router.delete("/org/sso")
async def delete_sso(request: Request, org_id: str) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, org_id, "admin")
    await run_in_threadpool(_cuentas(request).borrar_sso, org_id)
    await run_in_threadpool(_cuentas(request).anotar, org_id, usuario.id, "quitar_sso", "",
                            _ip(request))
    return await run_in_threadpool(_estado_sso, request, org_id)


class DomainsIn(BaseModel):
    org_id: str
    domains: list[str] = Field(max_length=50)


@router.put("/org/sso/domains")
async def put_domains(request: Request, body: DomainsIn) -> dict[str, Any]:
    """Sólo el administrador de la instalación (ver `sso_domains` en cuentas.py)."""
    usuario = await run_in_threadpool(_usuario, request)
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail=t("error.dominios_solo_instalacion"))
    try:
        await run_in_threadpool(
            _cuentas(request).poner_dominios, body.org_id, body.domains, usuario.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(
        _cuentas(request).anotar, body.org_id, usuario.id, "dominios_sso",
        ", ".join(body.domains), _ip(request),
    )
    return await run_in_threadpool(_estado_sso, request, body.org_id)


class ScimTokenIn(BaseModel):
    org_id: str


@router.post("/org/scim-tokens")
async def create_scim_token(request: Request, body: ScimTokenIn) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    token_id, token = await run_in_threadpool(
        _cuentas(request).crear_token_scim, body.org_id, usuario.id
    )
    await run_in_threadpool(
        _cuentas(request).anotar, body.org_id, usuario.id, "crear_token_scim", token_id,
        _ip(request),
    )
    # Sólo sale en esta respuesta: se guarda su hash.
    return {"id": token_id, "token": token}


def _grupos(request: Request, org_id: str) -> dict[str, Any]:
    return {"groups": [
        {"id": g["id"], "display_name": g["display_name"], "role": g["role"],
         "members": len(g["members"])}
        for g in _cuentas(request).scim_grupos(org_id)
    ]}


@router.get("/org/scim-groups")
async def get_scim_groups(request: Request, org_id: str) -> dict[str, Any]:
    await run_in_threadpool(_exigir_org, request, org_id, "admin")
    return await run_in_threadpool(_grupos, request, org_id)


class ScimGroupIn(BaseModel):
    org_id: str
    group_id: str
    #: '' es ninguno. Nunca propietario (D-211).
    role: str = Field(pattern="^(|lector|miembro|admin)$")


@router.put("/org/scim-groups")
async def put_scim_group(request: Request, body: ScimGroupIn) -> dict[str, Any]:
    """El rol que da un grupo del proveedor. Lo decide la organización, no el proveedor."""
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    cuentas = _cuentas(request)
    miembros = await run_in_threadpool(
        cuentas.rol_de_grupo, body.org_id, body.group_id, body.role
    )
    if miembros is None:
        raise HTTPException(status_code=404, detail=t("error.grupo_scim"))
    await run_in_threadpool(
        cuentas.anotar, body.org_id, usuario.id, "rol_grupo_scim",
        f"{body.group_id}: {body.role or '-'}", _ip(request),
    )
    await sincronizar_roles(cuentas, body.org_id, miembros)
    return await run_in_threadpool(_grupos, request, body.org_id)


@router.delete("/org/scim-tokens")
async def revoke_scim_token(request: Request, org_id: str, id: str) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, org_id, "admin")
    if not await run_in_threadpool(_cuentas(request).revocar_token_scim, org_id, id):
        raise HTTPException(status_code=404, detail=t("error.token_scim"))
    await run_in_threadpool(
        _cuentas(request).anotar, org_id, usuario.id, "revocar_token_scim", id, _ip(request)
    )
    return {"ok": True}
