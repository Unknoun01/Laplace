"""Entrar con el proveedor de identidad de la organización, por OpenID Connect (D-182).

Okta, Entra ID, Google Workspace, OneLogin, Auth0, Keycloak y JumpCloud hablan OIDC, y
OIDC se puede hacer bien sin dependencias. SAML no está, y es a propósito: verificar una
firma XML a mano es justo donde los inicios de sesión se rompen (los ataques de
*signature wrapping*), y hacerlo bien pide `xmlsec`, una biblioteca nativa que no
queremos en cada instalación. Ver D-182.

Cómo se hace, y por qué así:

* **Flujo de código con secreto de cliente y PKCE.** El navegador nunca ve un token: el
  servidor canjea el código directamente contra el proveedor.
* **La firma del `id_token` no se comprueba, y el estándar lo permite**: OIDC Core
  3.1.3.7, punto 6. El token llega del endpoint de tokens por TLS, en una petición que
  el servidor hace con su secreto, así que el TLS ya dice quién lo emite. Sí se
  comprueban el emisor, la audiencia, la caducidad y el `nonce`. Por eso el endpoint de
  tokens tiene que ser `https`, y se exige.
* **`state` y `nonce` de un solo uso, y atados al navegador.** El `state` va en la base
  y además en una cookie de esa sesión de navegador. Sin la cookie, otro sitio podría
  terminar un inicio de sesión que empezó él y meterte en su cuenta.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("laplace.sso")

#: Dominios de correo de todo el mundo. Ninguna organización puede reclamarlos: el que
#: reclama `gmail.com` mandaría a su proveedor a cualquiera que escriba un Gmail.
DOMINIOS_PUBLICOS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "msn.com", "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com",
        "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net", "mail.com",
        "yandex.com", "zoho.com", "qq.com", "163.com", "hey.com", "fastmail.com",
    }
)

#: Lo que dura un inicio de sesión a medias, entre que sale hacia el proveedor y vuelve.
DURACION_ESTADO_S = 600
#: Holgura con el reloj del proveedor.
HOLGURA_S = 120
COOKIE_ESTADO = "laplace_sso"


class SSOError(Exception):
    """Algo no cuadra. `codigo` va a la URL de vuelta; el detalle, sólo al log."""

    def __init__(self, codigo: str, detalle: str = "") -> None:
        super().__init__(detalle or codigo)
        self.codigo = codigo


@dataclass
class ConfigSSO:
    org_id: str
    issuer: str
    client_id: str
    client_secret: str = ""
    default_role: str = "miembro"
    #: Los miembros con correo de estos dominios no pueden entrar con contraseña.
    enforce: bool = False
    domains: list[str] = field(default_factory=list)


def normalizar_dominio(dominio: str) -> str:
    return dominio.strip().lower().lstrip("@").rstrip(".")


def dominio_de(email: str) -> str:
    return normalizar_dominio(email.rpartition("@")[2])


def validar_dominio(dominio: str) -> str:
    """Un motivo si no vale, cadena vacía si vale."""
    d = normalizar_dominio(dominio)
    if not d or "." not in d or any(c in d for c in " /@:"):
        return "dominio no válido"
    if d in DOMINIOS_PUBLICOS:
        return "es un dominio público de correo"
    return ""


# ---------------------------------------------------------------------------------
# HTTP, sin dependencias. Se sustituye en las pruebas.
# ---------------------------------------------------------------------------------


def _https(url: str) -> str:
    if not url.startswith("https://"):
        raise SSOError("proveedor", f"el proveedor tiene que ir por https: {url}")
    return url


def _pedir(peticion: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(peticion, timeout=15) as r:  # noqa: S310 - https
            datos = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        cuerpo = exc.read()[:300].decode("utf-8", "replace")
        raise SSOError("proveedor", f"{exc.code} de {peticion.full_url}: {cuerpo}") from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise SSOError("proveedor", f"{peticion.full_url}: {exc}") from exc
    if not isinstance(datos, dict):
        raise SSOError("proveedor", f"{peticion.full_url} no devolvió un objeto")
    return datos


def _get(url: str) -> dict[str, Any]:
    return _pedir(urllib.request.Request(_https(url), headers={"Accept": "application/json"}))


def _post(url: str, datos: dict[str, str], usuario: str, secreto: str) -> dict[str, Any]:
    basico = base64.b64encode(
        f"{urllib.parse.quote(usuario, safe='')}:{urllib.parse.quote(secreto, safe='')}".encode()
    ).decode()
    return _pedir(
        urllib.request.Request(
            _https(url),
            data=urllib.parse.urlencode(datos).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Authorization": f"Basic {basico}",
            },
            method="POST",
        )
    )


# ---------------------------------------------------------------------------------
# El flujo
# ---------------------------------------------------------------------------------

_DESCUBIERTOS: dict[str, tuple[float, dict[str, Any]]] = {}


def descubrir(issuer: str) -> dict[str, Any]:
    """La configuración publicada del proveedor, una hora en memoria."""
    issuer = issuer.rstrip("/")
    guardado = _DESCUBIERTOS.get(issuer)
    if guardado and time.monotonic() - guardado[0] < 3600:
        return guardado[1]
    doc = _get(f"{_https(issuer)}/.well-known/openid-configuration")
    # El emisor que dice el documento tiene que ser el que se configuró: si no, el
    # documento es de otro y todo lo que se compruebe después contra él no vale nada.
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise SSOError("proveedor", f"el documento es de {doc.get('issuer')!r}, no de {issuer}")
    for clave in ("authorization_endpoint", "token_endpoint"):
        _https(str(doc.get(clave) or ""))
    _DESCUBIERTOS[issuer] = (time.monotonic(), doc)
    return doc


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


@dataclass
class Inicio:
    url: str
    state: str
    nonce: str
    verifier: str


def empezar(config: ConfigSSO, redirect_uri: str, login_hint: str = "") -> Inicio:
    doc = descubrir(config.issuer)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    reto = _b64(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": reto,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    separador = "&" if "?" in doc["authorization_endpoint"] else "?"
    url = f"{doc['authorization_endpoint']}{separador}{urllib.parse.urlencode(params)}"
    return Inicio(url=url, state=state, nonce=nonce, verifier=verifier)


def _payload(id_token: str) -> dict[str, Any]:
    try:
        _, cuerpo, _ = id_token.split(".")
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (ValueError, TypeError) as exc:
        raise SSOError("token", f"id_token ilegible: {exc}") from exc
    if not isinstance(datos, dict):
        raise SSOError("token", "id_token sin objeto")
    return datos


def comprobar_id_token(
    id_token: str, *, issuer: str, client_id: str, nonce: str, ahora: float | None = None
) -> dict[str, Any]:
    """Las comprobaciones de OIDC Core 3.1.3.7 que no son la firma (ver el módulo)."""
    claims = _payload(id_token)
    ahora = time.time() if ahora is None else ahora
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise SSOError("token", f"emisor {claims.get('iss')!r}")
    audiencia = claims.get("aud")
    audiencias = audiencia if isinstance(audiencia, list) else [audiencia]
    if client_id not in audiencias:
        raise SSOError("token", f"audiencia {audiencia!r}")
    if len(audiencias) > 1 and claims.get("azp") != client_id:
        raise SSOError("token", "varias audiencias y azp no es el cliente")
    try:
        exp = float(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SSOError("token", "sin caducidad") from exc
    if exp < ahora - HOLGURA_S:
        raise SSOError("token", "caducado")
    if float(claims.get("iat") or 0) > ahora + HOLGURA_S:
        raise SSOError("token", "emitido en el futuro")
    if not nonce or claims.get("nonce") != nonce:
        raise SSOError("token", "nonce")
    if not claims.get("sub"):
        raise SSOError("token", "sin sujeto")
    return claims


@dataclass
class Persona:
    subject: str
    email: str
    name: str


def terminar(
    config: ConfigSSO, *, code: str, verifier: str, nonce: str, redirect_uri: str
) -> Persona:
    """Canjea el código y dice quién es, con su correo dentro de los dominios."""
    doc = descubrir(config.issuer)
    tokens = _post(
        doc["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        config.client_id,
        config.client_secret,
    )
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise SSOError("token", "la respuesta no trae id_token")
    claims = comprobar_id_token(
        id_token, issuer=doc["issuer"], client_id=config.client_id, nonce=nonce
    )
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if "@" not in email:
        raise SSOError("correo", "el proveedor no manda correo")
    # Entra ID no manda `email_verified`; un `false` explícito sí se respeta.
    if claims.get("email_verified") is False:
        raise SSOError("correo", "el proveedor dice que el correo no está verificado")
    # El dominio lo aprobó el administrador de la instalación (D-182): es lo que hace
    # que la organización pueda hablar por esos correos.
    if dominio_de(email) not in config.domains:
        raise SSOError("dominio", f"{dominio_de(email)} no es de la organización")
    return Persona(subject=str(claims["sub"]), email=email, name=str(claims.get("name") or ""))
