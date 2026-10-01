"""Alertas a Slack cuando una regla supera su umbral (Fase 2, punto 3).

Una alerta de coste sólo sirve si se lee. Todo lo de aquí está escrito contra la forma
en la que estas cosas se rompen siempre: la herramienta avisa del mismo problema cada
cinco minutos, alguien silencia el canal, y a partir de ese momento tampoco se leen las
que sí importaban. Así que:

1. **Nunca se repite un hallazgo mientras siga ahí.** Cada uno tiene un periodo de
   calma; dentro de él no vuelve a sonar aunque el problema persista (D-074).
2. **Un proyecto, un mensaje.** Los hallazgos que vencen a la vez viajan juntos, no en
   seis notificaciones seguidas.
3. **El umbral es dinero ya gastado**, no dinero proyectado. Con minutos de datos no
   hay proyección que valga (D-073), pero sí hay una factura: se dispara sobre ella.
4. **Una cifra que puede quedarse corta se anuncia como suelo**, con el `≥` delante y
   la razón escrita. Afirmar un número exacto que el propio motor de precios sabe
   incompleto es la forma más rápida de que nadie se vuelva a creer una alerta.

Funciona igual en local que en la nube: es el mismo proceso con el estado en otro
sitio —SQLite en local, Postgres en la nube—, exactamente como el resto del producto.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import logging
import smtplib
import socket
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from . import cifras, idioma
from .config import Settings
from .insights import Finding, Overview, span_label, window_label
from .storage.base import Window
from .textos import t, tn

logger = logging.getLogger("laplace.alerts")

#: Cabecera de un webhook entrante de Slack. Se comprueba para no mandar la factura de
#: nadie a un host cualquiera por una errata en la configuración.
SLACK_HOST_SUFFIX = "slack.com"
#: Clave de los ajustes de alertas puestos desde la interfaz (D-123).
CLAVE_AJUSTES = "alerts"


# ---------------------------------------------------------------------------------
# Configuración: global por entorno, afinada por proyecto en un JSON
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectAlertConfig:
    """Los ajustes que gobiernan las alertas de un proyecto.

    El umbral y el silenciado son por proyecto porque un agente de juguete y uno de
    producción no tienen el mismo «esto merece que me despierten».
    """

    project_id: str
    webhook_url: str = ""
    #: Dinero YA GASTADO en la ventana a partir del cual un hallazgo alerta.
    min_usd: float = 1.0
    #: Horas de silencio por hallazgo. Es el freno principal contra la repetición.
    quiet_hours: float = 24.0
    #: Ventana de análisis sobre la que se evalúan las reglas.
    window_days: int = 7
    #: Silencio total del proyecto, sin tocar el resto de la configuración.
    muted: bool = False
    #: Reglas concretas que no alertan (`repeticion`, `modelo_caro`, `contexto_fijo`).
    muted_kinds: frozenset[str] = frozenset()
    #: Canales que se ponen desde la interfaz (D-123): un webhook cualquiera, que recibe
    #: JSON, y una dirección de correo. Slack sigue siendo `webhook_url`.
    generic_webhook_url: str = ""
    email_to: str = ""
    #: En qué idioma se escriben los avisos: el de quien guardó los ajustes por última
    #: vez (D-148). Se mandan desde un proceso de fondo, que no tiene petición de la que
    #: sacarlo.
    language: str = "es"

    @property
    def enabled(self) -> bool:
        canales = self.webhook_url or self.generic_webhook_url or self.email_to
        return bool(canales) and not self.muted


class AlertConfig:
    """La configuración completa: valores por entorno más un JSON por proyecto.

    El JSON existe porque «configurable por proyecto» con variables de entorno acaba
    siendo `LAPLACE_ALERTS_MIN_USD_MI_AGENTE`, que no se puede leer ni revisar. Un
    fichero se versiona, se diferencia en un PR y se explica solo:

        {
          "defaults": {"min_usd": 1.0, "quiet_hours": 24, "window_days": 7},
          "projects": {
            "cobros":  {"min_usd": 0.5, "quiet_hours": 6},
            "juguete": {"muted": true},
            "soporte": {"webhook_url": "https://hooks.slack.com/services/…",
                        "muted_kinds": ["repeticion"]}
          }
        }

    El webhook por defecto vive en el entorno y no en el fichero: es un secreto, y un
    secreto no se versiona.
    """

    def __init__(self, settings: Settings) -> None:
        self._base = ProjectAlertConfig(
            project_id="",
            # El webhook del entorno sólo cuenta con las alertas encendidas por entorno.
            # Los canales puestos desde la interfaz no lo necesitan: ponerlos ya es
            # encenderlas a propósito para ese proyecto (D-123).
            webhook_url=settings.alerts_slack_webhook if settings.alerts_enabled else "",
            min_usd=settings.alerts_min_usd,
            quiet_hours=settings.alerts_quiet_hours,
            window_days=settings.alerts_window_days,
        )
        self._por_proyecto: dict[str, dict[str, Any]] = {}
        self.base_url = settings.alerts_base_url.rstrip("/")
        self.path = Path(settings.alerts_config_path).expanduser() if (
            settings.alerts_config_path
        ) else None
        self.reload()

    def reload(self) -> None:
        """Relee el fichero. Un fichero roto no puede tumbar el backend."""
        if self.path is None or not self.path.exists():
            return
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("no se puede leer %s; se usan los valores por defecto", self.path)
            return
        self._base = _apply(self._base, datos.get("defaults") or {})
        self._por_proyecto = dict(datos.get("projects") or {})

    def for_project(
        self, project_id: str, desde_interfaz: dict[str, Any] | None = None
    ) -> ProjectAlertConfig:
        """Entorno, después el fichero, y encima lo que se puso en la interfaz.

        La interfaz manda porque es lo último que alguien ha dicho a propósito sobre
        ese proyecto, y es lo que la pantalla enseña como configuración en vigor.
        """
        ajustes = self._por_proyecto.get(project_id) or {}
        config = _apply(replace(self._base, project_id=project_id), ajustes)
        return _apply(config, desde_interfaz or {})

    def finding_url(self, project_id: str, finding: Finding, days: int) -> str:
        """Enlace directo a la ficha del problema, la misma que abre la interfaz.

        Sin `base_url` no hay enlace: se prefiere un mensaje sin enlace a un enlace
        inventado que lleve a ninguna parte.
        """
        if not self.base_url:
            return ""
        return (
            f"{self.base_url}/problema?project={quote(project_id)}&days={days}"
            f"&id={quote(finding.id)}"
        )


def _apply(base: ProjectAlertConfig, ajustes: dict[str, Any]) -> ProjectAlertConfig:
    """Aplica un diccionario de ajustes, ignorando lo que no reconoce."""
    cambios: dict[str, Any] = {}
    for clave, cast in (
        ("webhook_url", str),
        ("generic_webhook_url", str),
        ("email_to", str),
        ("min_usd", float),
        ("quiet_hours", float),
        ("window_days", int),
        ("muted", bool),
        ("language", str),
    ):
        if clave in ajustes:
            try:
                cambios[clave] = cast(ajustes[clave])
            except (TypeError, ValueError):
                logger.warning("valor inválido para %s en la configuración de alertas", clave)
    if "muted_kinds" in ajustes:
        cambios["muted_kinds"] = frozenset(ajustes["muted_kinds"] or ())
    return replace(base, **cambios)


# ---------------------------------------------------------------------------------
# Estado: qué se ha avisado ya, y cuándo
# ---------------------------------------------------------------------------------


@dataclass
class AlertRecord:
    """Lo que recordamos de un hallazgo ya avisado."""

    finding_id: str
    #: Cuándo se mandó la última alerta sobre él. Es lo que abre el periodo de calma.
    notified_at: datetime
    #: La última vez que la evaluación lo vio por encima del umbral. Si deja de verse
    #: durante un periodo de calma entero, se olvida y puede volver a avisar como nuevo.
    seen_at: datetime
    amount_usd: float = 0.0
    times: int = 0


_STATE_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS alert_state (
    project_id   TEXT NOT NULL,
    finding_id   TEXT NOT NULL,
    notified_at  TEXT NOT NULL,
    seen_at      TEXT NOT NULL,
    amount_usd   REAL NOT NULL DEFAULT 0,
    times        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, finding_id)
);
"""


