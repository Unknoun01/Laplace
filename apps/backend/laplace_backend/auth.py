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

    def __init__(self, app: Any, *, required: bool, metadata_getter: Any) -> None:
        super().__init__(app)
        self._required = required
        self._metadata = metadata_getter

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
            identidad = await self._identify(request)
            await self._check_scope(request, identidad)
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
        if not clave:
            raise AuthError(
                401,
                "falta la clave de API. Mándala en la cabecera "
                "«Authorization: Bearer lp_…»",
            )
        return resolve(self._metadata(), clave)

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
