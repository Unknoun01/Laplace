"""Quién puede escribir y quién puede leer.

Hasta aquí la instalación de nube era «quien llegue a la URL lee y escribe todo»,
incluidos los prompts y las respuestas en crudo de los usuarios finales de un cliente.
Este módulo lo cierra, y el diseño está gobernado por una idea: **la comprobación no
puede depender de que alguien se acuerde de ponerla en la ruta nueva**.

Por eso no hay un decorador que se aplique endpoint por endpoint. Hay un middleware que
**deniega por defecto**: todo lo que cuelga de `/api` y de `/v1/traces` exige credencial
salvo lo que esté en una lista blanca explícita y corta. Una ruta nueva nace protegida;
si alguien quiere abrirla, tiene que escribirlo en la lista y se ve en el diff. Es el
mismo criterio estructural que el de D-083: la regla no se cumple porque se recuerde,
sino porque el camino contrario no existe.

Tres decisiones más:

* **El modo local no tiene cuentas, y es una exención escrita.** No es que en local no
  haya nadie comprobando: es que `auth_required` vale `False` a propósito, se dice por
  el log al arrancar y hay un test que lo fija. La diferencia importa: lo primero se
  rompe solo el día que alguien despliegue el modo local en una máquina con IP pública.
* **Si no podemos verificar, denegamos.** El resto del producto se degrada cuando
  Postgres no responde —la ingesta y la lectura de trazas siguen—, pero la autenticación
  no se degrada: sin base de claves no hay forma de saber si una clave es buena, y
  «no lo sé» tiene que leerse como «no».
* **La clave ata el proyecto.** Una clave de ingesta sólo puede escribir spans de su
  proyecto. Si llegan spans de otro, la petición se rechaza entera con el motivo en vez
  de reetiquetarlos: reetiquetar escondería una configuración mal puesta, y el usuario
  descubriría dentro de un mes que su tráfico lleva semanas en el proyecto equivocado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("laplace.auth")

#: Prefijo visible de las claves. Sirve para reconocerlas en un log o en un `.env` y
#: para que un escáner de secretos pueda buscarlas.
KEY_PREFIX = "lp_"

#: Rutas que no piden credencial, exhaustivas y con motivo. Todo lo demás la pide.
#: `/health` queda fuera porque lo llama el orquestador antes de que nadie tenga clave,
#: y porque no dice nada de nadie: responde si el proceso está vivo.
PUBLIC_PATHS = frozenset({"/health"})

#: Las rutas de entrar: tienen que atender a quien todavía no tiene sesión. Cada una
#: decide por su cuenta qué puede hacer un anónimo —entrar, aceptar una invitación,
#: configurar la instalación con su código— y ninguna devuelve datos de proyectos (D-127).
AUTH_PREFIX = "/api/auth/"

#: Escrituras que piden ser admin de la organización del proyecto, además de miembro.
#: Lo que cambia quién recibe avisos, cuánto se puede gastar o borra datos de todos no es
#: cosa de cualquiera que pueda anotar una traza (D-127).
ADMIN_WRITES = (
    ("DELETE", "/api/projects"),
    ("PUT", "/api/budget"),
    ("PUT", "/api/alert-settings"),
    ("POST", "/api/alert-settings/test"),
)

_ESCRITURAS = ("POST", "PUT", "PATCH", "DELETE")

#: Lo que exige credencial. El resto —la interfaz estática— no toca datos.
GUARDED_PREFIXES = ("/api", "/v1/traces")

#: Proyecto comodín: una clave de instalación, que ve todos los proyectos. Existe
#: porque el operador de un despliegue propio necesita abrir la interfaz y verlo todo,
#: y porque todavía no hay cuentas ni organizaciones que modelen eso bien.
ALL_PROJECTS = "*"


class AuthError(Exception):
    """Credencial ausente, inválida o insuficiente."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Identity:
    """Quién está haciendo la petición y qué proyectos puede tocar.

    En modo abierto —local— es `Identity.open()`: puede con todo, y el nombre lo dice
    para que en un log se distinga de una clave de verdad.
    """

    key_id: str = ""
    name: str = ""
    projects: frozenset[str] = field(default_factory=frozenset)
    #: True cuando no se ha verificado nada porque la instalación es abierta.
    anonymous: bool = False
    #: Una persona con sesión, y no una clave (D-127). Vacío para claves y modo abierto.
    user_id: str = ""
    email: str = ""
    #: Su rol en cada proyecto que ve. Sólo existe para personas: una clave no tiene
    #: rol, tiene un proyecto, y su alcance ya lo acota eso.
    roles: dict[str, str] = field(default_factory=dict)
    #: Entró por cookie: sus escrituras piden la cabecera anti-CSRF.
    by_cookie: bool = False

    @classmethod
    def open(cls) -> Identity:
        return cls(key_id="", name="modo abierto", projects=frozenset({ALL_PROJECTS}),
                   anonymous=True)

    @property
    def sees_everything(self) -> bool:
        return ALL_PROJECTS in self.projects

    def allows(self, project_id: str) -> bool:
        return self.sees_everything or project_id in self.projects

    def require(self, project_id: str) -> None:
        """Levanta si esta identidad no puede tocar ese proyecto.

        El mensaje no dice si el proyecto existe. Contestar «ese proyecto no es tuyo»
        para uno y «no existe» para otro convierte la API en un directorio de los
        proyectos de los demás.
        """
        if not self.allows(project_id):
            raise AuthError(403, "esta clave no tiene acceso a ese proyecto")

    def scope(self, project_id: str | None) -> str | None:
        """El proyecto que hay que consultar de verdad.

        Cuando la clave es de un proyecto y la petición no dice cuál, se fija el suyo.
        Sin esto, una ruta que acepta `project_id` opcional —abrir una traza por su id,
        por ejemplo— buscaría en todos los proyectos de la instalación.
        """
        if project_id:
            self.require(project_id)
            return project_id
        if self.sees_everything:
            return None
        return next(iter(sorted(self.projects)), None)

    def visible(self, project_ids: list[str]) -> list[str]:
        return [p for p in project_ids if self.allows(p)]


