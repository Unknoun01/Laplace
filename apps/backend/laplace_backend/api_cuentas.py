"""API de cuentas: entrar, configurar la instalación, invitar, miembros y claves (D-127).

Dos familias de rutas con reglas distintas:

* `/api/auth/*` atiende a quien todavía no tiene sesión. El middleware la deja pasar como
  anónima y cada ruta decide qué puede hacer: entrar con su contraseña, aceptar una
  invitación con su enlace, o configurar la instalación con el código del log.
* `/api/org/*` es de personas con sesión, y cada ruta comprueba el rol **en esa
  organización** con `_exigir_org`. No van por proyecto, así que la comprobación de rol
  por proyecto del middleware no les sirve y las deja pasar a ésta.

Las claves de API se crean aquí, desde la interfaz. `keys.py` decía que no debía existir
un endpoint así porque la primera credencial tendría que salir de algún sitio; ahora sale
del código de configuración, y crear claves pide sesión de admin de la organización.
"""

from __future__ import annotations

import hmac
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import AuthError, identity_of
from .auth import resolve as resolver_clave
from .cuentas import (
    COOKIE,
    COOKIE_CLAVE,
    DURACION_SESION,
    ROLES,
    comprobar_contrasena,
    normalizar_email,
    rol_suficiente,
    validar_contrasena,
)
from .textos import t

logger = logging.getLogger("laplace.api.cuentas")

router = APIRouter(prefix="/api")


def _cuentas(request: Request) -> Any:
    cuentas = getattr(request.app.state, "cuentas", None)
    if cuentas is None:
        raise HTTPException(status_code=503, detail=t("error.sin_cuentas"))
    return cuentas


def es_proxy_de_confianza(ip: str, confiables: frozenset[str]) -> bool:
    """Si `ip` es uno de los proxies declarados: una IP, un rango CIDR o un nombre.

    El nombre sirve para `docker compose`, donde la IP del contenedor `web` cambia en
    cada arranque pero su nombre no. Se resuelve en cada consulta: sólo se pregunta al
    entrar, aceptar una invitación o anotar en la auditoría, no en cada petición.
    """
    import ipaddress
    import socket

    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entrada in confiables:
        try:
            if direccion in ipaddress.ip_network(entrada, strict=False):
                return True
            continue
        except ValueError:
            pass
        try:
            resueltas = {i[4][0] for i in socket.getaddrinfo(entrada, None)}
        except OSError:
            continue
        if ip in resueltas:
            return True
    return False


def _por_proxy(request: Request) -> bool:
    """Si la conexión viene de un proxy declarado en `LAPLACE_TRUSTED_PROXIES`.

    Sólo entonces se cree a las cabeceras `X-Forwarded-*`. Antes se creían siempre, y
    cualquiera podía poner una IP distinta en cada intento: el freno por IP no frenaba y
    la auditoría apuntaba la IP que el atacante quisiera.
    """
    directa = request.client.host if request.client else ""
    confiables = request.app.state.settings.trusted_proxy_list
    return bool(directa and confiables) and es_proxy_de_confianza(directa, confiables)


def _ip(request: Request) -> str:
    directa = request.client.host if request.client else ""
    if not _por_proxy(request):
        return directa
    # El último salto es el que añadió nuestro proxy; los anteriores los pone el cliente
    # y pueden ser cualquier cosa.
    saltos = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
    saltos = [h for h in saltos if h]
    return saltos[-1] if saltos else directa


#: Para gastar el código de configuración una sola vez (ver `setup`).
_CERROJO_SETUP = threading.Lock()


def _local(request: Request) -> bool:
    return not request.app.state.settings.auth_enforced


def _poner_cookie(request: Request, response: Response, token: str) -> None:
    segura = request.url.scheme == "https" or (
        _por_proxy(request) and request.headers.get("x-forwarded-proto", "") == "https"
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(DURACION_SESION.total_seconds()),
        httponly=True,
        secure=segura,
        samesite="lax",
        path="/",
    )


def _usuario(request: Request) -> Any:
    """La persona con sesión, o 401. Una clave de API no es una persona."""
    identidad = identity_of(request)
    if not identidad.user_id:
        raise HTTPException(status_code=401, detail=t("error.solo_personas"))
    usuario = _cuentas(request).usuario(identidad.user_id)
    if usuario is None:
        raise HTTPException(status_code=401, detail=t("error.sesion_caducada"))
    return usuario


