"""El bot de pull requests: el arreglo de un hallazgo, propuesto en el código (D-185).

El Diagnóstico dice qué sobra y cuánto. El paso siguiente era del usuario: abrir el
fichero, encontrar la llamada y cambiarla. Aquí Laplace abre un pull request con el
cambio hecho, para que lo revise y lo fusione quien manda en el código.

Tres cuidados:

* **Sólo cambios mecánicos y comprobables.** Hoy, el cambio de modelo: el literal del
  modelo, en la llamada que el SDK anotó (`code.file.path`, `code.line.number`). Si el
  modelo no está escrito ahí —sale de una variable de entorno, de una tabla—, no se
  adivina dónde: se dice que no se puede proponer y por qué. Un PR que toca lo que no
  es resta más confianza de la que suma uno que acierta.
* **Nunca escribe en la rama principal.** Rama propia y pull request; fusionar es cosa
  de una persona. Si ya hay uno abierto para ese hallazgo, se devuelve ése.
* **Las credenciales no salen.** Como la clave de Stripe, el token se guarda con los
  ajustes del proyecto y no se devuelve nunca entero. Con la GitHub App, lo único que se
  guarda es el número de instalación: la clave privada es de la instalación de Laplace
  (`LAPLACE_GITHUB_APP_ID`, `LAPLACE_GITHUB_APP_PRIVATE_KEY`) y los tokens que firma
  duran una hora y no se guardan.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("laplace.github")

API = "https://api.github.com"
CLAVE = "github"
#: Prefijo del ajuste que recuerda el PR abierto de cada hallazgo.
PREFIJO_PR = "github_pr:"
#: Repositorio como lo escribe GitHub: `dueño/nombre`.
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
#: Cuántas líneas después de la anotada se busca el modelo: una llamada partida en
#: varias líneas lleva `model=` unas pocas más abajo.
VENTANA_LINEAS = 30

ATTR_RUTA = "code.file.path"
ATTR_LINEA = "code.line.number"

#: Quien habla con GitHub: `(método, url, token, cuerpo) -> json`. Se cambia en las pruebas.
Pedir = Callable[[str, str, str, dict[str, Any] | None], Any]


class GitHubError(Exception):
    """GitHub ha dicho que no, o no ha respondido. El mensaje es una clave de texto."""

    def __init__(self, clave: str, **datos: Any) -> None:
        super().__init__(clave)
        self.clave = clave
        self.datos = datos


class SinPropuesta(Exception):
    """No hay un cambio que proponer con seguridad. El mensaje es una clave de texto."""

    def __init__(self, clave: str, **datos: Any) -> None:
        super().__init__(clave)
        self.clave = clave
        self.datos = datos


class GitHubStatus(BaseModel):
    configured: bool = False
    repo: str = ""
    base_branch: str = ""
    #: `app` (instalación de la GitHub App) o `token`.
    auth: str = ""
    token_hint: str = ""
    installation_id: int | None = None
    #: Si esta instalación de Laplace tiene GitHub App (id y clave en el entorno).
    app_available: bool = False


# ---------------------------------------------------------------------------------
# Ajustes
# ---------------------------------------------------------------------------------


def repo_valido(repo: str) -> bool:
    return bool(_REPO.match(repo)) and ".." not in repo


def leer(metadata: Any, project_id: str) -> dict[str, Any]:
    try:
        return metadata.get_setting(project_id, CLAVE) or {}
    except Exception:  # noqa: BLE001
        return {}


def app_disponible() -> bool:
    return bool(os.environ.get("LAPLACE_GITHUB_APP_ID") and _clave_privada_app())


def _clave_privada_app() -> str:
    """La clave PEM de la App: en la variable, o en el fichero que diga la variable."""
    valor = os.environ.get("LAPLACE_GITHUB_APP_PRIVATE_KEY", "").strip()
    if valor and not valor.startswith("-----BEGIN"):
        try:
            with open(os.path.expanduser(valor), encoding="utf-8") as fichero:
                return fichero.read()
        except OSError:
            logger.warning("github: no se pudo leer la clave de la App en %s", valor)
            return ""
    return valor.replace("\\n", "\n")


def estado(metadata: Any, project_id: str) -> GitHubStatus:
    ajustes = leer(metadata, project_id)
    token = ajustes.get("token") or ""
    instalacion = ajustes.get("installation_id")
    auth = "app" if instalacion else ("token" if token else "")
    return GitHubStatus(
        configured=bool(ajustes.get("repo")) and bool(auth),
        repo=ajustes.get("repo") or "",
        base_branch=ajustes.get("base_branch") or "",
        auth=auth,
        token_hint=f"…{token[-4:]}" if token else "",
        installation_id=int(instalacion) if instalacion else None,
        app_available=app_disponible(),
    )


# ---------------------------------------------------------------------------------
# Hablar con GitHub
# ---------------------------------------------------------------------------------


def _pedir(metodo: str, url: str, token: str, cuerpo: dict[str, Any] | None = None) -> Any:
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    peticion = urllib.request.Request(
        url,
        data=datos,
        method=metodo,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "laplace-bot",
            **({"Content-Type": "application/json"} if datos else {}),
        },
    )
    try:
        with urllib.request.urlopen(peticion, timeout=20) as respuesta:  # noqa: S310
            texto = respuesta.read().decode("utf-8")
            return json.loads(texto) if texto else {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GitHubError("github.sin_permiso") from exc
        if exc.code == 404:
            raise GitHubError("github.no_encontrado") from exc
        if exc.code == 422:
            raise GitHubError("github.rechazado") from exc
        raise GitHubError("github.error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError("github.sin_respuesta") from exc


def _b64url(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def jwt_app(app_id: str, clave_pem: str, ahora: float | None = None) -> str:
    """El JWT con el que la App se presenta: RS256, diez minutos como mucho.

    `iat` va un minuto hacia atrás, como recomienda GitHub, por si el reloj de esta
    máquina adelanta un poco al suyo.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        raise GitHubError("github.falta_cryptography") from exc
    ahora = int(ahora if ahora is not None else time.time())
    cabecera = {"alg": "RS256", "typ": "JWT"}
    carga = {"iat": ahora - 60, "exp": ahora + 9 * 60, "iss": str(app_id)}
    firmado = ".".join(
        _b64url(json.dumps(parte, separators=(",", ":")).encode()) for parte in (cabecera, carga)
    )
    try:
        clave = serialization.load_pem_private_key(clave_pem.encode(), password=None)
        firma = clave.sign(firmado.encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
    except (ValueError, TypeError) as exc:
        raise GitHubError("github.clave_app_invalida") from exc
    return f"{firmado}.{_b64url(firma)}"


def token_de(ajustes: dict[str, Any], pedir: Pedir | None = None) -> str:
    """El token con el que se habla con el repositorio del proyecto."""
    pedir = pedir or _pedir
    instalacion = ajustes.get("installation_id")
    if instalacion:
        app_id = os.environ.get("LAPLACE_GITHUB_APP_ID", "")
        clave = _clave_privada_app()
        if not app_id or not clave:
            raise GitHubError("github.sin_app")
        respuesta = pedir(
            "POST",
            f"{API}/app/installations/{int(instalacion)}/access_tokens",
            jwt_app(app_id, clave),
            None,
        )
        return str(respuesta.get("token") or "")
    token = ajustes.get("token")
    if not token:
        raise GitHubError("github.sin_configurar")
    return str(token)


# ---------------------------------------------------------------------------------
# El cambio
# ---------------------------------------------------------------------------------


def ubicar(ruta_anotada: str, arbol: list[str]) -> str:
    """El fichero del repositorio que corresponde a la ruta que anotó el SDK.

    El SDK la anota relativa a la raíz del repositorio, al directorio de trabajo o como
    un sufijo, según lo que encontrase (D-185). Se busca por sufijo de tramos enteros:
    `agente/llamar.py` casa con `servicios/agente/llamar.py` y no con
    `servicios/otroagente/llamar.py`. Si casan varios, no se elige uno a ojo.
    """
    ruta = ruta_anotada.strip("/")
    if ruta in arbol:
        return ruta
    candidatos = [p for p in arbol if p.endswith("/" + ruta)]
    if len(candidatos) == 1:
        return candidatos[0]
    if not candidatos:
        raise SinPropuesta("pr.no_esta_en_repo", ruta=ruta)
    raise SinPropuesta("pr.ruta_ambigua", ruta=ruta, veces=len(candidatos))


def _literal(modelo: str) -> re.Pattern[str]:
    return re.compile(r"""(?P<q>["'])""" + re.escape(modelo) + r"(?P=q)")


def cambiar_modelo(fuente: str, linea: int, de: str, a: str) -> str:
    """El fichero con el modelo `de` cambiado por `a` en la llamada de `linea`.

    Se busca el literal entre comillas desde unas líneas antes de la anotada hasta
    `VENTANA_LINEAS` después, y se cambia el más cercano. Si ahí no está pero en todo el
    fichero hay **uno** solo (una constante `MODELO = "…"` arriba), se cambia ése. Con
    cero o con varios, no hay propuesta: elegir uno sería adivinar.
    """
    lineas = fuente.splitlines(keepends=True)
    patron = _literal(de)
    desde = max(0, linea - 4)
    hasta = min(len(lineas), linea + VENTANA_LINEAS)
    cerca = [i for i in range(desde, hasta) if patron.search(lineas[i])]
    if cerca:
        elegida = min(cerca, key=lambda i: (abs(i - (linea - 1)), i))
    else:
        todas = [i for i, texto in enumerate(lineas) if patron.search(texto)]
        if len(todas) != 1:
            raise SinPropuesta(
                "pr.modelo_no_literal" if not todas else "pr.modelo_varias_veces", modelo=de
            )
        elegida = todas[0]
    lineas[elegida] = patron.sub(lambda m: f"{m.group('q')}{a}{m.group('q')}", lineas[elegida], 1)
    return "".join(lineas)


def sitio(evidencia: list[Any], modelo: str) -> tuple[str, int]:
    """El fichero y la línea más repetidos entre las llamadas de ejemplo con ese modelo."""
    vistos: Counter[tuple[str, int]] = Counter()
    for span in evidencia:
        if getattr(span, "type", "llm") != "llm":
            # Los pasos de `@observe` anotan dónde está su función (D-190), no la llamada.
            continue
        attrs = getattr(span, "attributes", None) or {}
        llm = getattr(span, "llm", None)
        pedido = getattr(llm, "request_model", None) if llm else None
        if modelo and pedido and pedido != modelo:
            continue
        ruta, linea = attrs.get(ATTR_RUTA), attrs.get(ATTR_LINEA)
        if ruta and linea:
            try:
                vistos[(str(ruta), int(linea))] += 1
            except (TypeError, ValueError):
                continue
    if not vistos:
        raise SinPropuesta("pr.sin_sitio")
    return vistos.most_common(1)[0][0]


@dataclass
class Propuesta:
    ruta: str
    linea: int
    de: str
    a: str
    rama: str


def rama_para(finding_id: str) -> str:
    """Una rama por hallazgo, estable: pedirlo dos veces encuentra la misma."""
    return "laplace/" + hashlib.sha256(finding_id.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------------
# El pull request
# ---------------------------------------------------------------------------------


@dataclass
class PullRequest:
    url: str
    numero: int
    ya_abierto: bool = False


#: El cambio de un arreglo: `(fuente, ruta en el repositorio) -> fuente nueva`. Lanza
#: `SinPropuesta` si no se puede hacer con seguridad.
Cambio = Callable[[str, str], str]


def comprobar_cambio(fuente: str, nueva: str, ruta: str) -> None:
    """Lo que se exige a cualquier arreglo antes de escribirlo (D-190): que cambie algo
    y, si es Python, que el fichero siga compilando."""
    if nueva == fuente:
        raise SinPropuesta("pr.nada_que_cambiar")
    if ruta.endswith(".py"):
        try:
            ast.parse(nueva)
        except SyntaxError as exc:
            raise SinPropuesta("pr.no_compila", ruta=ruta) from exc


def abrir(
    ajustes: dict[str, Any],
    finding_id: str,
    *,
    ruta_anotada: str,
    titulo: str,
    cuerpo: str,
    mensaje: str,
    cambiar: Cambio | None = None,
    de: str = "",
    a: str = "",
    linea: int = 0,
    pedir: Pedir | None = None,
) -> PullRequest:
    """Rama, commit y pull request. Lo que no se pueda hacer con seguridad, no se hace.

    `cambiar` es el arreglo; sin él, el cambio de modelo de `de` a `a` en `linea`.
    """

    def _modelo(fuente: str, _ruta: str) -> str:
        return cambiar_modelo(fuente, linea, de, a)

    cambiar = cambiar or _modelo
    # Se resuelve al llamar, no al definir: así las pruebas cambian `_pedir` del módulo.
    pedir = pedir or _pedir
    repo = ajustes.get("repo") or ""
    if not repo_valido(repo):
        raise GitHubError("github.sin_configurar")
    token = token_de(ajustes, pedir)
    base = f"{API}/repos/{repo}"
    rama = rama_para(finding_id)
    dueño = repo.split("/", 1)[0]

    abiertos = pedir(
        "GET",
        f"{base}/pulls?state=open&head={urllib.parse.quote(f'{dueño}:{rama}')}",
        token,
        None,
    )
    if abiertos:
        return PullRequest(abiertos[0]["html_url"], int(abiertos[0]["number"]), ya_abierto=True)

    principal = ajustes.get("base_branch") or pedir("GET", base, token, None)["default_branch"]
    ref = pedir("GET", f"{base}/git/ref/heads/{urllib.parse.quote(principal)}", token, None)
    sha_base = ref["object"]["sha"]
    arbol = pedir("GET", f"{base}/git/trees/{sha_base}?recursive=1", token, None)
    rutas = [n["path"] for n in arbol.get("tree", []) if n.get("type") == "blob"]
    ruta = ubicar(ruta_anotada, rutas)

    fichero = pedir(
        "GET",
        f"{base}/contents/{urllib.parse.quote(ruta)}?ref={urllib.parse.quote(principal)}",
        token,
        None,
    )
    fuente = base64.b64decode(fichero["content"]).decode("utf-8")
    nueva = cambiar(fuente, ruta)
    comprobar_cambio(fuente, nueva, ruta)

    try:
        pedir("POST", f"{base}/git/refs", token, {"ref": f"refs/heads/{rama}", "sha": sha_base})
    except GitHubError as exc:
        # La rama ya existe (un intento anterior sin PR, o un PR cerrado): se
        # reaprovecha apuntándola otra vez a la base, que es lo que hubiera creado.
        if exc.clave != "github.rechazado":
            raise
        pedir(
            "PATCH",
            f"{base}/git/refs/heads/{rama}",
            token,
            {"sha": sha_base, "force": True},
        )
    pedir(
        "PUT",
        f"{base}/contents/{urllib.parse.quote(ruta)}",
        token,
        {
            "message": mensaje,
            "content": base64.b64encode(nueva.encode("utf-8")).decode("ascii"),
            "sha": fichero["sha"],
            "branch": rama,
        },
    )
    pr = pedir(
        "POST",
        f"{base}/pulls",
        token,
        {"title": titulo, "head": rama, "base": principal, "body": cuerpo},
    )
    return PullRequest(pr["html_url"], int(pr["number"]))