class AlertState(Protocol):
    """Lo que hace falta recordar para no repetirse."""

    def read(self, project_id: str) -> dict[str, AlertRecord]: ...

    def save(self, project_id: str, record: AlertRecord) -> None: ...

    def forget(self, project_id: str, finding_ids: list[str]) -> None: ...


class SQLiteAlertState:
    """El estado en el mismo fichero que las trazas. Es el modo local.

    Conexión propia y no la del almacén: el estado de las alertas no es asunto del
    `SpanStore`, y meterle métodos de alertas a su protocolo lo convertiría en un cajón.
    SQLite en WAL aguanta de sobra dos conexiones al mismo fichero.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(_STATE_DDL_SQLITE)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Una conexión que se cierra al salir del `with` (D-131)."""
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()

    def read(self, project_id: str) -> dict[str, AlertRecord]:
        with self._conn() as conn:
            filas = conn.execute(
                "SELECT finding_id, notified_at, seen_at, amount_usd, times "
                "FROM alert_state WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return {
            f["finding_id"]: AlertRecord(
                finding_id=f["finding_id"],
                notified_at=datetime.fromisoformat(f["notified_at"]),
                seen_at=datetime.fromisoformat(f["seen_at"]),
                amount_usd=float(f["amount_usd"]),
                times=int(f["times"]),
            )
            for f in filas
        }

    def save(self, project_id: str, record: AlertRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO alert_state "
                "(project_id, finding_id, notified_at, seen_at, amount_usd, times) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    record.finding_id,
                    record.notified_at.isoformat(),
                    record.seen_at.isoformat(),
                    record.amount_usd,
                    record.times,
                ),
            )

    def forget(self, project_id: str, finding_ids: list[str]) -> None:
        if not finding_ids:
            return
        with self._conn() as conn:
            conn.executemany(
                "DELETE FROM alert_state WHERE project_id = ? AND finding_id = ?",
                [(project_id, fid) for fid in finding_ids],
            )