def _exigir_org(request: Request, org_id: str, necesita: str) -> Any:
    """La persona, si tiene al menos `necesita` en esa organización. Si no, 403.

    El administrador de la instalación puede en todas: es quien la mantiene.
    """
    usuario = _usuario(request)
    if usuario.is_admin:
        return usuario
    orgs = _cuentas(request).orgs_de(usuario.id)
    rol = next((o["role"] for o in orgs if o["id"] == org_id), None)
    if rol is None:
        # Igual que con los proyectos: no se dice si la organización existe.
        raise HTTPException(status_code=404, detail=t("error.org_no_encontrada"))
    if not rol_suficiente(rol, necesita):
        raise HTTPException(status_code=403, detail=t(f"error.rol_org.{necesita}"))
    return usuario


# ---------------------------------------------------------------------------------
# Quién soy
# ---------------------------------------------------------------------------------


@router.get("/auth/me")
async def me(request: Request) -> dict[str, Any]:
    """Lo que la interfaz necesita para saber qué pintar antes de pedir nada más."""
    if _local(request):
        return {"mode": "local", "user": None}
    cuentas = _cuentas(request)
    identidad = identity_of(request)
    if not identidad.user_id:
        return {
            "mode": "nube",
            "user": None,
            "needs_setup": not await run_in_threadpool(cuentas.hay_usuarios),
            "by_key": bool(identidad.key_id),
        }
    usuario = await run_in_threadpool(cuentas.usuario, identidad.user_id)
    orgs = await run_in_threadpool(cuentas.orgs_de, identidad.user_id)
    return {
        "mode": "nube",
        "user": {
            "id": usuario.id,
            "email": usuario.email,
            "name": usuario.name,
            "is_admin": usuario.is_admin,
        },
        "orgs": orgs,
        # El rol en cada proyecto, para que la interfaz no enseñe botones que el
        # backend va a rechazar. El backend sigue comprobándolo en cada escritura: esto
        # es para no ofrecer lo que no se puede, no para impedirlo.
        "roles": identidad.roles,
    }


# ---------------------------------------------------------------------------------
# Configurar la instalación
# ---------------------------------------------------------------------------------


class SetupIn(BaseModel):
    token: str
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=120)
    password: str
    org_name: str = Field(default="", max_length=120)


@router.post("/auth/setup")
async def setup(request: Request, response: Response, body: SetupIn) -> dict[str, Any]:
    """La primera cuenta, con el código que el servidor escribió en su log al arrancar.

    Adopta todos los proyectos que ya existan: son de quien mantiene la instalación, y
    dejarlos sin dueño los haría invisibles para todo el mundo salvo por clave.
    """
    if _local(request):
        raise HTTPException(status_code=404, detail=t("error.local_sin_cuentas"))
    cuentas = _cuentas(request)
    if await run_in_threadpool(cuentas.hay_usuarios):
        raise HTTPException(status_code=409, detail=t("error.ya_configurada"))
    # El código se gasta aquí, antes de crear nada, y bajo cerrojo: dos peticiones a la
    # vez con el código bueno ya no crean dos administradores (D-131). Si algo falla más
    # abajo se devuelve, para no dejar la instalación sin forma de configurarse.
    with _CERROJO_SETUP:
        esperado = getattr(request.app.state, "setup_token", "") or ""
        if not esperado or not hmac.compare_digest(body.token.strip(), esperado):
            raise HTTPException(status_code=403, detail=t("error.codigo_configuracion"))
        request.app.state.setup_token = ""
    try:
        return await _configurar_instalacion(request, response, body, cuentas)
    except BaseException:
        request.app.state.setup_token = esperado
        raise


async def _configurar_instalacion(
    request: Request, response: Response, body: SetupIn, cuentas: Any
) -> dict[str, Any]:
    motivo = validar_contrasena(body.password)
    if motivo:
        raise HTTPException(status_code=400, detail=motivo)
    if "@" not in body.email:
        raise HTTPException(status_code=400, detail=t("error.email_invalido"))

    def crear() -> tuple[Any, str]:
        usuario = cuentas.crear_usuario(body.email, body.name, body.password, is_admin=True)
        org_id = cuentas.crear_org(body.org_name or "Mi organización")
        cuentas.poner_miembro(org_id, usuario.id, "propietario")
        for p in request.app.state.store.list_projects():
            cuentas.asignar_proyecto(p.project_id, org_id)
        cuentas.anotar(org_id, usuario.id, "configurar_instalacion", body.email, _ip(request))
        return usuario, cuentas.abrir_sesion(usuario.id, request.headers.get("user-agent", ""))

    usuario, token = await run_in_threadpool(crear)
    _poner_cookie(request, response, token)
    logger.warning("instalación configurada — primera cuenta %s", usuario.email)
    return {"ok": True, "email": usuario.email}


