"""Cuentas de usuario: personas, organizaciones, roles y sesiones (D-127).

Hasta aquí la nube sólo sabía de claves de API: una clave por proyecto, creada desde la
línea de comandos, pegada en el navegador. Servía para que los datos de un cliente no los
leyera otro, y no servía para un equipo: nadie sabía quién había hecho qué, no había
forma de quitarle el acceso a una persona sin rotar la clave de todos, y la primera
pantalla de un producto de pago era «pega aquí tu clave».

Las cuentas no sustituyen a las claves: las claves siguen siendo cómo **escribe un
agente**, y las cuentas son cómo **entra una persona**. Las dos acaban en la misma
`Identity` del middleware, así que ninguna ruta tiene que saber de dónde viene quien
llama.

Decisiones que sostienen el resto:

* **Contraseñas con `scrypt`**, de la biblioteca estándar. Es una función lenta a
  propósito y no trae dependencias: el mismo criterio que D-068.
* **La sesión es un secreto aleatorio en una cookie `httpOnly`**, guardado como SHA-256.
  Si alguien lee la base, no puede entrar con lo que encuentra.
* **Toda escritura con cookie pide una cabecera propia** (`X-Laplace`). Un formulario de
  otro sitio no puede ponerla sin pasar por CORS, así que no hay CSRF que valga.
* **La primera cuenta sale de un código de un solo uso** que el servidor escribe en su
  log. Si la primera cuenta la creara quien primero llegara a la URL, desplegar sería
  una carrera para ver quién se queda con la instalación.
* **El modo local sigue sin cuentas**, por diseño y por escrito (D-010).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .textos import t

logger = logging.getLogger("laplace.cuentas")

#: De menos a más. Cada rol puede todo lo de los anteriores.
ROLES = ("lector", "miembro", "admin", "propietario")

COOKIE = "laplace_session"
#: La clave de API de quien entra a la interfaz con clave y no con cuenta (D-130). En una
#: cookie `httpOnly` y no en `localStorage`: JavaScript no puede leerla, así que un fallo
#: de XSS no se lleva una credencial que no caduca.
COOKIE_CLAVE = "laplace_key"
#: La cabecera que toda escritura con cookie tiene que traer (ver la cabecera del módulo).
CABECERA_CSRF = "x-laplace"
DURACION_SESION = timedelta(days=30)
DURACION_INVITACION = timedelta(days=7)
MIN_CONTRASENA = 10

#: Parámetros de scrypt: ~16 MiB y unas décimas de segundo por intento. Van dentro del
#: hash guardado, así que subirlos mañana no invalida las contraseñas de hoy.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def rol_suficiente(tiene: str | None, necesita: str) -> bool:
    if tiene not in ROLES:
        return False
    return ROLES.index(tiene) >= ROLES.index(necesita)


# ---------------------------------------------------------------------------------
# Secretos
# ---------------------------------------------------------------------------------


def hash_contrasena(contrasena: str) -> str:
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(
        contrasena.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${sal.hex()}${derivada.hex()}"


def comprobar_contrasena(contrasena: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, esperado = guardado.split("$")
        derivada = hashlib.scrypt(
            contrasena.encode("utf-8"),
            salt=bytes.fromhex(sal),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(esperado)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derivada.hex(), esperado)


#: Un hash de relleno para cuando el email no existe: se compara igual, y la respuesta
#: tarda lo mismo que con un email real. Sin esto, el tiempo de respuesta dice qué
#: correos tienen cuenta.
_RELLENO = hash_contrasena(secrets.token_hex(16))


def validar_contrasena(contrasena: str) -> str:
    """Un motivo si no vale, cadena vacía si vale. Longitud, y nada de reglas de
    símbolos: obligan a contraseñas peores, no mejores."""
    if len(contrasena) < MIN_CONTRASENA:
        return t("error.contrasena_corta", n=MIN_CONTRASENA)
    if len(contrasena) > 256:
        return t("error.contrasena_larga")
    return ""


def nuevo_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(fecha: datetime) -> str:
    return fecha.astimezone(timezone.utc).isoformat()


def _id(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:12]}"


def normalizar_email(email: str) -> str:
    return email.strip().lower()


# ---------------------------------------------------------------------------------
# Límite de intentos
# ---------------------------------------------------------------------------------


class Frenos:
    """Intentos fallidos de entrar, por email y por IP, en memoria (D-127).

    Es el freno de un solo proceso: el de las pruebas y el de antes de que haya base de
    cuentas. Con cuentas se usa `FrenosEnBase` (D-130), porque en memoria cada worker
    llevaba su cuenta y con cuatro el tope de cinco intentos era de veinte.
    """

    def __init__(self, maximo: int = 5, ventana: float = 900.0) -> None:
        self._maximo = maximo
        self._ventana = ventana
        self._fallos: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _vivos(self, clave: str, ahora: float) -> list[float]:
        return [t for t in self._fallos.get(clave, []) if ahora - t < self._ventana]

    def bloqueado(self, *claves: str) -> bool:
        ahora = time.monotonic()
        with self._lock:
            return any(len(self._vivos(c, ahora)) >= self._maximo for c in claves)

    def fallo(self, *claves: str) -> None:
        ahora = time.monotonic()
        with self._lock:
            for c in claves:
                self._fallos[c] = [*self._vivos(c, ahora), ahora]

    def limpiar(self, *claves: str) -> None:
        with self._lock:
            for c in claves:
                self._fallos.pop(c, None)


class FrenosEnBase:
    """Los mismos frenos que `Frenos`, contados en la base de cuentas (D-130).

    Todos los procesos de la instalación ven los mismos intentos, y sobreviven a un
    reinicio: reiniciar ya no es una forma de volver a tener cinco intentos. Mismos
    métodos que `Frenos`, así que las rutas no distinguen cuál tienen.
    """

    def __init__(self, cuentas: CuentasStore, maximo: int = 5, ventana: float = 900.0) -> None:
        self._cuentas = cuentas
        self._maximo = maximo
        self._ventana = timedelta(seconds=ventana)

    def bloqueado(self, *claves: str) -> bool:
        if not claves:
            return False
        desde = _iso(_ahora() - self._ventana)
        marcas = ", ".join("?" for _ in claves)
        filas = self._cuentas._filas(
            f"SELECT clave, COUNT(*) FROM login_failures "
            f"WHERE clave IN ({marcas}) AND at > ? GROUP BY clave",
            (*claves, desde),
        )
        return any(int(n) >= self._maximo for _, n in filas)

    def fallo(self, *claves: str) -> None:
        ahora = _iso(_ahora())
        for c in claves:
            self._cuentas._ejecutar(
                "INSERT INTO login_failures (clave, at) VALUES (?, ?)", (c, ahora)
            )

    def limpiar(self, *claves: str) -> None:
        for c in claves:
            self._cuentas._ejecutar("DELETE FROM login_failures WHERE clave = ?", (c,))


# ---------------------------------------------------------------------------------
# Almacén
# ---------------------------------------------------------------------------------


@dataclass
class Usuario:
    id: str
    email: str
    name: str
    is_admin: bool
    created_at: str


_DDL = """
CREATE TABLE IF NOT EXISTS orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,
    -- Administra la instalación entera: ve todos los proyectos y pone tarifas.
    is_admin       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    disabled_at    TEXT
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id      TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);
-- Un proyecto es de una organización. Nace al crear su primera clave desde la interfaz,
-- o se adopta al configurar la instalación.
CREATE TABLE IF NOT EXISTS org_projects (
    project_id  TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    user_agent  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invitations (
    token_hash   TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    accepted_at  TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);
-- Intentos fallidos de entrar, por email y por IP (D-130). En la base y no en memoria:
-- con varios procesos, cada uno llevaba su cuenta y el tope se multiplicaba por ellos.
CREATE TABLE IF NOT EXISTS login_failures (
    clave  TEXT NOT NULL,
    at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_idx ON login_failures (clave, at);
CREATE INDEX IF NOT EXISTS audit_org_idx ON audit_log (org_id, at);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
"""


class CuentasStore:
    """Personas, organizaciones, sesiones e invitaciones.

    Una sola implementación para SQLite y Postgres: las consultas son las mismas y sólo
    cambia el marcador de parámetros. Las fechas van como texto ISO en UTC en los dos,
    que se ordena y se compara bien, y así no hay dos lecturas de la misma columna.
    """

    marcador = "?"

    def _conn(self) -> Any:  # pragma: no cover - lo implementa cada almacén
        raise NotImplementedError

    def _sql(self, sql: str) -> str:
        return sql if self.marcador == "?" else sql.replace("?", "%s")

    def _filas(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._conn() as conn:
            return list(conn.execute(self._sql(sql), params).fetchall())

    def _ejecutar(self, sql: str, params: tuple = ()) -> int:
        with self._conn() as conn:
            return conn.execute(self._sql(sql), params).rowcount

    def migrate(self) -> None:
        with self._conn() as conn:
            for sentencia in (s.strip() for s in _DDL.split(";")):
                if sentencia:
                    conn.execute(sentencia)
        self._migrar_claves()

    def _migrar_claves(self) -> None:
        """Las claves ganan caducidad, autor y último uso (D-127)."""
        for columna in ("expires_at", "last_used_at", "created_by"):
            try:
                self._ejecutar(f"ALTER TABLE api_keys ADD COLUMN {columna} TEXT")
            except Exception:  # noqa: BLE001 - ya existe
                pass

    # -- instalación ---------------------------------------------------------------

    def hay_usuarios(self) -> bool:
        return bool(self._filas("SELECT 1 FROM users LIMIT 1"))

    # -- usuarios ------------------------------------------------------------------

    def crear_usuario(
        self, email: str, nombre: str, contrasena: str, *, is_admin: bool = False
    ) -> Usuario:
        usuario = Usuario(
            id=_id("u"),
            email=normalizar_email(email),
            name=nombre.strip(),
            is_admin=is_admin,
            created_at=_iso(_ahora()),
        )
        self._ejecutar(
            "INSERT INTO users (id, email, name, password_hash, is_admin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                usuario.id,
                usuario.email,
                usuario.name,
                hash_contrasena(contrasena),
                1 if is_admin else 0,
                usuario.created_at,
            ),
        )
        return usuario

    def _usuario(self, fila: tuple) -> Usuario:
        return Usuario(
            id=fila[0], email=fila[1], name=fila[2], is_admin=bool(fila[3]), created_at=fila[4]
        )

    def usuario_por_email(self, email: str) -> tuple[Usuario, str] | None:
        filas = self._filas(
            "SELECT id, email, name, is_admin, created_at, password_hash FROM users "
            "WHERE email = ? AND disabled_at IS NULL",
            (normalizar_email(email),),
        )
        return (self._usuario(filas[0]), filas[0][5]) if filas else None

    def usuario(self, user_id: str) -> Usuario | None:
        filas = self._filas(
            "SELECT id, email, name, is_admin, created_at FROM users "
            "WHERE id = ? AND disabled_at IS NULL",
            (user_id,),
        )
        return self._usuario(filas[0]) if filas else None

    def cambiar_contrasena(self, user_id: str, contrasena: str) -> None:
        self._ejecutar(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_contrasena(contrasena), user_id),
        )

    def hash_de(self, user_id: str) -> str:
        filas = self._filas("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        return filas[0][0] if filas else _RELLENO

    # -- organizaciones y proyectos ------------------------------------------------

    def crear_org(self, nombre: str) -> str:
        org_id = _id("org")
        self._ejecutar(
            "INSERT INTO orgs (id, name, created_at) VALUES (?, ?, ?)",
            (org_id, nombre.strip() or "Mi organización", _iso(_ahora())),
        )
        return org_id

    def nombre_org(self, org_id: str) -> str:
        filas = self._filas("SELECT name FROM orgs WHERE id = ?", (org_id,))
        return filas[0][0] if filas else ""

    def poner_miembro(self, org_id: str, user_id: str, rol: str) -> None:
        if rol not in ROLES:
            raise ValueError(f"rol desconocido: {rol}")
        if self._filas(
            "SELECT 1 FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user_id)
        ):
            self._ejecutar(
                "UPDATE memberships SET role = ? WHERE org_id = ? AND user_id = ?",
                (rol, org_id, user_id),
            )
        else:
            self._ejecutar(
                "INSERT INTO memberships (org_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
                (org_id, user_id, rol, _iso(_ahora())),
            )

    def quitar_miembro(self, org_id: str, user_id: str) -> bool:
        return (
            self._ejecutar(
                "DELETE FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user_id)
            )
            > 0
        )

    def miembros(self, org_id: str) -> list[dict[str, Any]]:
        filas = self._filas(
            "SELECT u.id, u.email, u.name, m.role, m.created_at FROM memberships m "
            "JOIN users u ON u.id = m.user_id WHERE m.org_id = ? AND u.disabled_at IS NULL "
            "ORDER BY m.created_at, u.email",
            (org_id,),
        )
        return [
            {"user_id": f[0], "email": f[1], "name": f[2], "role": f[3], "since": f[4]}
            for f in filas
        ]

    def orgs_de(self, user_id: str) -> list[dict[str, str]]:
        filas = self._filas(
            "SELECT o.id, o.name, m.role FROM memberships m JOIN orgs o ON o.id = m.org_id "
            "WHERE m.user_id = ? ORDER BY o.created_at, o.id",
            (user_id,),
        )
        return [{"id": f[0], "name": f[1], "role": f[2]} for f in filas]

    def proyectos_de_org(self, org_id: str) -> list[str]:
        return [
            f[0]
            for f in self._filas(
                "SELECT project_id FROM org_projects WHERE org_id = ? ORDER BY project_id",
                (org_id,),
            )
        ]

    def org_del_proyecto(self, project_id: str) -> str | None:
        filas = self._filas(
            "SELECT org_id FROM org_projects WHERE project_id = ?", (project_id,)
        )
        return filas[0][0] if filas else None

    def asignar_proyecto(self, project_id: str, org_id: str) -> bool:
        """Da el proyecto a la organización si no es de nadie. Falso si ya es de otra."""
        actual = self.org_del_proyecto(project_id)
        if actual is not None:
            return actual == org_id
        self._ejecutar(
            "INSERT INTO org_projects (project_id, org_id, created_at) VALUES (?, ?, ?)",
            (project_id, org_id, _iso(_ahora())),
        )
        return True

    def soltar_proyecto(self, project_id: str) -> None:
        self._ejecutar("DELETE FROM org_projects WHERE project_id = ?", (project_id,))

    def roles_por_proyecto(self, user_id: str) -> dict[str, str]:
        filas = self._filas(
            "SELECT p.project_id, m.role FROM memberships m "
            "JOIN org_projects p ON p.org_id = m.org_id WHERE m.user_id = ?",
            (user_id,),
        )
        return {f[0]: f[1] for f in filas}

    # -- sesiones ------------------------------------------------------------------

    def abrir_sesion(self, user_id: str, user_agent: str = "") -> str:
        token = nuevo_token()
        ahora = _ahora()
        self._ejecutar(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent) "
            "VALUES (?, ?, ?, ?, ?)",
            (hash_token(token), user_id, _iso(ahora), _iso(ahora + DURACION_SESION),
             user_agent[:200]),
        )
        return token

    def usuario_de_sesion(self, token: str) -> Usuario | None:
        filas = self._filas(
            "SELECT u.id, u.email, u.name, u.is_admin, u.created_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled_at IS NULL",
            (hash_token(token), _iso(_ahora())),
        )
        return self._usuario(filas[0]) if filas else None

    def cerrar_sesion(self, token: str) -> None:
        self._ejecutar("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    def cerrar_todas(self, user_id: str, salvo: str | None = None) -> int:
        if salvo:
            return self._ejecutar(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                (user_id, hash_token(salvo)),
            )
        return self._ejecutar("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # -- invitaciones --------------------------------------------------------------

    def invitar(self, org_id: str, email: str, rol: str, creado_por: str) -> str:
        if rol not in ROLES or rol == "propietario":
            raise ValueError("se invita como lector, miembro o admin")
        token = nuevo_token()
        ahora = _ahora()
        self._ejecutar(
            "INSERT INTO invitations (token_hash, org_id, email, role, created_by, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hash_token(token), org_id, normalizar_email(email), rol, creado_por, _iso(ahora),
             _iso(ahora + DURACION_INVITACION)),
        )
        return token

    def invitacion(self, token: str) -> dict[str, str] | None:
        filas = self._filas(
            "SELECT i.org_id, o.name, i.email, i.role FROM invitations i "
            "JOIN orgs o ON o.id = i.org_id "
            "WHERE i.token_hash = ? AND i.accepted_at IS NULL AND i.expires_at > ?",
            (hash_token(token), _iso(_ahora())),
        )
        if not filas:
            return None
        f = filas[0]
        return {"org_id": f[0], "org_name": f[1], "email": f[2], "role": f[3]}

    def aceptar(self, token: str) -> bool:
        """Gasta la invitación. `False` si ya estaba gastada o ha caducado.

        La condición va en la propia escritura: comprobar antes y marcar después dejaba
        que dos aceptaciones simultáneas del mismo enlace pasaran las dos.
        """
        return (
            self._ejecutar(
                "UPDATE invitations SET accepted_at = ? "
                "WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ?",
                (_iso(_ahora()), hash_token(token), _iso(_ahora())),
            )
            > 0
        )

    def rol_en(self, org_id: str, user_id: str) -> str | None:
        filas = self._filas(
            "SELECT role FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user_id)
        )
        return filas[0][0] if filas else None

    def purgar_caducadas(self) -> int:
        """Borra sesiones caducadas e invitaciones vencidas sin aceptar.

        No afecta a nada que funcione —las dos ya se ignoraban al leer—, pero sin esto
        las tablas sólo crecían: cada entrada deja una sesión de 30 días para siempre.
        """
        ahora = _iso(_ahora())
        sesiones = self._ejecutar("DELETE FROM sessions WHERE expires_at <= ?", (ahora,))
        invitaciones = self._ejecutar(
            "DELETE FROM invitations WHERE accepted_at IS NULL AND expires_at <= ?", (ahora,)
        )
        # Los intentos fallidos sólo cuentan un cuarto de hora; un día de margen sobra.
        viejos = _iso(_ahora() - timedelta(days=1))
        intentos = self._ejecutar("DELETE FROM login_failures WHERE at <= ?", (viejos,))
        return sesiones + invitaciones + intentos

    def invitaciones(self, org_id: str) -> list[dict[str, str]]:
        filas = self._filas(
            "SELECT email, role, created_at, expires_at FROM invitations "
            "WHERE org_id = ? AND accepted_at IS NULL AND expires_at > ? ORDER BY created_at",
            (org_id, _iso(_ahora())),
        )
        return [
            {"email": f[0], "role": f[1], "created_at": f[2], "expires_at": f[3]} for f in filas
        ]

    def anular_invitacion(self, org_id: str, email: str) -> int:
        return self._ejecutar(
            "DELETE FROM invitations WHERE org_id = ? AND email = ? AND accepted_at IS NULL",
            (org_id, normalizar_email(email)),
        )

    # -- claves de API --------------------------------------------------------------

    def crear_clave(
        self, project_id: str, nombre: str, creado_por: str, caduca: datetime | None
    ) -> tuple[str, str]:
        from .auth import generate_key, hash_key

        clave = generate_key()
        key_id = _id("ak")
        # En Postgres la clave apunta a `projects` con clave foránea y en SQLite no: el
        # proyecto se asegura aquí para que el almacén valga igual en los dos, y no sólo
        # cuando lo llama una ruta que se acordó de hacerlo antes.
        self._ejecutar(
            "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (id) DO NOTHING",
            (project_id, project_id, _iso(_ahora())),
        )
        self._ejecutar(
            "INSERT INTO api_keys (id, project_id, key_hash, name, created_at, expires_at, "
            "created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key_id, project_id, hash_key(clave), nombre.strip(), _iso(_ahora()),
             _iso(caduca) if caduca else None, creado_por),
        )
        return key_id, clave

    def claves(self, proyectos: list[str]) -> list[dict[str, Any]]:
        if not proyectos:
            return []
        marcas = ", ".join("?" for _ in proyectos)
        filas = self._filas(
            "SELECT id, project_id, name, created_at, revoked_at, expires_at, last_used_at, "
            f"created_by FROM api_keys WHERE project_id IN ({marcas}) ORDER BY created_at",
            tuple(proyectos),
        )
        return [
            {
                "id": f[0],
                "project_id": f[1],
                "name": f[2],
                "created_at": str(f[3]),
                "revoked_at": str(f[4]) if f[4] else None,
                "expires_at": f[5],
                "last_used_at": f[6],
                "created_by": f[7] or "",
            }
            for f in filas
        ]

    def revocar_clave(self, key_id: str, proyectos: list[str]) -> bool:
        if not proyectos:
            return False
        marcas = ", ".join("?" for _ in proyectos)
        return (
            self._ejecutar(
                f"UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL "
                f"AND project_id IN ({marcas})",
                (_iso(_ahora()), key_id, *proyectos),
            )
            > 0
        )

    def usar_clave(self, key_id: str) -> None:
        self._ejecutar(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_iso(_ahora()), key_id)
        )

    # -- auditoría -----------------------------------------------------------------

    def anotar(
        self, org_id: str, user_id: str, accion: str, objetivo: str = "", ip: str = ""
    ) -> None:
        try:
            self._ejecutar(
                "INSERT INTO audit_log (id, org_id, user_id, action, target, ip, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_id("ev"), org_id, user_id, accion, objetivo[:300], ip[:64], _iso(_ahora())),
            )
        except Exception:  # noqa: BLE001 - la auditoría no puede tumbar lo que audita
            logger.exception("no se pudo anotar en la auditoría: %s", accion)

    def auditoria(self, org_id: str, limite: int = 100) -> list[dict[str, str]]:
        filas = self._filas(
            "SELECT a.at, a.action, a.target, a.ip, COALESCE(u.email, '') FROM audit_log a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE a.org_id = ? "
            "ORDER BY a.at DESC, a.id DESC LIMIT ?",
            (org_id, limite),
        )
        return [
            {"at": f[0], "action": f[1], "target": f[2], "ip": f[3], "email": f[4]}
            for f in filas
        ]


class SQLiteCuentas(CuentasStore):
    """En el mismo fichero que los metadatos. Existe para las pruebas y para que el
    modelo sea el mismo en los dos sitios; el modo local no pide cuenta (D-010)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """Una conexión que se cierra al salir del `with` (D-131)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()


class PostgresCuentas(CuentasStore):
    marcador = "%s"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self) -> Any:
        # El mismo pool que los metadatos: cada petición con sesión lee la sesión y los
        # roles, y abrir dos conexiones nuevas por petición era lo más caro de entrar.
        from .storage._pg import conexion

        return conexion(self._dsn)

    def _migrar_claves(self) -> None:
        for columna in ("expires_at", "last_used_at", "created_by"):
            self._ejecutar(f"ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS {columna} TEXT")


def build(settings: Any) -> CuentasStore | None:
    """El almacén de cuentas de esta instalación, o `None` si no hay dónde guardarlas."""
    if settings.store == "sqlite":
        return SQLiteCuentas(settings.sqlite_path)
    if settings.postgres_enabled:
        return PostgresCuentas(settings.postgres_dsn)
    return None
