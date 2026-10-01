"""Margen por cliente (Fase 6, D-161): lo que te paga cada cliente frente a lo que te
cuesta su trabajo.

Laplace sabe lo que cuesta cada ejecución; con `laplace.set_context(customer_id=…)` sabe
también para quién era. Lo que no sabe es lo que te paga cada cliente, y eso lo pone el
usuario a mano, en dinero al mes (más adelante, desde Stripe). Con las dos cosas, la
pregunta que nadie más contesta: **¿qué clientes te hacen perder dinero?**

Las reglas del producto valen aquí igual que en el héroe:

* **No se proyecta desde menos de un día de datos** (D-073). Los ingresos son al mes y el
  coste de una hora no se puede comparar con ellos: se enseña lo gastado y se dice
  cuánto falta. La proyección es la misma del Diagnóstico, sobre los días de datos del
  proyecto, así que el coste al mes de todos los clientes suma el del héroe.
* **Un modelo sin tarifa no cuesta cero.** Si un cliente tiene llamadas sin tarifa, su
  coste es un suelo y su margen un techo: se dice «como mucho». Un margen negativo sigue
  siendo negativo con más coste, así que «pierdes al menos» sí se puede afirmar.
* **El trabajo sin cliente se cuenta aparte**, nunca repartido entre los clientes: no
  sabemos de quién es, y repartirlo sería inventarse a quién cobrárselo. Las tiradas de
  evaluación no cuentan en ningún lado: son del desarrollador.
* **Lo evitable de cada cliente** (D-179) es lo evitable de cada problema repartido en
  proporción a lo que gastó cada cliente en el paso del problema, el mismo criterio que
  el gráfico del Diagnóstico (D-152). Es un reparto y se dice; la parte del trabajo sin
  cliente no se le da a nadie.
* **Los ingresos en otra moneda** se convierten con el tipo de cambio que pone el
  usuario (D-179). Sin tipo para esa moneda no se convierte con uno inventado: el
  cliente queda sin margen y se dice qué falta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import cifras
from .insights import MIN_DAYS_FOR_PROJECTION, observed_days, reparto, span_label
from .insights.modelos import DAYS_PER_MONTH, _projection_base
from .storage.base import Window
from .textos import t, tn

#: Clave de ajuste de cada cliente: `revenue:<customer_id>` → `{"monthly": 1200.0}`.
PREFIJO = "revenue:"
#: Por debajo de esta parte de lo que paga, el margen se llama ajustado: un cliente que
#: deja un 5 % pasa a perder dinero con cualquier cambio de modelo o de tráfico. Es una
#: elección de producto, escrita aquí para que se pueda discutir.
MARGEN_AJUSTADO = 0.2
#: Clientes que se leen del almacén. Más que eso no cabe en una tabla que se lea.
MAX_CLIENTES = 500
#: Los tipos de cambio del proyecto: `{"rates": {"EUR": 1.08}}`, dólares por unidad.
CLAVE_CAMBIO = "exchange_rates"
DOLAR = "USD"

Estado = Literal[
    "pierde", "ajustado", "gana", "sin-ingresos", "sin-proyeccion", "sin-trafico", "sin-cambio"
]


@dataclass(frozen=True)
class Ingreso:
    """Lo que paga un cliente al mes, en su moneda."""

    importe: float
    moneda: str = DOLAR


class FindingRef(BaseModel):
    """Un problema abierto del Diagnóstico que pasa en las ejecuciones de un cliente."""

    id: str
    title: str
    #: La parte de lo evitable del problema que es de este cliente, en la ventana (D-179).
    avoidable_usd: float = 0.0


class CustomerMargin(BaseModel):
    """Un cliente: lo que cuesta, lo que paga y lo que queda."""

    customer_id: str
    traces: int = 0
    #: Lo gastado en la ventana, medido.
    window_cost_usd: float = 0.0
    #: El mismo gasto al mes, al ritmo de la ventana. `None` sin un día de datos.
    monthly_cost_usd: float | None = None
    #: Lo que paga al mes, puesto por el usuario, ya en dólares. Sin `_usd`: es un dato
    #: suyo, no una cifra que el producto afirme (el guardia de D-107 va por esas).
    monthly_revenue: float | None = None
    #: Lo que paga, tal como se puso, y en qué moneda (D-179). En dólares coincide con
    #: `monthly_revenue`; en otra, `monthly_revenue` es la conversión, o `None` sin tipo.
    revenue_amount: float | None = None
    revenue_currency: str = DOLAR
    #: De dónde sale lo que paga: `manual` o `stripe` (D-162). Vacío sin ingresos.
    revenue_source: str = ""
    #: Ingresos menos coste, al mes. `None` sin ingresos o sin proyección.
    margin_usd: float | None = None
    #: Margen entre ingresos. `None` cuando no hay margen.
    margin_ratio: float | None = None
    #: Hay llamadas sin tarifa: el coste es un suelo y el margen, un techo.
    cost_is_floor: bool = False
    unknown_cost_spans: int = 0
    status: Estado = "sin-ingresos"
    headline: str = ""
    #: Los problemas abiertos del Diagnóstico que pasan en sus ejecuciones, del que más
    #: dinero devuelve al que menos. Es por donde empezar con un cliente que pierde.
    findings: list[FindingRef] = Field(default_factory=list)
    #: Lo evitable de todos sus problemas que es de él, en la ventana y al mes (D-179).
    avoidable_usd: float = 0.0
    avoidable_monthly_usd: float | None = None
    #: El margen al mes si se arreglan sus problemas. `None` sin margen.
    margin_after_fix_usd: float | None = None


class MarginView(BaseModel):
    """Lo que pinta la pestaña de Clientes."""

    project_id: str
    days: int
    currency: str = "USD"
    projected: bool = False
    observed_days: float = 0.0
    customers: list[CustomerMargin] = Field(default_factory=list)
    #: Cuántos te hacen perder dinero: es el aviso de la pestaña y del Diagnóstico.
    losing: int = 0
    #: El trabajo que no dice de qué cliente es. Se cuenta aparte, nunca se reparte.
    unassigned_traces: int = 0
    unassigned_cost_usd: float = 0.0
    #: Parte del gasto de la ventana que tiene cliente, entre 0 y 1.
    assigned_share: float = 0.0
    headline: str = ""


def clave(customer_id: str) -> str:
    return f"{PREFIJO}{customer_id}"


def leer_fuentes(metadata: Any, project_id: str) -> dict[str, str]:
    """De dónde sale lo que paga cada cliente: `stripe` o `manual`."""
    try:
        filas = metadata.list_settings(project_id, PREFIJO)
    except Exception:  # noqa: BLE001
        return {}
    return {
        k[len(PREFIJO) :]: ("stripe" if (v or {}).get("source") == "stripe" else "manual")
        for k, v in filas.items()
    }


def leer_ingresos(metadata: Any, project_id: str) -> dict[str, Ingreso]:
    """Lo que paga cada cliente, con su moneda. Sin base de metadatos, nada: no es un
    error. Se pasa a `calcular` junto con `leer_tipos`."""
    return leer_ingresos_en_moneda(metadata, project_id)


def _fila(
    cliente: str,
    trazas: int,
    coste: float,
    sin_tarifa: int,
    pagado: Ingreso | None,
    tipos: dict[str, float],
    base: float | None,
    dias: float,
) -> CustomerMargin:
    ingreso = en_dolares(pagado, tipos) if pagado is not None else None
    fila = CustomerMargin(
        customer_id=cliente,
        traces=trazas,
        window_cost_usd=coste,
        monthly_revenue=ingreso,
        revenue_amount=pagado.importe if pagado is not None else None,
        revenue_currency=pagado.moneda if pagado is not None else DOLAR,
        cost_is_floor=sin_tarifa > 0,
        unknown_cost_spans=sin_tarifa,
    )
    if base is not None:
        fila.monthly_cost_usd = coste / base * DAYS_PER_MONTH

    suelo = fila.cost_is_floor
    if trazas == 0:
        fila.status = "sin-trafico"
        fila.headline = t("margen.fila.sin_trafico")
        return fila
    if fila.monthly_cost_usd is None:
        fila.status = "sin-proyeccion"
        fila.headline = t(
            "margen.fila.sin_proyeccion_suelo" if suelo else "margen.fila.sin_proyeccion",
            coste=cifras.dinero(coste),
            tiempo=span_label(dias),
        )
        return fila
    coste_mes = cifras.dinero(fila.monthly_cost_usd)
    if pagado is not None and ingreso is None:
        # Paga en una moneda sin tipo de cambio puesto: no se compara con uno inventado.
        fila.status = "sin-cambio"
        fila.headline = t(
            "margen.fila.sin_cambio",
            importe=cifras.dinero(pagado.importe, pagado.moneda),
            moneda=pagado.moneda,
            coste=coste_mes,
        )
        return fila
    if ingreso is None:
        fila.status = "sin-ingresos"
        fila.headline = t(
            "margen.fila.sin_ingresos_suelo" if suelo else "margen.fila.sin_ingresos",
            coste=coste_mes,
        )
        return fila

    fila.margin_usd = ingreso - fila.monthly_cost_usd
    fila.margin_ratio = fila.margin_usd / ingreso
    valores = {
        "coste": coste_mes,
        "ingreso": cifras.dinero(ingreso),
        "margen": cifras.dinero(abs(fila.margin_usd)),
        "pct": cifras.porcentaje(abs(fila.margin_ratio)),
    }
    if fila.margin_usd < 0:
        # Con más coste por contar, pierde más: «al menos» es cierto.
        fila.status = "pierde"
        fila.headline = t("margen.fila.pierde_suelo" if suelo else "margen.fila.pierde", **valores)
    elif fila.margin_ratio < MARGEN_AJUSTADO:
        fila.status = "ajustado"
        fila.headline = t(
            "margen.fila.ajustado_suelo" if suelo else "margen.fila.ajustado", **valores
        )
    else:
        fila.status = "gana"
        fila.headline = t("margen.fila.gana_suelo" if suelo else "margen.fila.gana", **valores)
    return fila


#: Problemas que se enseñan por cliente: los que más devuelven. El resto, en el Diagnóstico.
MAX_PROBLEMAS = 3


def con_problemas(
    vista: MarginView,
    findings: list[Any],
    pasos: dict[str, dict[str, int]],
    costes: dict[str, dict[str, float]] | None = None,
    base: float | None = None,
) -> None:
    """Pone a cada cliente los problemas del Diagnóstico que pasan en sus ejecuciones, y
    cuánto de lo evitable es suyo.

    Se cruza por el paso, igual que la vista de traza: el problema está en un paso por
    el que pasan las ejecuciones del cliente. Una repetición o un bucle, además, sólo
    cuentan si el paso sale más de una vez en alguna de sus ejecuciones: pasar por él una
    vez no es repetirlo. `findings` llega ordenado por dinero, y ese orden se respeta.

    Lo evitable (D-179) se reparte por lo que gastó cada cliente en el paso, con
    `costes`, y se suma de todos sus problemas, también los que no caben en la lista.
    """
    evitable = evitable_por_cliente(findings, costes or {})
    for cliente in vista.customers:
        suyos = pasos.get(cliente.customer_id, {})
        dinero = evitable.get(cliente.customer_id, {})
        for f in findings:
            if len(cliente.findings) >= MAX_PROBLEMAS:
                break
            veces = max((suyos.get(p, 0) for p in (reparto(f) or {f.step_key: 0})), default=0)
            minimo = 2 if f.kind in ("repeticion", "bucle") else 1
            if f.step_key and veces >= minimo:
                cliente.findings.append(
                    FindingRef(id=f.id, title=f.title, avoidable_usd=dinero.get(f.id, 0.0))
                )
        cliente.avoidable_usd = sum(dinero.values())
        if base is not None and cliente.avoidable_usd > 0:
            cliente.avoidable_monthly_usd = cliente.avoidable_usd / base * DAYS_PER_MONTH
            if cliente.margin_usd is not None:
                cliente.margin_after_fix_usd = cliente.margin_usd + cliente.avoidable_monthly_usd
                if cliente.status in ("pierde", "ajustado"):
                    cliente.headline += t(
                        "margen.fila.arreglando_gana"
                        if cliente.margin_after_fix_usd >= 0
                        else "margen.fila.arreglando_pierde",
                        margen=cifras.dinero(abs(cliente.margin_after_fix_usd)),
                        evitable=cifras.dinero(cliente.avoidable_monthly_usd),
                    )


_ORDEN = {"pierde": 0, "ajustado": 1, "gana": 2, "sin-cambio": 3, "sin-ingresos": 3,
          "sin-proyeccion": 3, "sin-trafico": 4}


def calcular(
    store: Any,
    project_id: str,
    window: Window,
    ingresos: dict[str, float] | dict[str, Ingreso],
    tipos: dict[str, float] | None = None,
) -> MarginView:
    pagos = {
        c: (v if isinstance(v, Ingreso) else Ingreso(float(v), DOLAR)) for c, v in ingresos.items()
    }
    tipos = tipos or {}
    resumen = store.summarize_window(project_id, window)
    base = _projection_base(resumen, window)
    dias = observed_days(resumen, window)
    # Sin tiradas de evaluación: una prueba del arreglo no es trabajo de ningún cliente,
    # aunque el agente fije un `customer_id` dentro. En la demo, la comparación A/B
    # heredaba el cliente del último `set_context` y se le cobraba a él.
    grupos = store.cost_by(project_id, window, "customer", limit=MAX_CLIENTES, rules=True)

    vista = MarginView(
        project_id=project_id, days=window.days, projected=base is not None, observed_days=dias
    )
    vistos: set[str] = set()
    for g in grupos:
        if not g.key:
            vista.unassigned_traces = g.traces
            vista.unassigned_cost_usd = g.cost_usd
            continue
        vistos.add(g.key)
        vista.customers.append(
            _fila(g.key, g.traces, g.cost_usd, g.unknown_cost_spans, pagos.get(g.key), tipos,
                  base, dias)
        )
    # Un cliente con ingresos puestos y sin tráfico en el rango también se enseña: que
    # desaparezca de la tabla haría pensar que se ha borrado lo que se escribió.
    for cliente, pagado in pagos.items():
        if cliente not in vistos:
            vista.customers.append(_fila(cliente, 0, 0.0, 0, pagado, tipos, base, dias))

    vista.customers.sort(
        key=lambda c: (_ORDEN[c.status], c.margin_usd if c.margin_usd is not None else 0.0,
                       -c.window_cost_usd, c.customer_id)
    )
    vista.losing = sum(1 for c in vista.customers if c.status == "pierde")
    asignado = sum(c.window_cost_usd for c in vista.customers)
    total = asignado + vista.unassigned_cost_usd
    vista.assigned_share = asignado / total if total > 0 else 0.0

    con_trafico = [c for c in vista.customers if c.traces > 0]
    if not con_trafico:
        vista.headline = t("margen.titular.sin_clientes")
    elif base is None:
        vista.headline = t(
            "margen.titular.sin_proyeccion",
            hay=span_label(dias),
            minimo=span_label(MIN_DAYS_FOR_PROJECTION),
        )
    elif vista.losing:
        vista.headline = tn("margen.titular.pierden", vista.losing, n=vista.losing)
    elif not ingresos:
        vista.headline = t("margen.titular.sin_ingresos")
    else:
        vista.headline = t("margen.titular.bien")
    return vista


# ---------------------------------------------------------------------------------
# Monedas (D-179)
# ---------------------------------------------------------------------------------


def leer_tipos(metadata: Any, project_id: str) -> dict[str, float]:
    """Los tipos de cambio del proyecto, en dólares por unidad de cada moneda."""
    try:
        ajustes = metadata.get_setting(project_id, CLAVE_CAMBIO) or {}
    except Exception:  # noqa: BLE001
        return {}
    salida: dict[str, float] = {}
    for moneda, tipo in (ajustes.get("rates") or {}).items():
        try:
            valor = float(tipo)
        except (TypeError, ValueError):
            continue
        if math.isfinite(valor) and valor > 0:
            salida[str(moneda).upper()] = valor
    return salida


def leer_ingresos_en_moneda(metadata: Any, project_id: str) -> dict[str, Ingreso]:
    """Lo que paga cada cliente, en la moneda en que se puso."""
    try:
        filas = metadata.list_settings(project_id, PREFIJO)
    except Exception:  # noqa: BLE001
        return {}
    salida: dict[str, Ingreso] = {}
    for k, v in filas.items():
        try:
            importe = float((v or {}).get("monthly"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(importe) and importe > 0:
            moneda = str((v or {}).get("currency") or DOLAR).upper()
            salida[k[len(PREFIJO) :]] = Ingreso(importe, moneda)
    return salida


def en_dolares(ingreso: Ingreso, tipos: dict[str, float]) -> float | None:
    if ingreso.moneda == DOLAR:
        return ingreso.importe
    tipo = tipos.get(ingreso.moneda)
    return ingreso.importe * tipo if tipo else None


# ---------------------------------------------------------------------------------
# Lo evitable de cada cliente (D-179)
# ---------------------------------------------------------------------------------


def evitable_por_cliente(
    findings: list[Any], costes: dict[str, dict[str, float]]
) -> dict[str, dict[str, float]]:
    """Cliente → problema → la parte de su dinero evitable que es de ese cliente.

    Cada problema se reparte entre los clientes en proporción a lo que gastó cada uno en
    el paso (o pasos) del problema. El total de un paso incluye el trabajo sin cliente,
    que no se le da a nadie: la suma de los clientes nunca pasa de lo evitable.
    """
    totales: dict[str, float] = {}
    for pasos in costes.values():
        for paso, coste in pasos.items():
            totales[paso] = totales.get(paso, 0.0) + coste
    salida: dict[str, dict[str, float]] = {}
    for f in findings:
        for paso, dinero in reparto(f).items():
            total = totales.get(paso, 0.0)
            if dinero <= 0 or total <= 0:
                continue
            for cliente, pasos in costes.items():
                suyo = pasos.get(paso, 0.0)
                if cliente and suyo > 0:
                    por_cliente = salida.setdefault(cliente, {})
                    por_cliente[f.id] = por_cliente.get(f.id, 0.0) + dinero * suyo / total
    return salida