# ---------------------------------------------------------------------------------
# Entrar y salir
# ---------------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


@router.post("/auth/login")
async def login(request: Request, response: Response, body: LoginIn) -> dict[str, Any]:
    if _local(request):
        raise HTTPException(status_code=404, detail=t("error.local_sin_cuentas"))
    cuentas = _cuentas(request)
    frenos = request.app.state.frenos
    email = normalizar_email(body.email)
    ip = _ip(request)
    claves = (f"email:{email}", f"ip:{ip}")
    if await run_in_threadpool(frenos.bloqueado, *claves):
        raise HTTPException(
            status_code=429,
            detail=t("error.demasiados_intentos"),
        )

    def comprobar() -> Any:
        encontrado = cuentas.usuario_por_email(email)
        # Se compara siempre, exista o no el email: el tiempo de respuesta no puede decir
        # qué correos tienen cuenta.
        guardado = encontrado[1] if encontrado else cuentas.hash_de("")
        valido = comprobar_contrasena(body.password, guardado)
        return encontrado[0] if encontrado and valido else None

    usuario = await run_in_threadpool(comprobar)
    if usuario is None:
        await run_in_threadpool(frenos.fallo, *claves)
        await run_in_threadpool(cuentas.anotar, "", "", "login_fallido", email, ip)
        raise HTTPException(status_code=401, detail=t("error.credenciales"))

    await run_in_threadpool(frenos.limpiar, f"email:{email}")
    token = await run_in_threadpool(
        cuentas.abrir_sesion, usuario.id, request.headers.get("user-agent", "")
    )
    # Entrar es un buen momento para barrer lo caducado: pasa a menudo, pero no en cada
    # petición, y no hace falta otro bucle en segundo plano para esto.
    try:
        await run_in_threadpool(cuentas.purgar_caducadas)
    except Exception:  # noqa: BLE001 - barrer no puede impedir entrar
        logger.warning("no se pudieron purgar las sesiones caducadas", exc_info=True)
    for org in await run_in_threadpool(cuentas.orgs_de, usuario.id):
        await run_in_threadpool(cuentas.anotar, org["id"], usuario.id, "login", "", ip)
    _poner_cookie(request, response, token)
    return {"ok": True, "email": usuario.email}


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE)
    if token and not _local(request):
        await run_in_threadpool(_cuentas(request).cerrar_sesion, token)
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(COOKIE_CLAVE, path="/")
    return {"ok": True}


class KeySessionIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)


@router.post("/auth/key")
async def key_session(request: Request, response: Response, body: KeySessionIn) -> dict[str, Any]:
    """Entrar a la interfaz con una clave de API en vez de con cuenta (D-130).

    La clave se comprueba aquí y se deja en una cookie `httpOnly`; la interfaz no la
    guarda en ningún sitio que JavaScript pueda leer. Antes vivía en `localStorage` y
    viajaba en `Authorization` desde el navegador: cualquier XSS se la llevaba, y una
    clave de API no caduca como una sesión.
    """
    clave = body.key.strip()
    try:
        identidad = await run_in_threadpool(resolver_clave, request.app.state.metadata, clave)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    segura = request.url.scheme == "https" or (
        _por_proxy(request) and request.headers.get("x-forwarded-proto", "") == "https"
    )
    response.set_cookie(
        COOKIE_CLAVE,
        clave,
        max_age=int(DURACION_SESION.total_seconds()),
        httponly=True,
        secure=segura,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "projects": sorted(identidad.projects)}


