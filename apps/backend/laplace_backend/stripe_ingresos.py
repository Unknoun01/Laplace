"""Ingresos por cliente desde Stripe (Fase 6, D-162).

El margen por cliente (D-161) necesita lo que paga cada uno, y escribirlo a mano se
queda viejo el mes que alguien cambia de plan. Aquí se leen las facturas pagadas de
Stripe y se convierten en «lo que paga al mes» por cliente, con tres cuidados:

* **A qué cliente de las trazas corresponde.** El `customer_id` de Laplace lo pone el
  agente del usuario y no tiene por qué ser el de Stripe. Manda
  `metadata.laplace_customer_id` del cliente de Stripe; si no lo tiene, su id (`cus_…`).
  No se casa por correo ni por nombre: un parecido no es una identidad.
* **Al mes, de verdad.** Cada línea de factura lleva el periodo que cubre: un plan anual
  de 1.200 $ son 100 $ al mes, no 1.200 el mes en que se cobró. Lo que no tiene periodo
  (un cargo suelto) cuenta entero en el mes en que se pagó.
* **Sin convertir monedas.** El coste está en dólares; una factura en euros no se pasa a
  dólares con un tipo inventado. Se cuenta aparte y se dice.

La clave es del proyecto, se guarda con los ajustes y no se devuelve nunca entera, como
las URLs de webhook. Basta una clave restringida con lectura de facturas y clientes.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from . import margen

logger = logging.getLogger("laplace.stripe")

API = "https://api.stripe.com/v1"
CLAVE = "stripe"
#: Cuántos días de facturas se leen. Un mes: es lo que se compara con el coste al mes.
DIAS = 30
#: Por debajo de esto una línea no es «una suscripción de N días», es un cargo suelto.
MIN_DIAS_PERIODO = 20
#: Tope de páginas de 100 facturas. Más de 5.000 facturas al mes es otro producto.
MAX_PAGINAS = 50

#: Quien pide a Stripe: `(url, clave) -> json`. Se cambia en las pruebas.
Pedir = Callable[[str, str], dict[str, Any]]


class StripeError(Exception):
    """Stripe ha dicho que no, o no ha respondido. El mensaje es para el usuario."""


class StripeStatus(BaseModel):
    configured: bool = False
    #: Los últimos cuatro caracteres, para reconocer la clave sin enseñarla.
    key_hint: str = ""
    last_sync: datetime | None = None
    customers: int = 0


class SyncResult(BaseModel):
    #: Cliente de las trazas → lo que paga al mes, en dólares.
    revenue: dict[str, float] = Field(default_factory=dict)
    invoices: int = 0
    #: Facturas en otra moneda: no se convierten, se cuentan aquí.
    other_currency: int = 0
    synced_at: datetime


def clave_valida(clave: str) -> bool:
    """Secreta (`sk_`) o restringida (`rk_`), de pruebas o de producción."""
    return clave.startswith(("sk_live_", "sk_test_", "rk_live_", "rk_test_")) and len(clave) > 20


def _pedir(url: str, clave: str) -> dict[str, Any]:
    peticion = urllib.request.Request(url, headers={"Authorization": f"Bearer {clave}"})
    try:
        with urllib.request.urlopen(peticion, timeout=20) as respuesta:  # noqa: S310
            return json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise StripeError("stripe.clave_rechazada") from exc
        raise StripeError("stripe.error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StripeError("stripe.sin_respuesta") from exc


def _cliente(factura: dict[str, Any]) -> str:
    cliente = factura.get("customer")
    if isinstance(cliente, dict):
        propio = (cliente.get("metadata") or {}).get("laplace_customer_id")
        return str(propio or cliente.get("id") or "")
    return str(cliente or "")


def _al_mes(factura: dict[str, Any]) -> float:
    """Lo que una factura pagada supone al mes, en la unidad de la moneda (no céntimos)."""
    lineas = ((factura.get("lines") or {}).get("data")) or []
    if not lineas:
        return (factura.get("amount_paid") or 0) / 100
    total = 0.0
    for linea in lineas:
        importe = (linea.get("amount") or 0) / 100
        periodo = linea.get("period") or {}
        inicio, fin = periodo.get("start"), periodo.get("end")
        dias = (fin - inicio) / 86400 if inicio and fin else 0
        total += importe * 30 / dias if dias >= MIN_DIAS_PERIODO else importe
    return total


def sincronizar(
    clave: str, ahora: datetime | None = None, pedir: Pedir | None = None
) -> SyncResult:
    """Lee las facturas pagadas del último mes y devuelve lo que paga cada cliente."""
    # `_pedir` se busca al llamar, no al definir: es lo que deja cambiarlo en las pruebas.
    pedir = pedir or _pedir
    ahora = ahora or datetime.now(timezone.utc)
    desde = int((ahora - timedelta(days=DIAS)).timestamp())
    resultado = SyncResult(synced_at=ahora)
    parametros = {
        "status": "paid",
        "created[gte]": str(desde),
        "limit": "100",
        "expand[]": "data.customer",
    }
    for _ in range(MAX_PAGINAS):
        pagina = pedir(f"{API}/invoices?{urllib.parse.urlencode(parametros)}", clave)
        facturas = pagina.get("data") or []
        for factura in facturas:
            resultado.invoices += 1
            if (factura.get("currency") or "").lower() != "usd":
                resultado.other_currency += 1
                continue
            cliente = _cliente(factura)
            if not cliente:
                continue
            resultado.revenue[cliente] = resultado.revenue.get(cliente, 0.0) + _al_mes(factura)
        if not pagina.get("has_more") or not facturas:
            break
        parametros["starting_after"] = facturas[-1]["id"]
    resultado.revenue = {k: round(v, 2) for k, v in resultado.revenue.items() if v > 0}
    return resultado


def leer(metadata: Any, project_id: str) -> dict[str, Any]:
    try:
        return metadata.get_setting(project_id, CLAVE) or {}
    except Exception:  # noqa: BLE001
        return {}


def estado(metadata: Any, project_id: str) -> StripeStatus:
    ajustes = leer(metadata, project_id)
    clave = ajustes.get("api_key") or ""
    ultima = ajustes.get("last_sync")
    return StripeStatus(
        configured=bool(clave),
        key_hint=f"…{clave[-4:]}" if clave else "",
        last_sync=datetime.fromisoformat(ultima) if ultima else None,
        customers=int(ajustes.get("customers") or 0),
    )


#: Cada cuánto se traen solos los ingresos de un proyecto con clave puesta.
CADA = timedelta(hours=24)


def sincronizar_si_toca(metadata: Any, project_id: str, ahora: datetime | None = None) -> bool:
    """La traída diaria (D-163): si hay clave y la última fue hace más de un día, trae.

    La llama el bucle de fondo, que ya tiene turno entre procesos. Un fallo de Stripe no
    se propaga: se apunta en el log y se reintenta en la siguiente vuelta, porque lo
    último traído sigue valiendo mientras tanto. Devuelve si ha traído.
    """
    ahora = ahora or datetime.now(timezone.utc)
    ajustes = leer(metadata, project_id)
    clave = ajustes.get("api_key")
    if not clave:
        return False
    ultima = ajustes.get("last_sync")
    if ultima and ahora - datetime.fromisoformat(ultima) < CADA:
        return False
    try:
        aplicar(metadata, project_id, sincronizar(clave, ahora))
    except StripeError as exc:
        logger.warning("stripe: no se han traído los ingresos de %s (%s)", project_id, exc)
        return False
    except Exception:  # noqa: BLE001 - el bucle de fondo no puede caerse por esto
        logger.exception("stripe: fallo al traer los ingresos de %s", project_id)
        return False
    logger.info("stripe: ingresos de %s traídos", project_id)
    return True


def aplicar(metadata: Any, project_id: str, resultado: SyncResult) -> None:
    """Guarda lo que paga cada cliente, marcado como de Stripe.

    Lo puesto a mano para un cliente que Stripe no conoce se queda. Lo que venía de
    Stripe y ya no sale (dejó de pagar) se quita: dejarlo sería decir que paga lo que
    pagaba hace meses. Lo puesto a mano para un cliente que Stripe sí conoce, lo pisa
    Stripe: la factura manda.
    """
    previos = metadata.list_settings(project_id, margen.PREFIJO)
    for clave, valor in previos.items():
        cliente = clave[len(margen.PREFIJO) :]
        if (valor or {}).get("source") == "stripe" and cliente not in resultado.revenue:
            metadata.delete_setting(project_id, clave)
    for cliente, importe in resultado.revenue.items():
        metadata.set_setting(
            project_id,
            margen.clave(cliente),
            {"monthly": importe, "source": "stripe", "synced_at": resultado.synced_at.isoformat()},
        )
    ajustes = leer(metadata, project_id)
    ajustes.update(
        {"last_sync": resultado.synced_at.isoformat(), "customers": len(resultado.revenue)}
    )
    metadata.set_setting(project_id, CLAVE, ajustes)