# ---------------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------------


def generate_key() -> str:
    """Una clave nueva. 32 bytes de entropía: no se adivina ni a fuerza bruta."""
    return f"{KEY_PREFIX}{secrets.token_hex(32)}"


def hash_key(key: str) -> str:
    """SHA-256 en hexadecimal.

    Y no bcrypt ni argon2, que es lo que se usaría con una contraseña. Aquí el secreto
    lo generamos nosotros con 256 bits de entropía: no hay diccionario que probar, así
    que el coste de derivación no compra nada y sí traería una dependencia nueva al
    camino de cada petición. Lo que sí importa —que la clave en claro no se guarde
    nunca— se cumple igual.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fingerprint(key: str) -> str:
    """Los primeros caracteres, para poder hablar de una clave sin enseñarla."""
    return f"{key[:len(KEY_PREFIX) + 6]}…" if len(key) > len(KEY_PREFIX) + 6 else "…"


def bearer_from(request: Request) -> str | None:
    """La credencial de la petición, del sitio estándar o del alternativo.

    `Authorization: Bearer` es lo que manda el exportador OTLP y lo que manda la
    interfaz. `x-api-key` se acepta porque es lo que mandan varias herramientas de
    terceros y porque no cuesta nada; lo que no se acepta es la clave en la URL, que
    acaba en los logs de cualquier proxy por el que pase.
    """
    cabecera = request.headers.get("authorization", "")
    if cabecera.lower().startswith("bearer "):
        return cabecera[7:].strip() or None
    directa = request.headers.get("x-api-key", "").strip()
    return directa or None


def resolve(metadata: Any, key: str) -> Identity:
    """De una clave a una identidad, o error.

    Si el almacén de metadatos no responde no se deja pasar: sin base de claves no hay
    forma de saber si ésta es buena, y «no lo sé» tiene que leerse como «no».
    """
    try:
        fila = metadata.api_key_by_hash(hash_key(key))
    except Exception as exc:  # noqa: BLE001
        logger.exception("no se pudo verificar la clave")
        raise AuthError(503, "no se puede verificar la credencial ahora mismo") from exc

    if fila is None:
        raise AuthError(401, "credencial inválida")
    if fila.get("revoked_at"):
        raise AuthError(401, "esa clave está revocada")
    caduca = fila.get("expires_at")
    if caduca and str(caduca) <= datetime.now(timezone.utc).isoformat():
        raise AuthError(401, "esa clave ha caducado: crea otra en la pantalla de la organización")
    return Identity(
        key_id=fila["id"],
        name=fila.get("name") or fila["id"],
        projects=frozenset({fila["project_id"]}),
    )


# ---------------------------------------------------------------------------------
# El middleware: denegar por defecto
# ---------------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Autenticación y alcance de proyecto para todo lo que toca datos.

    Hace tres cosas, en este orden:

    1. **Autenticar.** Sin credencial válida no se pasa a nada que no esté en
       `PUBLIC_PATHS`. Una ruta nueva nace protegida.
    2. **Acotar por proyecto lo que viene en la URL.** Cualquier ruta con
       `?project_id=` queda comprobada sin que su autor tenga que acordarse.
    3. **Acotar por proyecto lo que viene en el cuerpo.** Las escrituras llevan
       `project_id` dentro del JSON —anotar, crear un conjunto, registrar una tirada,
       guardar un prompt—, así que el cuerpo se lee aquí, se comprueba y se deja
       cacheado para que el endpoint lo vuelva a leer sin coste.

    El paso 3 es el que evita el agujero clásico: proteger las lecturas, que son las
    obvias, y dejar abierta la escritura que alguien añadió el martes.
    """

    def __init__(
        self,
        app: Any,
        *,
        required: bool,
        metadata_getter: Any,
        cuentas_getter: Any = None,
    ) -> None:
        super().__init__(app)
        self._required = required
        self._metadata = metadata_getter
        self._cuentas = cuentas_getter or (lambda: None)
        #: Cuándo se apuntó por última vez el uso de cada clave. Se apunta como mucho
        #: cada cinco minutos: «último uso» sirve para saber si una clave está muerta, y
        #: no merece una escritura en cada petición de ingesta.
        self._usos: dict[str, float] = {}

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        ruta = request.url.path
        if not ruta.startswith(GUARDED_PREFIXES) or ruta in PUBLIC_PATHS:
            request.state.identity = Identity.open()
            return await call_next(request)

        # El preflight de CORS pasa sin credencial, y tiene que pasar: el navegador lo
        # manda **sin** cabeceras de autorización a propósito, así que pedírsela haría
        # que toda petición desde otro origen muriera antes de llegar a mandarla. No
        # abre nada: un `OPTIONS` no devuelve datos, sólo qué métodos se permiten.
        if request.method == "OPTIONS":
            request.state.identity = Identity(name="preflight")
            return await call_next(request)

        try:
            if ruta.startswith(AUTH_PREFIX):
                # Quien viene a entrar no tiene sesión todavía: se le identifica si puede
                # y, si no, pasa como anónimo. La ruta decide.
                identidad = await self._identify_optional(request)
                self._check_csrf(request, identidad, siempre=True)
            else:
                identidad = await self._identify(request)
                self._check_csrf(request, identidad)
                # Las rutas de la organización van por organización, no por proyecto:
                # crear la clave de un proyecto nuevo lleva en el cuerpo un proyecto que
                # todavía no es de nadie. Comprueban el rol en la organización ellas
                # mismas (`_exigir_org`), y a una clave de API no la atienden.
                if not ruta.startswith("/api/org"):
                    await self._check_scope(request, identidad)
                    await self._check_role(request, identidad)
        except AuthError as exc:
            # 401 lleva `WWW-Authenticate` porque es lo que dice el estándar y lo que
            # hace que un cliente sepa que le falta credencial y no que se ha roto algo.
            cabeceras = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
            return JSONResponse(
                status_code=exc.status, content={"detail": exc.detail}, headers=cabeceras
            )

        request.state.identity = identidad
        return await call_next(request)

    async def _identify(self, request: Request) -> Identity:
        if not self._required:
            return Identity.open()
        clave = bearer_from(request)
        if clave:
            identidad = resolve(self._metadata(), clave)
            await self._apuntar_uso(identidad.key_id)
            return identidad
        sesion = identity_from_session(self._cuentas(), request)
        if sesion is not None:
            return sesion
        raise AuthError(
            401,
            "inicia sesión, o manda una clave de API en la cabecera "
            "«Authorization: Bearer lp_…»",
        )

    async def _apuntar_uso(self, key_id: str) -> None:
        import time

        from starlette.concurrency import run_in_threadpool

        cuentas = self._cuentas()
        ahora = time.monotonic()
        if cuentas is None or not key_id or ahora - self._usos.get(key_id, -1e9) < 300:
            return
        self._usos[key_id] = ahora
        try:
            await run_in_threadpool(cuentas.usar_clave, key_id)
        except Exception:  # noqa: BLE001 - apuntar el uso no puede tumbar la petición
            logger.warning("no se pudo apuntar el uso de la clave %s", key_id, exc_info=True)

    async def _identify_optional(self, request: Request) -> Identity:
        try:
            return await self._identify(request)
        except AuthError as exc:
            if exc.status == 503:
                raise
            return Identity(name="anónimo")

    def _check_csrf(self, request: Request, identidad: Identity, *, siempre: bool = False) -> None:
        """Una escritura que viaja con cookie tiene que traer la cabecera propia.

        Un formulario de otro sitio puede mandar la cookie —el navegador la adjunta—, pero
        no puede poner una cabecera sin pasar por CORS, que sólo abre a nuestro origen.
        En las rutas de entrar se pide siempre: es lo que impide que otro sitio te meta
        en una cuenta suya con un formulario escondido (D-127).
        """
        if request.method not in _ESCRITURAS:
            return
        if not (identidad.by_cookie or siempre):
            return
        if not request.headers.get("x-laplace"):
            raise AuthError(403, "falta la cabecera X-Laplace en una escritura con sesión")

    async def _check_role(self, request: Request, identidad: Identity) -> None:
        """Lo que puede escribir una persona depende de su rol en ese proyecto.

        Lector: nada. Miembro: anotar, marcar, prompts, conjuntos. Admin: además alertas,
        presupuesto y borrar. Una escritura con sesión tiene que decir sobre qué proyecto
        es —en la URL o en el cuerpo—: sin eso no hay rol que mirar, y adivinarlo con «su
        primer proyecto» es la forma de que un lector de A escriba en A porque es miembro
        de B.
        """
        if request.method not in _ESCRITURAS:
            return
        # La organización no va por proyecto: sus rutas comprueban el rol en esa
        # organización con `_exigir_org` (api_cuentas.py).
        if request.url.path.startswith("/api/org"):
            return
        es_de_admin = any(
            request.method == metodo and request.url.path.startswith(ruta)
            for metodo, ruta in ADMIN_WRITES
        )
        if not identidad.user_id:
            # Una clave de proyecto escribe datos —spans, anotaciones, tiradas—, pero no
            # gobierna el proyecto. Esas claves viven en el entorno de los agentes de los
            # clientes, que es donde se filtran; con una de ellas se podía borrar el
            # proyecto entero o mandar sus alertas a otro sitio. Lo de admin pide una
            # persona con ese rol o la clave de instalación (y el modo local, que es
            # abierto a propósito, la tiene).
            if es_de_admin and not identidad.sees_everything:
                raise AuthError(
                    403,
                    "esto lo hace una persona con rol de admin desde la interfaz, no una "
                    "clave de API de proyecto",
                )
            return
        from .cuentas import rol_suficiente

        proyecto = request.query_params.get("project_id")
        if not proyecto and request.method in ("POST", "PUT", "PATCH"):
            try:
                datos = json.loads(await request.body() or b"{}")
                if isinstance(datos, dict) and isinstance(datos.get("project_id"), str):
                    proyecto = datos["project_id"]
            except (ValueError, UnicodeDecodeError):
                pass
        if identidad.sees_everything:
            return
        if not proyecto:
            raise AuthError(400, "esta escritura tiene que decir sobre qué proyecto es")
        rol = identidad.roles.get(proyecto)
        necesita = "admin" if es_de_admin else "miembro"
        if not rol_suficiente(rol, necesita):
            raise AuthError(
                403,
                f"tu rol en este proyecto no permite esto: hace falta ser {necesita}",
            )

    async def _check_scope(self, request: Request, identidad: Identity) -> None:
        if identidad.sees_everything:
            return

        pedido = request.query_params.get("project_id")
        if pedido:
            identidad.require(pedido)

        if request.method in ("POST", "PUT", "PATCH"):
            # Leer el cuerpo aquí es seguro: Starlette envuelve la petición en un
            # `_CachedRequest` justo para esto, y lo reproduce para el endpoint. Si se
            # hiciera a mano —tocando `_body`— el endpoint se quedaría esperando un
            # cuerpo que ya se había consumido.
            cuerpo = await request.body()
            if not cuerpo:
                return
            try:
                datos = json.loads(cuerpo)
            except (ValueError, UnicodeDecodeError):
                return  # no es JSON (OTLP va en protobuf y se comprueba en la ingesta)
            if isinstance(datos, dict) and isinstance(datos.get("project_id"), str):
                identidad.require(datos["project_id"])