class PostgresAlertState:
    """El estado donde ya vive lo mutable en la nube."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_state (
                    project_id  TEXT NOT NULL,
                    finding_id  TEXT NOT NULL,
                    notified_at TIMESTAMPTZ NOT NULL,
                    seen_at     TIMESTAMPTZ NOT NULL,
                    amount_usd  DOUBLE PRECISION NOT NULL DEFAULT 0,
                    times       INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, finding_id)
                )
                """
            )

    def _connect(self) -> Any:
        from .storage._pg import conexion

        return conexion(self._dsn)

    def read(self, project_id: str) -> dict[str, AlertRecord]:
        with self._connect() as conn:
            filas = conn.execute(
                "SELECT finding_id, notified_at, seen_at, amount_usd, times "
                "FROM alert_state WHERE project_id = %s",
                (project_id,),
            ).fetchall()
        return {
            r[0]: AlertRecord(
                finding_id=r[0],
                notified_at=r[1],
                seen_at=r[2],
                amount_usd=float(r[3]),
                times=int(r[4]),
            )
            for r in filas
        }

    def save(self, project_id: str, record: AlertRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_state
                    (project_id, finding_id, notified_at, seen_at, amount_usd, times)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, finding_id) DO UPDATE SET
                    notified_at = EXCLUDED.notified_at,
                    seen_at     = EXCLUDED.seen_at,
                    amount_usd  = EXCLUDED.amount_usd,
                    times       = EXCLUDED.times
                """,
                (
                    project_id,
                    record.finding_id,
                    record.notified_at,
                    record.seen_at,
                    record.amount_usd,
                    record.times,
                ),
            )

    def forget(self, project_id: str, finding_ids: list[str]) -> None:
        if not finding_ids:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM alert_state WHERE project_id = %s AND finding_id = ANY(%s)",
                (project_id, list(finding_ids)),
            )


class MemoryAlertState:
    """Último recurso: el estado en memoria.

    Sirve para no dejar de alertar porque falte la base de datos, pero cada reinicio
    reabre el periodo de calma de todo, así que se avisa por el log. No es un modo de
    funcionamiento, es una degradación.
    """

    def __init__(self) -> None:
        self._datos: dict[str, dict[str, AlertRecord]] = {}

    def read(self, project_id: str) -> dict[str, AlertRecord]:
        return dict(self._datos.get(project_id, {}))

    def save(self, project_id: str, record: AlertRecord) -> None:
        self._datos.setdefault(project_id, {})[record.finding_id] = record

    def forget(self, project_id: str, finding_ids: list[str]) -> None:
        for fid in finding_ids:
            self._datos.get(project_id, {}).pop(fid, None)


def build_alert_state(settings: Settings) -> AlertState:
    """El estado va donde ya va lo mutable de cada modo.

    En local, el mismo fichero SQLite que las trazas. En la nube, Postgres, que es
    donde vive el resto de lo mutable. Si no hay ninguno, memoria y un aviso.
    """
    if settings.store == "sqlite":
        return SQLiteAlertState(settings.sqlite_path)
    if settings.postgres_enabled:
        try:
            return PostgresAlertState(settings.postgres_dsn)
        except Exception:  # noqa: BLE001
            logger.warning("no se puede usar postgres para el estado de las alertas", exc_info=True)
    logger.warning(
        "el estado de las alertas queda en memoria: tras un reinicio se puede repetir "
        "un aviso que ya se había mandado"
    )
    return MemoryAlertState()


# ---------------------------------------------------------------------------------
# Decisión: qué se avisa y qué se calla
# ---------------------------------------------------------------------------------


@dataclass
class Decision:
    """Lo que sale de evaluar un proyecto: qué mandar y qué recordar."""

    project_id: str
    #: Hallazgos que hay que anunciar ahora.
    due: list[Finding]
    #: Los que pasan el umbral pero están dentro de su periodo de calma. No se mandan;
    #: se cuentan en el mensaje para que se note que el silencio es deliberado.
    silenced: list[Finding]
    #: Hallazgos recordados que ya no aparecen ni siquiera por debajo del umbral, y
    #: que llevan un periodo de calma entero sin verse: se olvidan.
    forgotten: list[str]
    reason: str = ""
    #: Aviso de presupuesto que toca mandar (D-123), y su clave en el estado.
    budget_notice: str = ""
    budget_key: str = ""
    #: Clientes que han pasado a hacer perder dinero (D-161): el aviso, las claves que
    #: se recuerdan al mandarlo y las de los que ya no pierden, que se olvidan para que
    #: vuelva a sonar si recaen.
    customers_notice: str = ""
    customer_keys: list[str] = field(default_factory=list)
    customers_recovered: list[str] = field(default_factory=list)
    #: Los que ya se avisaron y siguen perdiendo: se les toca `seen_at` para que la
    #: limpieza de `decide()` no los olvide y vuelvan a sonar cada periodo de calma.
    customers_still: list[str] = field(default_factory=list)


def decide(
    overview: Overview,
    config: ProjectAlertConfig,
    state: dict[str, AlertRecord],
    now: datetime,
) -> Decision:
    """Decide, sin efectos: es la parte que se puede probar sin red ni reloj.

    Tres filtros, en este orden:

    1. **Umbral.** Dinero ya gastado en la ventana por encima de `min_usd`. Un hallazgo
       que sólo cuesta tiempo (`costs_money=False`) nunca cruza un umbral en dólares y
       por tanto nunca alerta: es deliberado, una alerta de coste habla de coste.
    2. **Silenciado.** Proyecto entero o reglas concretas.
    3. **Periodo de calma.** Si ya se avisó de ese hallazgo hace menos de `quiet_hours`,
       se calla, **aunque el problema siga ahí y aunque haya empeorado**. Avisar de que
       algo ha empeorado suena razonable y es exactamente la puerta por la que vuelve
       la repetición: cualquier cifra oscila.
    """
    calma = timedelta(hours=config.quiet_hours)
    candidatos = [
        f
        for f in overview.findings
        if f.costs_money
        and f.window_waste_usd >= config.min_usd
        and f.kind not in config.muted_kinds
    ]

    due: list[Finding] = []
    silenced: list[Finding] = []
    for finding in candidatos:
        previo = state.get(finding.id)
        if previo is not None and now - previo.notified_at < calma:
            silenced.append(finding)
        else:
            due.append(finding)

    # Un hallazgo se olvida cuando lleva un periodo de calma entero sin verse. No en
    # cuanto desaparece: una cifra que baila alrededor del umbral entraría y saldría
    # del estado, y cada reentrada contaría como «nuevo» y volvería a sonar.
    vivos = {f.id for f in candidatos}
    forgotten = [
        fid
        for fid, record in state.items()
        if fid not in vivos and now - record.seen_at >= calma
    ]

    return Decision(
        project_id=overview.project_id,
        due=due,
        silenced=silenced,
        forgotten=forgotten,
    )


# ---------------------------------------------------------------------------------
# Redacción del mensaje
# ---------------------------------------------------------------------------------


def money(amount: float) -> str:
    """El mismo criterio de decimales que la interfaz, para que no se contradigan.

    Esto era una **copia** del criterio, con su propio ladder de decimales, y el
    docstring de arriba ya decía la intención que la copia no cumplía: se quedó con el
    punto decimal inglés mientras la alerta escribía los millares en español, así que un
    mismo mensaje de Slack podía llevar «$1.234» y «$5.00» (D-120).
    """
    return cifras.dinero(amount)


def _amount_phrase(finding: Finding, ventana: str) -> str:
    """La frase de dinero de un hallazgo, con su ventana y su honestidad.

    Aquí se cumple la regla conservadora: si el coste del hallazgo está marcado como no
    fiable —modelo sin tarifa, o tarifa asumida— la cifra NO se afirma. Se anuncia con
    `≥`, que es lo único que sabemos con certeza: que se ha gastado al menos eso.
    """
    def importe(valor: float) -> str:
        return t("alerta.al_menos", x=money(valor)) if finding.cost_is_floor else money(valor)

    frase = t("alerta.importe", coste=importe(finding.window_waste_usd), ventana=ventana)
    if finding.monthly_saving_usd is not None:
        frase += t("alerta.al_mes", coste=importe(finding.monthly_saving_usd))
    if finding.cost_unverified:
        # No es un suelo: una tarifa de LiteLLM puede pasarse igual que quedarse corta.
        frase += t("alerta.sin_verificar")
    return frase


def compose(
    overview: Overview,
    decision: Decision,
    config: ProjectAlertConfig,
    urls: dict[str, str],
) -> str:
    """El mensaje de Slack, en mrkdwn.

    Un mensaje por proyecto con todo lo que vence, nunca uno por hallazgo: seis avisos
    seguidos se leen como spam y se silencia el canal, que es peor que no avisar.
    """
    ventana = window_label(overview.observed_days)
    cuantos = len(decision.due)
    cabecera = tn(
        "alerta.cabecera", cuantos, proyecto=overview.project_id, umbral=money(config.min_usd)
    )

    lineas = [cabecera, ""]
    for finding in decision.due:
        enlace = urls.get(finding.id, "")
        titulo = f"<{enlace}|{finding.title}>" if enlace else f"*{finding.title}*"
        lineas.append(f"• {titulo}")
        lineas.append(f"   {_amount_phrase(finding, ventana)}")
        if finding.scope_label:
            lineas.append(f"   {finding.scope_label} · {finding.difficulty_label}")

    notas: list[str] = []
    if any(f.cost_is_floor for f in decision.due):
        notas.append(t("alerta.nota.suelo"))
    if not overview.projected:
        notas.append(
            t(
                "alerta.nota.sin_proyeccion",
                hay=span_label(overview.observed_days),
                minimo=span_label(overview.min_days_for_projection),
            )
        )
    if decision.silenced:
        notas.append(
            tn(
                "alerta.nota.calma",
                len(decision.silenced),
                calma=span_label(config.quiet_hours / 24),
            )
        )

    if notas:
        lineas.append("")
        lineas.extend(f"_{n}_" for n in notas)
    return "\n".join(lineas)


# ---------------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------------


class SlackNotifier:
    """Un POST a un webhook entrante. Sin dependencias nuevas, a propósito (D-068)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def send(self, webhook_url: str, text: str) -> bool:
        if not _looks_like_slack(webhook_url):
            logger.error(
                "el webhook configurado no parece de Slack; no se manda nada. "
                "Un error de configuración no puede acabar publicando el gasto de "
                "alguien en un host cualquiera."
            )
            return False
        cuerpo = json.dumps({"text": text}).encode("utf-8")
        peticion = urllib.request.Request(  # noqa: S310 - el esquema se valida arriba
            webhook_url,
            data=cuerpo,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _SIN_REDIRECCIONES.open(peticion, timeout=self._timeout) as respuesta:
                return 200 <= respuesta.status < 300
        except urllib.error.URLError as exc:
            logger.warning("no se ha podido avisar a Slack: %s", exc)
            return False


class WebhookNotifier:
    """Un POST con JSON a un webhook cualquiera: Teams, Discord, un n8n propio (D-123).

    HTTPS obligatorio salvo contra la propia máquina. El cuerpo lleva el texto ya
    redactado y el proyecto, que es lo mínimo para enrutarlo al otro lado.
    """

    def __init__(self, timeout: float = 10.0, *, permitir_local: bool = False) -> None:
        self._timeout = timeout
        #: Sólo en modo local: ahí el webhook de un n8n en el portátil es legítimo, y
        #: la máquina es de quien lo configura.
        self._permitir_local = permitir_local

    def send(self, url: str, text: str, project_id: str) -> bool:
        motivo, ips = resolver_destino(url, permitir_local=self._permitir_local)
        if motivo:
            logger.error("webhook de %s rechazado, no se manda nada: %s", project_id, motivo)
            return False
        cuerpo = json.dumps(
            {"text": text, "content": text, "project": project_id, "source": "laplace"},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            estado = post_a_ip(url, ips, cuerpo, timeout=self._timeout)
        except OSError as exc:
            logger.warning("no se ha podido avisar al webhook: %s", exc)
            return False
        if 300 <= estado < 400:
            logger.warning("el webhook redirige a otro sitio (%s); no se sigue", estado)
        return 200 <= estado < 300


# ---------------------------------------------------------------------------------
# A dónde se puede mandar (SSRF)
#
# La URL de un webhook la pone quien administra un proyecto, y quien la usa es el
# servidor, desde dentro de la red del despliegue. Sin comprobar, un webhook
# `https://10.0.0.5/…`, `https://clickhouse/…` o uno público que redirige a
# `http://169.254.169.254/…` convertía el botón de «probar» en una forma de tocar
# servicios internos, con `delivered` de oráculo. Tres barreras:
#
# 1. Al guardar, sin red: https, con host, y que no sea una IP privada ni «localhost».
# 2. Al enviar: el nombre se resuelve y **todas** sus direcciones tienen que ser
#    públicas. Es lo que para a un nombre público que apunta a una IP interna.
# 3. Las redirecciones no se siguen. Un webhook contesta; no manda a otro sitio.
#
# 4. Se conecta a la IP que se ha comprobado, no al nombre otra vez (D-130). Si no, entre
#    comprobar y conectar el DNS podía cambiar de respuesta —DNS rebinding— y la segunda
#    resolución llevaba a la red interna. El nombre se sigue usando para TLS (SNI y
#    certificado) y para la cabecera Host, así que el otro lado no nota nada.
# ---------------------------------------------------------------------------------


class _NoSeguirRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"el webhook redirige a otro sitio ({newurl}); no se sigue",
            headers,
            fp,
        )


_SIN_REDIRECCIONES = urllib.request.build_opener(_NoSeguirRedirecciones)

_NOMBRES_LOCALES = ("localhost", "localhost.localdomain", "ip6-localhost")


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _es_local(host: str) -> bool:
    ip = _ip_literal(host)
    return host.lower() in _NOMBRES_LOCALES or (ip is not None and ip.is_loopback)


def webhook_valido(url: str, *, permitir_local: bool = False) -> bool:
    """La comprobación de guardar: de forma, sin tocar la red.

    En modo local se admite además `http(s)` contra la propia máquina. En la nube, sólo
    https hacia un host que no sea, a la vista, interno.
    """
    from urllib.parse import urlparse

    partes = urlparse(url)
    host = partes.hostname or ""
    if not host or partes.scheme not in ("http", "https"):
        return False
    if permitir_local and _es_local(host):
        return True
    if partes.scheme != "https" or host.lower() in _NOMBRES_LOCALES:
        return False
    ip = _ip_literal(host)
    return ip is None or ip.is_global


def resolver_destino(url: str, *, permitir_local: bool = False) -> tuple[str, list[str]]:
    """`(motivo, ips)`: por qué no se manda a esa URL, o las IPs comprobadas.

    Es la comprobación de enviar: repite la de forma, resuelve el nombre una sola vez y
    exige que **todas** sus direcciones sean públicas. Las IPs que devuelve son las que
    hay que usar al conectar (`post_a_ip`): volver a resolver abriría la ventana del
    rebinding. Son todas y no la primera porque un nombre con IPv6 e IPv4 puede no
    escuchar en la primera, y quedarse con ella era no llegar.
    """
    from urllib.parse import urlparse

    if not webhook_valido(url, permitir_local=permitir_local):
        return "tiene que ser https hacia un host público", []
    partes = urlparse(url)
    host = partes.hostname or ""
    puerto = partes.port or (443 if partes.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"no se puede resolver {host}: {exc}", []
    direcciones = list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))
    if not direcciones:
        return f"{host} no resuelve a ninguna dirección", []
    if permitir_local and _es_local(host):
        return "", direcciones
    for direccion in direcciones:
        ip = _ip_literal(direccion)
        if ip is None or not ip.is_global:
            return f"{host} resuelve a una dirección no pública ({direccion})", []
    return "", direcciones