@router.delete("/auth/key")
async def forget_key(response: Response) -> dict[str, Any]:
    """Deja de usar la clave en este navegador."""
    response.delete_cookie(COOKIE_CLAVE, path="/")
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@router.post("/auth/password")
async def change_password(request: Request, body: PasswordIn) -> dict[str, Any]:
    """Cambiar la contraseña cierra las demás sesiones: si alguien la sabía, deja de
    servirle también la sesión que ya tuviera abierta."""
    usuario = await run_in_threadpool(_usuario, request)
    cuentas = _cuentas(request)
    guardado = await run_in_threadpool(cuentas.hash_de, usuario.id)
    # scrypt tarda décimas de segundo a propósito: fuera del bucle de eventos.
    if not await run_in_threadpool(comprobar_contrasena, body.current, guardado):
        raise HTTPException(status_code=403, detail=t("error.contrasena_actual"))
    motivo = validar_contrasena(body.new)
    if motivo:
        raise HTTPException(status_code=400, detail=motivo)
    await run_in_threadpool(cuentas.cambiar_contrasena, usuario.id, body.new)
    cerradas = await run_in_threadpool(
        cuentas.cerrar_todas, usuario.id, request.cookies.get(COOKIE)
    )
    return {"ok": True, "closed_sessions": cerradas}


@router.post("/auth/logout-all")
async def logout_all(request: Request, response: Response) -> dict[str, Any]:
    usuario = await run_in_threadpool(_usuario, request)
    cerradas = await run_in_threadpool(_cuentas(request).cerrar_todas, usuario.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True, "closed_sessions": cerradas}


# ---------------------------------------------------------------------------------
# Invitaciones
# ---------------------------------------------------------------------------------


@router.get("/auth/invitation")
async def invitation(request: Request, token: str) -> dict[str, Any]:
    """Lo que la pantalla de aceptar enseña: a qué organización y con qué rol."""
    info = await run_in_threadpool(_cuentas(request).invitacion, token)
    if info is None:
        raise HTTPException(status_code=404, detail=t("error.invitacion"))
    existe = await run_in_threadpool(_cuentas(request).usuario_por_email, info["email"])
    return {**info, "has_account": existe is not None}


class AcceptIn(BaseModel):
    token: str
    name: str = Field(default="", max_length=120)
    password: str = Field(max_length=256)


@router.post("/auth/accept")
async def accept(request: Request, response: Response, body: AcceptIn) -> dict[str, Any]:
    """Aceptar crea la cuenta, o —si ese email ya tenía una— pide su contraseña."""
    cuentas = _cuentas(request)
    info = await run_in_threadpool(cuentas.invitacion, body.token)
    if info is None:
        raise HTTPException(status_code=404, detail=t("error.invitacion"))

    existente = await run_in_threadpool(cuentas.usuario_por_email, info["email"])
    if existente is not None:
        # Aquí también se prueba una contraseña, así que lleva el mismo freno que entrar:
        # sin él, un enlace de invitación era un sitio donde probarlas sin límite.
        frenos = request.app.state.frenos
        claves = (f"email:{info['email']}", f"ip:{_ip(request)}")
        if await run_in_threadpool(frenos.bloqueado, *claves):
            raise HTTPException(
                status_code=429,
                detail=t("error.demasiados_intentos"),
            )
        usuario, guardado = existente
        if not await run_in_threadpool(comprobar_contrasena, body.password, guardado):
            await run_in_threadpool(frenos.fallo, *claves)
            raise HTTPException(status_code=403, detail=t("error.contrasena_cuenta"))
    else:
        motivo = validar_contrasena(body.password)
        if motivo:
            raise HTTPException(status_code=400, detail=motivo)
        usuario = await run_in_threadpool(
            cuentas.crear_usuario, info["email"], body.name, body.password
        )

    def unir() -> str | None:
        # Primero se gasta la invitación, y sólo si era la primera vez se hace miembro.
        if not cuentas.aceptar(body.token):
            return None
        # Una invitación no baja a nadie de rol: si ya era admin de esa organización y
        # acepta un enlace de lector, se queda admin.
        actual = cuentas.rol_en(info["org_id"], usuario.id)
        if not rol_suficiente(actual, info["role"]):
            cuentas.poner_miembro(info["org_id"], usuario.id, info["role"])
        cuentas.anotar(info["org_id"], usuario.id, "aceptar_invitacion", info["role"], _ip(request))
        return cuentas.abrir_sesion(usuario.id, request.headers.get("user-agent", ""))

    token = await run_in_threadpool(unir)
    if token is None:
        raise HTTPException(status_code=404, detail=t("error.invitacion"))
    _poner_cookie(request, response, token)
    return {"ok": True, "org_name": info["org_name"]}


