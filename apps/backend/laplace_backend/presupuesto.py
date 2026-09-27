"""Presupuesto mensual: «avísame si paso de 200 $ al mes» (D-123).

Hasta aquí los umbrales eran por hallazgo, y la pregunta de quien paga no es ésa: es
cuánto lleva gastado este mes y si va a pasarse. Se mide sobre el **mes natural** —el
que factura el proveedor—, no sobre el rango del selector.

La proyección a fin de mes sigue la misma regla que el resto del producto (D-073): por
debajo de un día de datos del mes no se proyecta, porque multiplicar una hora por treinta
días convierte un pico en una alarma falsa. Sin proyección se dice lo gastado y nada más.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from . import cifras
from .insights import MIN_DAYS_FOR_PROJECTION, observed_days
from .storage.base import Window
from .textos import t

CLAVE = "budget"
#: Porcentajes del presupuesto que avisan, una vez cada uno por mes.
AVISOS = (80, 100)


class Budget(BaseModel):
    """El presupuesto de un proyecto y cómo va este mes."""

    project_id: str
    #: `None` cuando no hay presupuesto puesto: entonces lo demás es informativo.
    monthly_usd: float | None = None
    month: str = ""
    month_to_date_usd: float = 0.0
    #: Lo que costará el mes a este ritmo. `None` con menos de un día de datos del mes.
    projected_usd: float | None = None
    #: Fracción del presupuesto ya gastada. `None` sin presupuesto.
    ratio: float | None = None
    #: `sin-presupuesto`, `bien`, `cerca`, `va-a-pasarse` o `pasado`.
    status: str = "sin-presupuesto"
    headline: str = ""
    #: Hay llamadas sin tarifa en el mes: lo gastado es un suelo.
    cost_is_floor: bool = False
    unknown_cost_spans: int = 0


def inicio_de_mes(ahora: datetime) -> datetime:
    return ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def leer(metadata: Any, project_id: str) -> float | None:
    try:
        valor = metadata.get_setting(project_id, CLAVE)
    except Exception:  # noqa: BLE001
        return None
    if not valor:
        return None
    try:
        importe = float(valor.get("monthly_usd"))
    except (TypeError, ValueError):
        return None
    return importe if importe > 0 else None


def calcular(
    store: Any, project_id: str, monthly_usd: float | None, ahora: datetime | None = None
) -> Budget:
    ahora = ahora or datetime.now(timezone.utc)
    desde = inicio_de_mes(ahora)
    ventana = Window(since=desde, until=ahora, days=max(1, ahora.day))
    resumen = store.summarize_window(project_id, ventana)
    gastado = resumen.total_cost_usd

    # Los días con datos de este mes, no los días que lleva el mes: un agente que empezó
    # a enviar trazas ayer no ha gastado «lo de diecinueve días» en uno. Medidos con la
    # misma función que el inicio: si no, el inicio diría «todavía no proyectamos» y
    # esta línea, justo debajo, proyectaría el mes.
    dias_con_datos = observed_days(resumen, ventana)
    proyectado = None
    if dias_con_datos >= MIN_DAYS_FOR_PROJECTION:
        restantes = (inicio_de_mes_siguiente(ahora) - ahora).total_seconds() / 86400
        proyectado = gastado + gastado / dias_con_datos * restantes

    b = Budget(
        project_id=project_id,
        monthly_usd=monthly_usd,
        month=ahora.strftime("%Y-%m"),
        month_to_date_usd=gastado,
        projected_usd=proyectado,
        cost_is_floor=resumen.unknown_cost_spans > 0,
        unknown_cost_spans=resumen.unknown_cost_spans,
    )
    lleva = t(
        "presupuesto.lleva_suelo" if b.cost_is_floor else "presupuesto.lleva",
        coste=cifras.dinero(gastado),
    )
    if monthly_usd is None:
        b.headline = t("presupuesto.sin_tope", lleva=lleva)
        return b

    b.ratio = gastado / monthly_usd
    valores = {
        "lleva": lleva,
        "tope": cifras.dinero(monthly_usd),
        "pct": cifras.porcentaje(b.ratio),
    }
    if b.ratio >= 1:
        b.status = "pasado"
        b.headline = t("presupuesto.pasado", **valores)
    elif proyectado is not None and proyectado > monthly_usd:
        b.status = "va-a-pasarse"
        b.headline = t(
            "presupuesto.va_a_pasarse", **valores, proyectado=cifras.dinero(proyectado)
        )
    elif b.ratio >= AVISOS[0] / 100:
        b.status = "cerca"
        b.headline = t("presupuesto.cerca", **valores)
    else:
        b.status = "bien"
        cierre = (
            t("presupuesto.cierre.proyectado", proyectado=cifras.dinero(proyectado))
            if proyectado is not None
            else t(
                "presupuesto.cierre.sin_proyeccion",
                dias=cifras.decimal(MIN_DAYS_FOR_PROJECTION),
            )
        )
        b.headline = t("presupuesto.bien", **valores, cierre=cierre)
    return b


def inicio_de_mes_siguiente(ahora: datetime) -> datetime:
    inicio = inicio_de_mes(ahora)
    if inicio.month == 12:
        return inicio.replace(year=inicio.year + 1, month=1)
    return inicio.replace(month=inicio.month + 1)


def aviso_pendiente(budget: Budget) -> int | None:
    """El porcentaje más alto cruzado este mes, si hay alguno. Lo usan las alertas."""
    if budget.ratio is None:
        return None
    cruzados = [p for p in AVISOS if budget.ratio * 100 >= p]
    return max(cruzados) if cruzados else None