def _conectar(ips: list[str], puerto: int, timeout: float | None) -> socket.socket:
    """La primera de `ips` que conteste. Sólo éstas: son las que se han comprobado."""
    ultimo: OSError | None = None
    for ip in ips:
        try:
            return socket.create_connection((ip, puerto), timeout)
        except OSError as exc:
            ultimo = exc
    raise ultimo or OSError("no hay ninguna dirección a la que conectar")


class _HTTPSaIP(http.client.HTTPSConnection):
    """HTTPS contra IPs fijas, con el nombre original para SNI y el certificado."""

    def __init__(self, host: str, ips: list[str], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._ips = ips

    def connect(self) -> None:
        crudo = _conectar(self._ips, self.port, self.timeout)
        self.sock = self._context.wrap_socket(crudo, server_hostname=self.host)


class _HTTPaIP(http.client.HTTPConnection):
    """HTTP contra IPs fijas. Sólo se usa en modo local, contra la propia máquina."""

    def __init__(self, host: str, ips: list[str], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._ips = ips

    def connect(self) -> None:
        self.sock = _conectar(self._ips, self.port, self.timeout)


def post_a_ip(url: str, ips: str | list[str], cuerpo: bytes, *, timeout: float) -> int:
    """POST de JSON a `url` conectando a `ips`, en orden. Devuelve el código de estado.

    No sigue redirecciones —`http.client` no las sigue nunca—: una respuesta 3xx vuelve
    como tal y quien llama decide. El nombre de la URL va en `Host` y en el TLS.
    """
    from urllib.parse import urlparse

    partes = urlparse(url)
    clase = _HTTPSaIP if partes.scheme == "https" else _HTTPaIP
    lista = [ips] if isinstance(ips, str) else list(ips)
    conexion = clase(partes.hostname or "", lista, port=partes.port, timeout=timeout)
    ruta = partes.path or "/"
    if partes.query:
        ruta += f"?{partes.query}"
    try:
        conexion.request(
            "POST",
            ruta,
            body=cuerpo,
            headers={"Content-Type": "application/json", "User-Agent": "laplace-alertas"},
        )
        return conexion.getresponse().status
    finally:
        conexion.close()


class EmailNotifier:
    """Correo por SMTP. La credencial vive en el entorno; el destinatario, en la
    interfaz (D-123)."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def configured(self) -> bool:
        return bool(self._s.smtp_host and (self._s.smtp_from or self._s.smtp_user))

    def send(self, to: str, subject: str, text: str) -> bool:
        if not self.configured:
            logger.error(
                "hay un correo de alertas puesto pero no hay servidor: falta LAPLACE_SMTP_HOST"
            )
            return False
        try:
            # Dentro del `try`: es al poner la cabecera cuando `email` rechaza un salto de
            # línea, no al enviar.
            mensaje = EmailMessage()
            mensaje["From"] = self._s.smtp_from or self._s.smtp_user
            mensaje["To"] = to
            mensaje["Subject"] = subject
            mensaje.set_content(text)
            with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=15) as smtp:
                if self._s.smtp_starttls:
                    smtp.starttls()
                if self._s.smtp_user:
                    smtp.login(self._s.smtp_user, self._s.smtp_password)
                smtp.send_message(mensaje)
            return True
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            # ValueError: una cabecera que `email` rechaza (un destinatario guardado antes
            # de que se validara). Se dice y se sigue con los demás canales (D-131).
            logger.warning("no se ha podido mandar el correo de alertas: %s", exc)
            return False


def texto_plano(mrkdwn: str) -> str:
    """El mensaje de Slack sin su marcado, para el correo y los webhooks genéricos."""
    import re

    texto = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2 (\1)", mrkdwn)
    return texto.replace("*", "").replace("_", "")


def _looks_like_slack(url: str) -> bool:
    from urllib.parse import urlparse

    partes = urlparse(url)
    if partes.scheme != "https" or not partes.hostname:
        return False
    return partes.hostname == SLACK_HOST_SUFFIX or partes.hostname.endswith(
        f".{SLACK_HOST_SUFFIX}"
    )


# ---------------------------------------------------------------------------------
# El ciclo
# ---------------------------------------------------------------------------------


def _window(days: int) -> Window:
    hasta = datetime.now(timezone.utc)
    return Window(since=hasta - timedelta(days=days), until=hasta, days=days)


class AlertRunner:
    """Evalúa los proyectos y manda lo que toque. Un solo sitio con efectos."""

    def __init__(
        self,
        store: Any,
        config: AlertConfig,
        state: AlertState,
        notifier: SlackNotifier | None = None,
        *,
        metadata: Any = None,
        webhook: WebhookNotifier | None = None,
        email: EmailNotifier | None = None,
        turno: Any = None,
    ) -> None:
        self._store = store
        self._config = config
        self._state = state
        self._notifier = notifier or SlackNotifier()
        #: De aquí salen los ajustes puestos en la interfaz, los estados de cada
        #: hallazgo y el presupuesto. Sin ella, lo de siempre: entorno y fichero.
        self._metadata = metadata
        #: La retención de la instalación: la de un proyecto no puede pasar de ella (D-173).
        self.retencion_instalacion = 0
        self._webhook = webhook or WebhookNotifier()
        self._email = email
        #: Quién hace esta vuelta cuando hay varios procesos: una función que devuelve un
        #: gestor de contexto con `True` si le toca a éste. Sin ella, siempre le toca (un
        #: solo proceso, o el modo local).
        self._turno = turno

    def evaluate_all_if_mine(self) -> list[Decision]:
        """`evaluate_all`, pero sólo si esta vuelta le toca a este proceso.

        Con varios workers o réplicas cada uno tiene su bucle, y sin turno cada uno
        mandaba las mismas alertas: la marca de «ya avisado» se guarda después de
        enviar, así que dos procesos que evalúan a la vez avisan los dos.
        """
        if self._turno is None:
            return self.evaluate_all()
        with self._turno() as mio:
            if not mio:
                logger.debug("las alertas de esta vuelta las evalúa otro proceso")
                return []
            return self.evaluate_all()

    def _desde_interfaz(self, project_id: str) -> dict[str, Any]:
        if self._metadata is None:
            return {}
        try:
            return self._metadata.get_setting(project_id, CLAVE_AJUSTES) or {}
        except Exception:  # noqa: BLE001
            logger.warning("no se leen los ajustes de alertas de %s", project_id, exc_info=True)
            return {}

    def config_for(self, project_id: str) -> ProjectAlertConfig:
        """Los ajustes en vigor para un proyecto. Los lee la API de estado."""
        return self._config.for_project(project_id, self._desde_interfaz(project_id))

    @property
    def email_ready(self) -> bool:
        return self._email is not None and self._email.configured

    @property
    def mailer(self) -> EmailNotifier | None:
        """El correo de la instalación, para quien tenga que mandar algo que no es una
        alerta (las invitaciones). `None` si no hay servidor configurado."""
        return self._email if self.email_ready else None

    def send_test(self, project_id: str) -> bool | None:
        """Un mensaje de prueba por los canales del proyecto. `None` si no tiene ninguno
        puesto o está silenciado; si no, si ha llegado por alguno."""
        ajustes = self.config_for(project_id)
        if not ajustes.enabled:
            return None
        with idioma.usar(ajustes.language):
            texto = t("alerta.prueba", proyecto=project_id)
        return self._enviar(ajustes, texto)

    def _enviar(self, ajustes: ProjectAlertConfig, texto: str) -> bool:
        """Por cada canal puesto. Cuenta como enviado si ha llegado por alguno."""
        llegados: list[bool] = []
        if ajustes.webhook_url:
            llegados.append(self._notifier.send(ajustes.webhook_url, texto))
        plano = texto_plano(texto)
        if ajustes.generic_webhook_url:
            llegados.append(
                self._webhook.send(ajustes.generic_webhook_url, plano, ajustes.project_id)
            )
        if ajustes.email_to and self._email is not None:
            asunto = f"Laplace · {ajustes.project_id}: " + plano.splitlines()[0].split("—")[-1]
            llegados.append(self._email.send(ajustes.email_to, asunto.strip(), plano))
        return any(llegados)

    def _presupuesto(self, project_id: str, ahora: datetime) -> tuple[str, str]:
        """El aviso de presupuesto que toca, si toca, y su clave en el estado."""
        if self._metadata is None:
            return "", ""
        from . import presupuesto

        tope = presupuesto.leer(self._metadata, project_id)
        if tope is None:
            return "", ""
        b = presupuesto.calcular(self._store, project_id, tope, ahora)
        pct = presupuesto.aviso_pendiente(b)
        if pct is None:
            return "", ""
        clave = f"budget:{b.month}:{pct}"
        if clave in self._state.read(project_id):
            return "", ""
        return t("alerta.presupuesto", texto=b.headline), clave

    def _clientes(
        self, project_id: str, ajustes: ProjectAlertConfig
    ) -> tuple[str, list[str], list[str], list[str]]:
        """El aviso de los clientes que han pasado a hacer perder dinero (D-161).

        Una vez por cliente, cuando pasa a perder: no cada vuelta mientras siga igual,
        que es la forma de que se silencie el canal. Cuando deja de perder se olvida, y
        si vuelve a perder, vuelve a sonar. Sin ingresos puestos no hay nada que decir.
        """
        if self._metadata is None or "cliente_pierde" in ajustes.muted_kinds:
            return "", [], [], []
        from . import margen

        ingresos = margen.leer_ingresos(self._metadata, project_id)
        if not ingresos:
            return "", [], [], []
        vista = margen.calcular(
            self._store, project_id, _window(ajustes.window_days), ingresos,
            margen.leer_tipos(self._metadata, project_id),
        )
        previos = self._state.read(project_id)
        pierden = [c for c in vista.customers if c.status == "pierde"]
        nuevos = [c for c in pierden if f"customer:{c.customer_id}" not in previos]
        vivos = {f"customer:{c.customer_id}" for c in pierden}
        recuperados = [k for k in previos if k.startswith("customer:") and k not in vivos]
        siguen = [k for k in vivos if k in previos]
        if not nuevos:
            return "", [], recuperados, siguen
        lineas = [tn("alerta.clientes", len(nuevos))]
        lineas += [f"• *{c.customer_id}* — {c.headline}" for c in nuevos]
        return (
            "\n".join(lineas),
            [f"customer:{c.customer_id}" for c in nuevos],
            recuperados,
            siguen,
        )

    def evaluate(self, project_id: str, *, dry_run: bool = False) -> Decision:
        """Un proyecto. `dry_run` calcula la decisión sin mandar ni recordar nada.

        Todo en el idioma de los ajustes del proyecto: los títulos de los hallazgos y el
        presupuesto se redactan dentro, y este proceso no viene de ninguna petición.
        """
        with idioma.usar(self.config_for(project_id).language):
            return self._evaluate(project_id, dry_run=dry_run)

    def _evaluate(self, project_id: str, *, dry_run: bool) -> Decision:
        from .insights import overview as build_overview

        ajustes = self.config_for(project_id)
        if not ajustes.enabled:
            return Decision(
                project_id, [], [], [], reason="silenciado o sin canal: ni webhook ni correo"
            )

        # Lo marcado como arreglado o ignorado no alerta: ya tiene respuesta (D-123).
        estados = {}
        if self._metadata is not None:
            from .seguimiento import leer_estados

            estados = leer_estados(self._metadata, project_id)
        vista = build_overview(
            self._store, project_id, _window(ajustes.window_days), states=estados
        )
        ahora = datetime.now(timezone.utc)
        previos = self._state.read(project_id)
        decision = decide(vista, ajustes, previos, ahora)
        decision.budget_notice, decision.budget_key = self._presupuesto(project_id, ahora)
        (
            decision.customers_notice,
            decision.customer_keys,
            decision.customers_recovered,
            decision.customers_still,
        ) = self._clientes(project_id, ajustes)
        if dry_run:
            return decision

        # Un cliente que sigue perdiendo no está olvidado: sólo está avisado.
        decision.forgotten = [k for k in decision.forgotten if k not in decision.customers_still]
        self._state.forget(project_id, decision.forgotten + decision.customers_recovered)
        for clave_cliente in decision.customers_still:
            previo = previos.get(clave_cliente)
            if previo is not None:
                previo.seen_at = ahora
                self._state.save(project_id, previo)
        # Los que están en calma se tocan igual: `seen_at` es lo que impide que un
        # problema que sigue vivo se olvide sólo por llevar rato sin poder avisar.
        for finding in decision.silenced:
            previo = previos.get(finding.id)
            if previo is not None:
                previo.seen_at = ahora
                previo.amount_usd = finding.window_waste_usd
                self._state.save(project_id, previo)

        if not decision.due and not decision.budget_notice and not decision.customers_notice:
            return decision

        urls = {
            f.id: self._config.finding_url(project_id, f, ajustes.window_days)
            for f in decision.due
        }
        partes = []
        if decision.budget_notice:
            partes.append(t("alerta.proyecto", proyecto=project_id, texto=decision.budget_notice))
        if decision.customers_notice:
            partes.append(
                t("alerta.proyecto", proyecto=project_id, texto=decision.customers_notice)
            )
        if decision.due:
            partes.append(compose(vista, decision, ajustes, urls))
        texto = "\n\n".join(partes)
        if not self._enviar(ajustes, texto):
            # No se recuerda lo que no se ha mandado: si el envío falla, el siguiente
            # ciclo lo vuelve a intentar en lugar de tragarse el aviso para siempre.
            return replace(decision, due=[], reason="el envío ha fallado por todos los canales")

        for clave_cliente in decision.customer_keys:
            self._state.save(
                project_id,
                AlertRecord(
                    finding_id=clave_cliente,
                    notified_at=ahora,
                    seen_at=ahora,
                    amount_usd=0.0,
                    times=1,
                ),
            )
        if decision.budget_key:
            self._state.save(
                project_id,
                AlertRecord(
                    finding_id=decision.budget_key,
                    notified_at=ahora,
                    seen_at=ahora,
                    amount_usd=0.0,
                    times=1,
                ),
            )

        for finding in decision.due:
            anterior = previos.get(finding.id)
            self._state.save(
                project_id,
                AlertRecord(
                    finding_id=finding.id,
                    notified_at=ahora,
                    seen_at=ahora,
                    amount_usd=finding.window_waste_usd,
                    times=(anterior.times if anterior else 0) + 1,
                ),
            )
        logger.info(
            "alerta enviada — proyecto=%s hallazgos=%d", project_id, len(decision.due)
        )
        return decision

    def evaluate_all(self) -> list[Decision]:
        try:
            proyectos = [p.project_id for p in self._store.list_projects()]
        except Exception:  # noqa: BLE001
            logger.warning("no se han podido listar los proyectos", exc_info=True)
            return []
        salida = []
        for project_id in proyectos:
            if self._metadata is not None:
                # Los ingresos de Stripe, una vez al día y antes de evaluar: la alerta de
                # clientes que pierden dinero tiene que mirar lo que pagan hoy (D-163).
                from .stripe_ingresos import sincronizar_si_toca

                sincronizar_si_toca(self._metadata, project_id)
                # La retención propia del proyecto, también una vez al día (D-173).
                from .retencion import aplicar_si_toca

                aplicar_si_toca(
                    self._store, self._metadata, project_id, self.retencion_instalacion
                )
            try:
                salida.append(self.evaluate(project_id))
            except Exception:  # noqa: BLE001
                logger.exception("fallo al evaluar las alertas de %s", project_id)
        return salida


async def alert_loop(runner: AlertRunner, interval_seconds: int) -> None:
    """Repasa los proyectos cada tanto. Vive en el mismo proceso que la API.

    Es lo que hace que las alertas funcionen igual en local y en la nube sin montar un
    planificador aparte: `laplace ui` es un proceso, la nube es ese mismo proceso.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(runner.evaluate_all_if_mine)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("fallo en el ciclo de alertas; se reintenta al siguiente")