# ---------------------------------------------------------------------------------
# La organización
# ---------------------------------------------------------------------------------


@router.get("/org")
async def get_org(request: Request, org_id: str) -> dict[str, Any]:
    """Miembros para todos; invitaciones, claves y proyectos para los admins."""
    usuario = await run_in_threadpool(_exigir_org, request, org_id, "lector")
    cuentas = _cuentas(request)
    rol = next(
        (
            o["role"]
            for o in await run_in_threadpool(cuentas.orgs_de, usuario.id)
            if o["id"] == org_id
        ),
        "propietario" if usuario.is_admin else "lector",
    )
    salida: dict[str, Any] = {
        "id": org_id,
        "name": await run_in_threadpool(cuentas.nombre_org, org_id),
        "role": rol,
        "members": await run_in_threadpool(cuentas.miembros, org_id),
        "projects": await run_in_threadpool(cuentas.proyectos_de_org, org_id),
    }
    if rol_suficiente(rol, "admin"):
        salida["invitations"] = await run_in_threadpool(cuentas.invitaciones, org_id)
        salida["keys"] = await run_in_threadpool(cuentas.claves, salida["projects"])
    return salida


class InviteIn(BaseModel):
    org_id: str
    email: str = Field(max_length=254)
    role: str = Field(pattern="^(lector|miembro|admin)$")


@router.post("/org/invitations")
async def invite(request: Request, body: InviteIn) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    if "@" not in body.email:
        raise HTTPException(status_code=400, detail=t("error.email_invalido"))
    cuentas = _cuentas(request)
    token = await run_in_threadpool(cuentas.invitar, body.org_id, body.email, body.role, usuario.id)
    base = (
        request.app.state.settings.alerts_base_url.rstrip("/")
        or str(request.base_url).rstrip("/")
    )
    enlace = f"{base}/invitacion?token={token}"
    await run_in_threadpool(
        cuentas.anotar,
        body.org_id,
        usuario.id,
        "invitar",
        f"{body.email} ({body.role})",
        _ip(request),
    )

    # Por correo si la instalación tiene servidor; si no, el enlace se enseña para
    # mandarlo a mano. El enlace sólo sale en esta respuesta: se guarda su hash.
    enviado = False
    notificador = request.app.state.alerts.mailer
    if notificador is not None:
        nombre = await run_in_threadpool(cuentas.nombre_org, body.org_id)
        enviado = await run_in_threadpool(
            notificador.send,
            body.email,
            f"Te han invitado a «{nombre}» en Laplace",
            f"{usuario.email} te ha invitado a «{nombre}» como {body.role}.\n\n"
            f"Acepta aquí (caduca en 7 días):\n{enlace}\n",
        )
    return {"link": enlace, "emailed": enviado}


@router.delete("/org/invitations")
async def cancel_invite(request: Request, org_id: str, email: str) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, org_id, "admin")
    anuladas = await run_in_threadpool(_cuentas(request).anular_invitacion, org_id, email)
    await run_in_threadpool(
        _cuentas(request).anotar, org_id, usuario.id, "anular_invitacion", email, _ip(request)
    )
    return {"cancelled": anuladas}


class MemberIn(BaseModel):
    org_id: str
    user_id: str
    role: str = Field(pattern="^(lector|miembro|admin|propietario)$")


def _propietarios(cuentas: Any, org_id: str) -> list[str]:
    return [m["user_id"] for m in cuentas.miembros(org_id) if m["role"] == "propietario"]


@router.put("/org/members")
async def set_role(request: Request, body: MemberIn) -> dict[str, Any]:
    """Cambiar un rol. Sólo un propietario hace o deshace propietarios, y la organización
    no se queda nunca sin ninguno: sería una organización que nadie puede administrar."""
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    cuentas = _cuentas(request)
    miembros = {m["user_id"]: m for m in await run_in_threadpool(cuentas.miembros, body.org_id)}
    if body.user_id not in miembros:
        raise HTTPException(status_code=404, detail=t("error.persona_no_en_org"))
    actual = miembros[body.user_id]["role"]
    toca_propietario = "propietario" in (actual, body.role)
    if toca_propietario:
        await run_in_threadpool(_exigir_org, request, body.org_id, "propietario")
    propietarios = await run_in_threadpool(_propietarios, cuentas, body.org_id)
    if actual == "propietario" and body.role != "propietario" and len(propietarios) == 1:
        raise HTTPException(status_code=409, detail=t("error.sin_propietario"))
    await run_in_threadpool(cuentas.poner_miembro, body.org_id, body.user_id, body.role)
    await run_in_threadpool(
        cuentas.anotar,
        body.org_id,
        usuario.id,
        "cambiar_rol",
        f"{miembros[body.user_id]['email']}: {actual} → {body.role}",
        _ip(request),
    )
    return {"ok": True}