def identity_from_session(cuentas: Any, request: Request) -> Identity | None:
    """De la cookie de sesión a una identidad, o `None` si no hay sesión válida."""
    from .cuentas import COOKIE

    token = request.cookies.get(COOKIE)
    if not token or cuentas is None:
        return None
    try:
        usuario = cuentas.usuario_de_sesion(token)
        if usuario is None:
            return None
        roles = {} if usuario.is_admin else cuentas.roles_por_proyecto(usuario.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("no se pudo verificar la sesión")
        raise AuthError(503, "no se puede verificar la sesión ahora mismo") from exc
    return Identity(
        name=usuario.email,
        user_id=usuario.id,
        email=usuario.email,
        # El administrador de la instalación ve todos los proyectos, como la clave '*'.
        projects=frozenset({ALL_PROJECTS}) if usuario.is_admin else frozenset(roles),
        roles=roles,
        by_cookie=True,
    )


def identity_of(request: Request) -> Identity:
    """La identidad de esta petición. Nunca `None`: el middleware siempre la pone."""
    return getattr(request.state, "identity", None) or Identity.open()


__all__ = [
    "ALL_PROJECTS",
    "AuthError",
    "AuthMiddleware",
    "Identity",
    "KEY_PREFIX",
    "PUBLIC_PATHS",
    "bearer_from",
    "fingerprint",
    "generate_key",
    "hash_key",
    "identity_of",
    "resolve",
]