@router.delete("/org/members")
async def remove_member(request: Request, org_id: str, user_id: str) -> dict[str, Any]:
    """Quitar a alguien, o irse uno mismo. Cierra sus sesiones: si no, seguiría viendo
    los datos hasta que caducara la cookie."""
    yo = await run_in_threadpool(_usuario, request)
    necesita = "lector" if user_id == yo.id else "admin"
    await run_in_threadpool(_exigir_org, request, org_id, necesita)
    cuentas = _cuentas(request)
    miembros = {m["user_id"]: m for m in await run_in_threadpool(cuentas.miembros, org_id)}
    if user_id not in miembros:
        raise HTTPException(status_code=404, detail=t("error.persona_no_en_org"))
    if miembros[user_id]["role"] == "propietario":
        if user_id != yo.id:
            await run_in_threadpool(_exigir_org, request, org_id, "propietario")
        if len(await run_in_threadpool(_propietarios, cuentas, org_id)) == 1:
            raise HTTPException(
                status_code=409, detail=t("error.sin_propietario")
            )
    await run_in_threadpool(cuentas.quitar_miembro, org_id, user_id)
    await run_in_threadpool(cuentas.cerrar_todas, user_id)
    await run_in_threadpool(
        cuentas.anotar, org_id, yo.id, "quitar_miembro", miembros[user_id]["email"], _ip(request)
    )
    return {"ok": True}


# ---------------------------------------------------------------------------------
# Claves de API
# ---------------------------------------------------------------------------------


class KeyIn(BaseModel):
    org_id: str
    project_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._\-]+$")
    name: str = Field(default="", max_length=120)
    #: 0 o ausente: no caduca.
    expires_days: int = Field(default=0, ge=0, le=3650)


@router.post("/org/keys")
async def create_key(request: Request, body: KeyIn) -> dict[str, Any]:
    """Una clave nueva para un proyecto de la organización. Si el proyecto no existía,
    nace aquí y es de esta organización; si es de otra, no."""
    usuario = await run_in_threadpool(_exigir_org, request, body.org_id, "admin")
    cuentas = _cuentas(request)
    if body.project_id == "*":
        raise HTTPException(status_code=400, detail=t("error.asterisco"))
    if not await run_in_threadpool(cuentas.asignar_proyecto, body.project_id, body.org_id):
        raise HTTPException(status_code=409, detail=t("error.proyecto_en_uso"))
    caduca = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_days)
        if body.expires_days
        else None
    )
    await run_in_threadpool(request.app.state.metadata.ensure_project, body.project_id)
    key_id, clave = await run_in_threadpool(
        cuentas.crear_clave, body.project_id, body.name or "sin nombre", usuario.email, caduca
    )
    await run_in_threadpool(
        cuentas.anotar, body.org_id, usuario.id, "crear_clave",
        f"{body.project_id} · {body.name}", _ip(request),
    )
    return {"id": key_id, "key": clave, "project_id": body.project_id}


@router.delete("/org/keys")
async def revoke_key(request: Request, org_id: str, key_id: str) -> dict[str, Any]:
    usuario = await run_in_threadpool(_exigir_org, request, org_id, "admin")
    cuentas = _cuentas(request)
    proyectos = await run_in_threadpool(cuentas.proyectos_de_org, org_id)
    if not await run_in_threadpool(cuentas.revocar_clave, key_id, proyectos):
        raise HTTPException(status_code=404, detail=t("error.clave_no_activa"))
    await run_in_threadpool(
        cuentas.anotar, org_id, usuario.id, "revocar_clave", key_id, _ip(request)
    )
    return {"ok": True}


@router.get("/org/audit")
async def audit(request: Request, org_id: str) -> dict[str, Any]:
    await run_in_threadpool(_exigir_org, request, org_id, "admin")
    return {"events": await run_in_threadpool(_cuentas(request).auditoria, org_id, 200)}


__all__ = ["router", "ROLES"]
