"""API de lo que el usuario decide sobre su proyecto (D-123).

Estado de cada hallazgo, presupuesto, alertas, tarifas propias, reparto del gasto por
usuario y por sesión, datos de ejemplo y borrado. Todo cuelga de la misma tabla de
ajustes por proyecto, y va en su propio router por lo mismo que Evaluaciones y Prompts:
metido en `api.py` lo habría convertido en un cajón.

Las reglas de acceso son las de siempre (D-097, D-121). Lo que lleva proyecto lo acota
el middleware; lo que es de la instalación entera —las tarifas, los datos de ejemplo—
pide que quien llama la vea entera, que en local es cualquiera y en la nube el operador.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import arreglos_codigo, control, github_bot, idioma, margen, presupuesto, stripe_ingresos
from .alerts import CLAVE_AJUSTES, webhook_valido
from .auth import identity_of
from .ingest.otlp import recalcular_coste
from .pricing import custom_prices, get_price_table, set_custom_prices
from .seguimiento import clave
from .storage.base import Window
from .storage.metadata import MetadataUnavailable
from .textos import t

logger = logging.getLogger("laplace.api.ajustes")

router = APIRouter(prefix="/api")


def _store(request: Request) -> Any:
    return request.app.state.store


def _meta(request: Request) -> Any:
    return request.app.state.metadata


def _guard(fn, *args, **kwargs):
    """«No hay dónde guardar» es un 503 con su motivo, nunca un 200 que miente."""
    try:
        return fn(*args, **kwargs)
    except MetadataUnavailable as exc:
        # Lo que dice («postgres no responde: …») es para quien opera: al log.
        logger.warning("sin base de metadatos: %s", exc)
        raise HTTPException(status_code=503, detail=t("error.metadatos")) from exc


def _solo_instalacion(que: str) -> HTTPException:
    """Lo que afecta a todos los proyectos pide ver todos los proyectos.

    La comprobación va escrita en cada ruta y no escondida aquí: la red de D-121 lee el
    código de la ruta, y una comprobación que no se ve es una que se puede quitar.
    """
    return HTTPException(
        status_code=403,
        detail=t(f"error.solo_instalacion.{que}"),
    )


def _ventana(days: int) -> Window:
    hasta = datetime.now(timezone.utc)
    return Window(since=hasta - timedelta(days=days), until=hasta, days=days)


# ---------------------------------------------------------------------------------
# Estado de un hallazgo
# ---------------------------------------------------------------------------------


class FindingStateIn(BaseModel):
    project_id: str
    finding_id: str
    status: str = Field(pattern="^(arreglado|ignorado)$")
    note: str = Field(default="", max_length=2000)


@router.post("/finding-state")
async def set_finding_state(request: Request, body: FindingStateIn) -> dict[str, Any]:
    """Marca un hallazgo como arreglado o ignorado. Se guarda cuándo: es la frontera
    entre el antes y el después con la que se comprueba si el arreglo ha servido."""
    valor = {
        "status": body.status,
        "at": datetime.now(timezone.utc).isoformat(),
        "note": body.note.strip()[:500],
    }
    await run_in_threadpool(
        _guard, _meta(request).set_setting, body.project_id, clave(body.finding_id), valor
    )
    return {"finding_id": body.finding_id, **valor}


@router.delete("/finding-state")
async def clear_finding_state(
    request: Request, project_id: str, finding_id: str
) -> dict[str, Any]:
    """Lo devuelve a la lista, como si no se hubiera marcado nunca."""
    borrado = await run_in_threadpool(
        _guard, _meta(request).delete_setting, project_id, clave(finding_id)
    )
    return {"finding_id": finding_id, "cleared": borrado}


# ---------------------------------------------------------------------------------
# Presupuesto
# ---------------------------------------------------------------------------------


@router.get("/budget", response_model=presupuesto.Budget)
async def get_budget(request: Request, project_id: str) -> presupuesto.Budget:
    tope = await run_in_threadpool(presupuesto.leer, _meta(request), project_id)
    return await run_in_threadpool(presupuesto.calcular, _store(request), project_id, tope)


class BudgetIn(BaseModel):
    """Lo que escribe el usuario. No lleva `_usd` a propósito: es un tope que pone
    él, no una cifra que el producto afirme, y el guardia de D-107 va por esas."""

    project_id: str
    #: Dólares al mes. `None` o 0 lo quita.
    monthly_limit: float | None = Field(default=None, ge=0)


@router.put("/budget", response_model=presupuesto.Budget)
async def put_budget(request: Request, body: BudgetIn) -> presupuesto.Budget:
    meta = _meta(request)
    if body.monthly_limit:
        await run_in_threadpool(
            _guard,
            meta.set_setting,
            body.project_id,
            presupuesto.CLAVE,
            {"monthly_usd": body.monthly_limit},
        )
    else:
        await run_in_threadpool(_guard, meta.delete_setting, body.project_id, presupuesto.CLAVE)
    tope = body.monthly_limit or None
    return await run_in_threadpool(presupuesto.calcular, _store(request), body.project_id, tope)


# ---------------------------------------------------------------------------------
# Margen por cliente (Fase 6, D-161)
# ---------------------------------------------------------------------------------


@router.get("/customers", response_model=margen.MarginView)
async def get_customers(
    request: Request, project_id: str, days: int = Query(30, ge=1, le=90)
) -> margen.MarginView:
    """Coste, ingresos y margen de cada cliente, con el aviso de quién hace perder dinero
    y los problemas del Diagnóstico que pasan en sus ejecuciones."""
    from .api import get_overview

    ingresos = await run_in_threadpool(margen.leer_ingresos, _meta(request), project_id)
    tipos = await run_in_threadpool(margen.leer_tipos, _meta(request), project_id)
    ventana = _ventana(days)
    vista = await run_in_threadpool(
        margen.calcular, _store(request), project_id, ventana, ingresos, tipos
    )
    if vista.customers:
        # El mismo Diagnóstico que el inicio, con su caché y los estados de cada problema:
        # lo arreglado o ignorado no se le atribuye a nadie.
        diagnostico = await get_overview(request, project_id, days)
        pasos = await run_in_threadpool(_store(request).customer_steps, project_id, ventana)
        costes = await run_in_threadpool(
            _store(request).customer_step_costs, project_id, ventana
        )
        # La misma base que el coste al mes de cada cliente: lo evitable y el coste se
        # proyectan igual, o el margen después de arreglar no querría decir nada.
        base = vista.observed_days if vista.projected else None
        margen.con_problemas(vista, diagnostico.findings, pasos, costes, base)
    fuentes = await run_in_threadpool(margen.leer_fuentes, _meta(request), project_id)
    for cliente in vista.customers:
        if cliente.monthly_revenue is not None:
            cliente.revenue_source = fuentes.get(cliente.customer_id, "manual")
    return vista


class RevenueIn(BaseModel):
    """Lo que paga un cliente al mes. Sin `_usd`, como el presupuesto: es un dato del
    usuario, no una cifra que el producto afirme."""

    project_id: str
    customer_id: str = Field(min_length=1, max_length=200)
    #: Al mes. `None` o 0 lo quita.
    monthly: float | None = Field(default=None, ge=0, le=1e9)
    #: En qué moneda (D-179). Otra que no sea el dólar necesita su tipo de cambio.
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")


@router.put("/customers/revenue")
async def put_customer_revenue(request: Request, body: RevenueIn) -> dict[str, Any]:
    meta = _meta(request)
    cliente = body.customer_id.strip()
    if not cliente:
        raise HTTPException(status_code=422, detail=t("error.cliente_vacio"))
    moneda = body.currency.upper()
    if body.monthly:
        valor: dict[str, Any] = {"monthly": body.monthly}
        if moneda != margen.DOLAR:
            valor["currency"] = moneda
        await run_in_threadpool(
            _guard, meta.set_setting, body.project_id, margen.clave(cliente), valor
        )
    else:
        await run_in_threadpool(
            _guard, meta.delete_setting, body.project_id, margen.clave(cliente)
        )
    return {"customer_id": cliente, "monthly": body.monthly or None, "currency": moneda}


class ExchangeRatesIn(BaseModel):
    """Los tipos de cambio del proyecto, en dólares por unidad de cada moneda (D-179).

    Los pone el usuario: el producto no tiene fuente de tipos y no se inventa uno. Lo que
    se manda sustituye a lo que había; una moneda que no venga se quita.
    """

    project_id: str
    rates: dict[str, float] = Field(default_factory=dict)


@router.get("/exchange-rates")
async def get_exchange_rates(request: Request, project_id: str) -> dict[str, Any]:
    tipos = await run_in_threadpool(margen.leer_tipos, _meta(request), project_id)
    return {"rates": tipos}


@router.put("/exchange-rates")
async def put_exchange_rates(request: Request, body: ExchangeRatesIn) -> dict[str, Any]:
    tipos: dict[str, float] = {}
    for moneda, tipo in body.rates.items():
        codigo = moneda.strip().upper()
        if len(codigo) != 3 or not codigo.isalpha() or codigo == margen.DOLAR:
            raise HTTPException(status_code=422, detail=t("error.moneda", moneda=moneda))
        if not (0 < tipo <= 1e6):
            raise HTTPException(status_code=422, detail=t("error.tipo_cambio", moneda=codigo))
        tipos[codigo] = float(tipo)
    meta = _meta(request)
    if tipos:
        await run_in_threadpool(
            _guard, meta.set_setting, body.project_id, margen.CLAVE_CAMBIO, {"rates": tipos}
        )
    else:
        await run_in_threadpool(_guard, meta.delete_setting, body.project_id, margen.CLAVE_CAMBIO)
    return {"rates": tipos}


# Ingresos desde Stripe (D-162). La clave es un secreto: se guarda y no se devuelve.


@router.get("/stripe", response_model=stripe_ingresos.StripeStatus)
async def get_stripe(request: Request, project_id: str) -> stripe_ingresos.StripeStatus:
    return await run_in_threadpool(stripe_ingresos.estado, _meta(request), project_id)


class StripeIn(BaseModel):
    project_id: str
    #: Una clave secreta o restringida de Stripe. `None` o vacía la quita.
    api_key: str | None = Field(default=None, max_length=300)


@router.put("/stripe", response_model=stripe_ingresos.StripeStatus)
async def put_stripe(request: Request, body: StripeIn) -> stripe_ingresos.StripeStatus:
    meta = _meta(request)
    clave_stripe = (body.api_key or "").strip()
    if clave_stripe and not stripe_ingresos.clave_valida(clave_stripe):
        raise HTTPException(status_code=422, detail=t("stripe.clave_invalida"))
    if clave_stripe:
        ajustes = await run_in_threadpool(stripe_ingresos.leer, meta, body.project_id)
        ajustes["api_key"] = clave_stripe
        await run_in_threadpool(
            _guard, meta.set_setting, body.project_id, stripe_ingresos.CLAVE, ajustes
        )
    else:
        await run_in_threadpool(
            _guard, meta.delete_setting, body.project_id, stripe_ingresos.CLAVE
        )
    return await run_in_threadpool(stripe_ingresos.estado, meta, body.project_id)


class StripeSyncIn(BaseModel):
    project_id: str


@router.post("/stripe/sync")
async def sync_stripe(request: Request, body: StripeSyncIn) -> dict[str, Any]:
    """Trae lo que paga cada cliente de las facturas pagadas del último mes."""
    meta = _meta(request)
    ajustes = await run_in_threadpool(stripe_ingresos.leer, meta, body.project_id)
    clave_stripe = ajustes.get("api_key")
    if not clave_stripe:
        raise HTTPException(status_code=409, detail=t("stripe.sin_clave"))
    try:
        tipos = await run_in_threadpool(margen.leer_tipos, _meta(request), body.project_id)
        resultado = await run_in_threadpool(
            stripe_ingresos.sincronizar, clave_stripe, None, None, tipos
        )
    except stripe_ingresos.StripeError as exc:
        raise HTTPException(status_code=502, detail=t(str(exc))) from exc
    await run_in_threadpool(_guard, stripe_ingresos.aplicar, meta, body.project_id, resultado)
    return {
        "customers": len(resultado.revenue),
        "invoices": resultado.invoices,
        "other_currency": resultado.other_currency,
        "synced_at": resultado.synced_at.isoformat(),
        "detail": t(
            "stripe.sincronizado",
            clientes=len(resultado.revenue),
            facturas=resultado.invoices,
        )
        + (
            " " + t("stripe.otra_moneda", n=resultado.other_currency)
            if resultado.other_currency
            else ""
        ),
    }


# ---------------------------------------------------------------------------------
# Alertas desde la interfaz
# ---------------------------------------------------------------------------------

#: Lo que la interfaz puede poner. Los secretos —URLs de webhook— se aceptan pero no
#: se devuelven nunca enteros.
_CAMPOS_ALERTA = (
    "language",
    "webhook_url",
    "generic_webhook_url",
    "email_to",
    "min_usd",
    "quiet_hours",
    "muted",
    "muted_kinds",
)


#: Destinatarios por proyecto. Un aviso de gasto no es una lista de correo, y sin tope
#: el botón de «probar» servía para mandar correos desde nuestro servidor a quien fuera.
MAX_DESTINATARIOS = 5


def motivo_correo_invalido(valor: str) -> str:
    """Por qué no vale este destinatario de alertas, o cadena vacía si vale (D-131).

    Antes bastaba con una `@`: un salto de línea pasaba, y dentro de una cabecera de
    correo es la forma de añadir cabeceras propias; y una coma daba para cien destinos.
    """
    from email.utils import getaddresses

    if any(c in valor for c in "\r\n\0"):
        return t("error.correo_saltos")
    direcciones = [d for _, d in getaddresses([valor]) if d]
    if not direcciones:
        return t("error.correo_invalido")
    if len(direcciones) > MAX_DESTINATARIOS:
        return t("error.correo_demasiados", n=MAX_DESTINATARIOS)
    for d in direcciones:
        usuario, arroba, dominio = d.rpartition("@")
        if not arroba or not usuario or "." not in dominio or " " in d:
            return t("error.correo_direccion", direccion=d)
    return ""


def _oculto(url: str) -> str:
    """De una URL secreta, sólo el host: basta para reconocerla y no sirve para usarla."""
    from urllib.parse import urlparse

    if not url:
        return ""
    return f"{urlparse(url).hostname or '?'}/…"


def _vista_alertas(request: Request, project_id: str) -> dict[str, Any]:
    runner = request.app.state.alerts
    ajustes = runner.config_for(project_id)
    return {
        "project_id": project_id,
        "enabled": ajustes.enabled,
        "slack": _oculto(ajustes.webhook_url),
        "webhook": _oculto(ajustes.generic_webhook_url),
        "email_to": ajustes.email_to,
        "email_ready": runner.email_ready,
        "min_usd": ajustes.min_usd,
        "quiet_hours": ajustes.quiet_hours,
        "window_days": ajustes.window_days,
        "muted": ajustes.muted,
        "muted_kinds": sorted(ajustes.muted_kinds),
        "env_enabled": request.app.state.settings.alerts_enabled,
    }


@router.get("/alert-settings")
async def get_alert_settings(request: Request, project_id: str) -> dict[str, Any]:
    return await run_in_threadpool(_vista_alertas, request, project_id)


class AlertSettingsIn(BaseModel):
    project_id: str
    #: Un campo ausente no se toca; una cadena vacía quita ese canal.
    webhook_url: str | None = Field(default=None, max_length=2000)
    generic_webhook_url: str | None = Field(default=None, max_length=2000)
    email_to: str | None = Field(default=None, max_length=254)
    #: Umbral en dólares ya gastados. Se guarda como `min_usd`, que es como lo llama el
    #: resto de las alertas; aquí no lleva el sufijo por lo mismo que `BudgetIn`.
    threshold: float | None = Field(default=None, ge=0)
    quiet_hours: float | None = Field(default=None, ge=0)
    muted: bool | None = None
    muted_kinds: list[str] | None = Field(default=None, max_length=50)


@router.put("/alert-settings")
async def put_alert_settings(request: Request, body: AlertSettingsIn) -> dict[str, Any]:
    meta = _meta(request)
    actual = await run_in_threadpool(meta.get_setting, body.project_id, CLAVE_AJUSTES) or {}
    cambios = body.model_dump(exclude_unset=True, exclude={"project_id"})
    if "threshold" in cambios:
        cambios["min_usd"] = cambios.pop("threshold")
    # Los avisos salen en el idioma de quien los configura (D-148).
    cambios["language"] = idioma.actual()
    local = request.app.state.settings.store == "sqlite"
    for campo in ("webhook_url", "generic_webhook_url"):
        url = (cambios.get(campo) or "").strip()
        if url and not webhook_valido(url, permitir_local=local):
            raise HTTPException(
                status_code=400,
                detail=t("error.webhook_publico_o_local" if local else "error.webhook_publico"),
            )
    if (cambios.get("webhook_url") or "").strip() and "slack.com" not in cambios["webhook_url"]:
        raise HTTPException(
            status_code=400,
            detail=t("error.webhook_no_slack"),
        )
    correo = (cambios.get("email_to") or "").strip()
    if correo:
        motivo = motivo_correo_invalido(correo)
        if motivo:
            raise HTTPException(status_code=400, detail=motivo)
        cambios["email_to"] = correo
    nuevo = {**actual, **{k: v for k, v in cambios.items() if k in _CAMPOS_ALERTA}}
    await run_in_threadpool(_guard, meta.set_setting, body.project_id, CLAVE_AJUSTES, nuevo)
    return await run_in_threadpool(_vista_alertas, request, body.project_id)


@router.post("/alert-settings/test")
async def test_alert(request: Request, project_id: str) -> dict[str, Any]:
    """Manda un mensaje de prueba por los canales puestos. Es la única forma de saber
    que el webhook está bien antes de que haga falta de verdad."""
    llegado = await run_in_threadpool(request.app.state.alerts.send_test, project_id)
    if llegado is None:
        raise HTTPException(status_code=400, detail=t("error.sin_canal"))
    return {"delivered": llegado}


# ---------------------------------------------------------------------------------
# Quién gasta: por usuario y por sesión
# ---------------------------------------------------------------------------------


class CostGroupOut(BaseModel):
    key: str
    traces: int
    cost_usd: float
    cost_per_trace_usd: float
    tokens: int
    #: Llamadas sin tarifa dentro del grupo: su coste es un suelo.
    unknown_cost_spans: int = 0


class Breakdown(BaseModel):
    project_id: str
    by: str
    groups: list[CostGroupOut]
    #: Ejecuciones que no dicen de quién son. Sin esto, los grupos sumarían menos que el
    #: total sin explicación.
    untagged_traces: int = 0
    untagged_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    unknown_cost_spans: int = 0


@router.get("/breakdown", response_model=Breakdown)
async def get_breakdown(
    request: Request,
    project_id: str,
    by: str = Query("user", pattern="^(user|session)$"),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=100),
) -> Breakdown:
    grupos = await run_in_threadpool(
        _store(request).cost_by, project_id, _ventana(days), by, limit + 1
    )
    sin = next((g for g in grupos if not g.key), None)
    con = [g for g in grupos if g.key][:limit]
    total = sum(g.cost_usd for g in grupos)
    return Breakdown(
        project_id=project_id,
        by=by,
        groups=[
            CostGroupOut(
                key=g.key,
                traces=g.traces,
                cost_usd=g.cost_usd,
                cost_per_trace_usd=g.cost_usd / g.traces if g.traces else 0.0,
                tokens=g.tokens,
                unknown_cost_spans=g.unknown_cost_spans,
            )
            for g in con
        ],
        untagged_traces=sin.traces if sin else 0,
        untagged_cost_usd=sin.cost_usd if sin else 0.0,
        total_cost_usd=total,
        unknown_cost_spans=sum(g.unknown_cost_spans for g in grupos),
    )


# ---------------------------------------------------------------------------------
# Tarifas propias
# ---------------------------------------------------------------------------------


class PriceIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    #: Dólares por millón de tokens, como en la tabla.
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)


def _recalcular(store: Any, modelo: str) -> int:
    """Vuelve a poner precio a todo lo guardado de ese modelo. Devuelve cuántos spans."""
    spans = store.spans_by_model(modelo)
    if not spans:
        return 0
    tabla = get_price_table()
    store.insert_spans([recalcular_coste(s, tabla) for s in spans])
    return len(spans)


def _guardar_tarifas(meta: Any, modelos: dict[str, dict[str, Any]]) -> None:
    meta.set_setting("*", "prices", {"models": modelos})
    set_custom_prices(modelos)


@router.get("/pricing/custom")
async def get_custom_prices(request: Request) -> dict[str, Any]:
    """Las tarifas propias y los modelos vistos que siguen sin tarifa."""
    identidad = identity_of(request)
    if identidad.sees_everything:
        proyectos = None
    else:
        todos = await run_in_threadpool(_store(request).list_projects)
        proyectos = identidad.visible([p.project_id for p in todos])
    sin_tarifa = await run_in_threadpool(
        _store(request).unpriced_models, proyectos, _ventana(90)
    )
    return {
        "models": custom_prices(),
        "unpriced": sin_tarifa,
        "editable": identidad.sees_everything,
    }


@router.put("/pricing/custom")
async def put_custom_price(request: Request, body: PriceIn) -> dict[str, Any]:
    if not identity_of(request).sees_everything:
        raise _solo_instalacion("cambiar_tarifa")
    modelos = custom_prices()
    entrada: dict[str, Any] = {"input": body.input, "output": body.output}
    if body.cached_input is not None:
        entrada["cached_input"] = body.cached_input
    modelos[body.model.strip()] = entrada
    await run_in_threadpool(_guard, _guardar_tarifas, _meta(request), modelos)
    recalculados = await run_in_threadpool(_recalcular, _store(request), body.model.strip())
    return {"model": body.model.strip(), "repriced_spans": recalculados}


@router.delete("/pricing/custom")
async def delete_custom_price(request: Request, model: str) -> dict[str, Any]:
    if not identity_of(request).sees_everything:
        raise _solo_instalacion("quitar_tarifa")
    modelos = custom_prices()
    if modelos.pop(model, None) is None:
        raise HTTPException(status_code=404, detail=t("error.modelo_sin_tarifa_propia"))
    await run_in_threadpool(_guard, _guardar_tarifas, _meta(request), modelos)
    recalculados = await run_in_threadpool(_recalcular, _store(request), model)
    return {"model": model, "repriced_spans": recalculados}


# ---------------------------------------------------------------------------------
# La instalación, los datos de ejemplo y el borrado
# ---------------------------------------------------------------------------------


@router.get("/instance")
async def instance(request: Request) -> dict[str, Any]:
    """Lo que la pantalla de ajustes necesita saber de esta instalación."""
    settings = request.app.state.settings
    return {
        "local": settings.store == "sqlite",
        "retention_days": settings.retention_days,
        "email_ready": request.app.state.alerts.email_ready,
        "alerts_env_enabled": settings.alerts_enabled,
        "operator": identity_of(request).sees_everything,
    }


@router.post("/demo")
async def load_demo(request: Request) -> dict[str, Any]:
    """Manda las trazas de `laplace demo` desde la propia interfaz, sólo en local.

    Quien acaba de instalar tiene una pantalla vacía y un comando que teclear en otra
    ventana. En la nube no: meter datos inventados en una instalación compartida es
    lo último que alguien espera de un botón.
    """
    if not identity_of(request).sees_everything:
        raise _solo_instalacion("demo")
    if request.app.state.settings.store != "sqlite":
        raise HTTPException(status_code=404, detail=t("error.demo_solo_local"))
    from .demo import cargar_demo

    origen = str(request.base_url).rstrip("/")
    return await run_in_threadpool(cargar_demo, origen, _store(request), _meta(request))


# ---------------------------------------------------------------------------------
# Retención por proyecto y borrado de una persona o un cliente (D-173)
# ---------------------------------------------------------------------------------


@router.get("/retention")
async def get_retention(request: Request, project_id: str) -> dict[str, Any]:
    from . import retencion

    ajustes = await run_in_threadpool(retencion.leer, _meta(request), project_id)
    instalacion = request.app.state.settings.retention_days
    propios = int(ajustes.get("days") or 0)
    return {
        "project_id": project_id,
        "days": propios,
        "installation_days": instalacion,
        "effective_days": retencion.dias_efectivos(propios, instalacion),
        "last_run": ajustes.get("last_run"),
    }


class RetencionIn(BaseModel):
    project_id: str
    #: Días que guarda el proyecto. 0 lo quita y manda la de la instalación.
    days: int = Field(ge=0, le=3650)


@router.put("/retention")
async def put_retention(request: Request, body: RetencionIn) -> dict[str, Any]:
    from . import retencion

    meta = _meta(request)
    if body.days:
        await run_in_threadpool(
            _guard, meta.set_setting, body.project_id, retencion.CLAVE, {"days": body.days}
        )
    else:
        await run_in_threadpool(_guard, meta.delete_setting, body.project_id, retencion.CLAVE)
    return await get_retention(request, body.project_id)


@router.delete("/subjects")
async def delete_subject(
    request: Request,
    project_id: str,
    confirm: str,
    user_id: str | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Borra las trazas enteras en las que aparece una persona (`user_id`) o un cliente
    (`customer_id`) del proyecto: lo que pediría el cliente de un cliente.

    `confirm` repite el id, como al borrar un proyecto: un `DELETE` escrito a mano con el
    id equivocado no se lleva nada por delante.
    """
    quien = user_id or customer_id
    if bool(user_id) == bool(customer_id):
        raise HTTPException(status_code=400, detail=t("error.sujeto_uno"))
    if confirm != quien:
        raise HTTPException(status_code=400, detail=t("error.confirmar_sujeto"))
    trazas = await run_in_threadpool(
        _store(request).delete_subject, project_id, user_id=user_id, customer_id=customer_id
    )
    cuentas = getattr(request.app.state, "cuentas", None)
    if cuentas is not None:
        org_id = await run_in_threadpool(cuentas.org_del_proyecto, project_id)
        await run_in_threadpool(
            cuentas.anotar,
            org_id or "",
            identity_of(request).user_id,
            "borrar_usuario" if user_id else "borrar_cliente",
            project_id,
        )
    logger.warning("datos borrados de %s %s en %s: %d trazas",
                   "la persona" if user_id else "el cliente", quien, project_id, trazas)
    return {"project_id": project_id, "deleted_traces": trazas}


@router.delete("/projects")
async def delete_project(request: Request, project_id: str, confirm: str) -> dict[str, Any]:
    """Borra un proyecto entero: trazas, anotaciones, conjuntos, prompts y ajustes.

    `confirm` tiene que repetir el nombre: es la misma fricción que pide la pantalla, y
    un `DELETE` escrito a mano con el id equivocado no se lleva nada por delante.
    """
    if confirm != project_id:
        raise HTTPException(status_code=400, detail=t("error.confirmar_proyecto"))
    # Primero lo mutable, claves incluidas, y en una transacción; después las trazas.
    # Al revés, si fallaba lo segundo quedaba un proyecto sin trazas pero con sus claves
    # vivas escribiendo en él. Así, si falla lo primero no se ha borrado nada, y si falla
    # lo segundo ya no entra tráfico nuevo y repetir la petición termina el trabajo.
    await run_in_threadpool(_guard, _meta(request).delete_project_data, project_id)
    try:
        await run_in_threadpool(_store(request).delete_project, project_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("borrado de %s a medias: faltan las trazas", project_id)
        raise HTTPException(
            status_code=503,
            detail=t("error.borrado_a_medias"),
        ) from exc
    # Con cuentas, el proyecto deja de ser de su organización y queda escrito quién lo
    # borró (D-127). En local no hay ni una cosa ni la otra.
    cuentas = getattr(request.app.state, "cuentas", None)
    if cuentas is not None:
        org_id = await run_in_threadpool(cuentas.org_del_proyecto, project_id)
        await run_in_threadpool(cuentas.soltar_proyecto, project_id)
        await run_in_threadpool(
            cuentas.anotar,
            org_id or "",
            identity_of(request).user_id,
            "borrar_proyecto",
            project_id,
        )
    logger.warning("proyecto borrado entero — %s", project_id)
    return {"project_id": project_id, "deleted": True}


# Plano de control (D-187): tope por ejecución, bucles y parada. El GET lo usa también el
# SDK del usuario cada 30 s, con su clave de proyecto; el PUT es de admin (`ADMIN_WRITES`).


@router.get("/control", response_model=control.Control)
async def get_control(request: Request, project_id: str) -> control.Control:
    return await run_in_threadpool(_guard, control.leer, _meta(request), project_id)


@router.put("/control", response_model=control.Control)
async def put_control(request: Request, body: control.ControlIn) -> control.Control:
    meta = _meta(request)
    antes = await run_in_threadpool(_guard, control.leer, meta, body.project_id)
    puesto = await run_in_threadpool(_guard, control.guardar, meta, body)
    cuentas = getattr(request.app.state, "cuentas", None)
    if cuentas is not None and antes.stopped != puesto.stopped:
        # Parar los agentes de todos es de las cosas que tiene que quedar escritas.
        org_id = await run_in_threadpool(cuentas.org_del_proyecto, body.project_id)
        await run_in_threadpool(
            cuentas.anotar,
            org_id or "",
            identity_of(request).user_id,
            "parar_proyecto" if puesto.stopped else "reanudar_proyecto",
            body.project_id,
        )
    if antes.stopped != puesto.stopped:
        logger.warning(
            "proyecto %s — %s", "parado" if puesto.stopped else "reanudado", body.project_id
        )
    return puesto


# Bot de pull requests (D-185). El token es un secreto: se guarda y no se devuelve.


@router.get("/github", response_model=github_bot.GitHubStatus)
async def get_github(request: Request, project_id: str) -> github_bot.GitHubStatus:
    return await run_in_threadpool(github_bot.estado, _meta(request), project_id)


class GitHubIn(BaseModel):
    project_id: str
    #: `dueño/nombre`. Vacío desconecta y borra el token.
    repo: str = Field(default="", max_length=200)
    #: Vacía: la rama principal del repositorio.
    base_branch: str = Field(default="", max_length=200)
    #: Un token nuevo; vacío deja el que había.
    token: str | None = Field(default=None, max_length=300)
    #: Con la GitHub App de la instalación, en lugar de token.
    installation_id: int | None = Field(default=None, ge=1)


@router.put("/github", response_model=github_bot.GitHubStatus)
async def put_github(request: Request, body: GitHubIn) -> github_bot.GitHubStatus:
    meta = _meta(request)
    repo = body.repo.strip()
    if not repo:
        await run_in_threadpool(_guard, meta.delete_setting, body.project_id, github_bot.CLAVE)
        return await run_in_threadpool(github_bot.estado, meta, body.project_id)
    if not github_bot.repo_valido(repo):
        raise HTTPException(status_code=422, detail=t("github.repo_invalido"))
    ajustes = await run_in_threadpool(github_bot.leer, meta, body.project_id)
    ajustes.update(repo=repo, base_branch=body.base_branch.strip())
    # Para el aviso de GitHub al fusionar (D-191). Se genera una vez y se conserva: si
    # cambiase al guardar otra cosa, el webhook ya puesto en GitHub dejaría de valer.
    ajustes.setdefault("webhook_secret", github_bot.nuevo_secreto())
    token = (body.token or "").strip()
    if body.installation_id:
        if not github_bot.app_disponible():
            raise HTTPException(status_code=422, detail=t("github.sin_app"))
        ajustes["installation_id"] = body.installation_id
        ajustes.pop("token", None)
    elif token:
        ajustes["token"] = token
        ajustes.pop("installation_id", None)
    if not ajustes.get("token") and not ajustes.get("installation_id"):
        raise HTTPException(status_code=422, detail=t("github.sin_credencial"))
    await run_in_threadpool(_guard, meta.set_setting, body.project_id, github_bot.CLAVE, ajustes)
    return await run_in_threadpool(github_bot.estado, meta, body.project_id)


@router.post("/github/webhook")
async def github_webhook(request: Request) -> dict[str, Any]:
    """El aviso de GitHub cuando se cierra un pull request (D-191).

    No pide clave de Laplace —está en `PUBLIC_PATHS`, porque la llama GitHub—: lo que la
    cierra es la firma del cuerpo. Vale el secreto de la GitHub App de la instalación o el
    del proyecto que tenga conectado ese repositorio, y sólo para ese proyecto. Antes de
    comprobar la firma no se hace nada con lo que dice el cuerpo salvo buscar el
    repositorio, que es lo que dice qué secretos probar.
    """
    cuerpo = await request.body()
    firma = request.headers.get("X-Hub-Signature-256", "")
    evento = request.headers.get("X-GitHub-Event", "")
    try:
        datos = json.loads(cuerpo or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=t("github.webhook_cuerpo")) from exc
    repo = str((datos.get("repository") or {}).get("full_name") or "").lower()
    proyectos = await run_in_threadpool(_proyectos_del_repo, request, repo)
    secreto_app = os.environ.get(github_bot.ENV_WEBHOOK_SECRET, "")
    validos = [
        p
        for p, ajustes in proyectos
        if github_bot.firma_valida(cuerpo, firma, secreto_app)
        or github_bot.firma_valida(cuerpo, firma, ajustes.get("webhook_secret") or "")
    ]
    if not validos:
        raise HTTPException(status_code=401, detail=t("github.webhook_firma"))
    if evento == "ping":
        return {"ok": True, "projects": len(validos)}
    if evento != "pull_request" or datos.get("action") != "closed":
        return {"ignored": evento or "?", "fixed": []}
    pr = datos.get("pull_request") or {}
    arreglados = []
    for project_id in validos:
        arreglados += await run_in_threadpool(_pr_cerrado, _meta(request), project_id, pr)
    return {"fixed": arreglados}


def _proyectos_del_repo(request: Request, repo: str) -> list[tuple[str, dict[str, Any]]]:
    """Los proyectos de esta instalación con ese repositorio conectado."""
    if not repo:
        return []
    meta = _meta(request)
    salida = []
    for proyecto in _store(request).list_projects():
        ajustes = github_bot.leer(meta, proyecto.project_id)
        if str(ajustes.get("repo") or "").lower() == repo:
            salida.append((proyecto.project_id, ajustes))
    return salida


def _pr_cerrado(meta: Any, project_id: str, pr: dict[str, Any]) -> list[str]:
    """Apunta cómo acabó el PR de Laplace y, si se fusionó, marca su hallazgo arreglado.

    El PR se reconoce por su número **y** por su rama (`rama_para`), cuando GitHub la
    manda: un número suelto puede ser de otro PR si el repositorio se recreó. Lo que el
    usuario ignoró sigue ignorado. La hora es la de la fusión, no la de llegada del aviso:
    es la frontera desde la que el seguimiento mide si ha servido.
    """
    numero = pr.get("number")
    rama = str((pr.get("head") or {}).get("ref") or "")
    fusionado = bool(pr.get("merged"))
    momento = str(pr.get("merged_at") or datetime.now(timezone.utc).isoformat())
    if momento.endswith("Z"):
        momento = momento[:-1] + "+00:00"
    arreglados = []
    for clave_pr, guardado in _guard(
        meta.list_settings, project_id, github_bot.PREFIJO_PR
    ).items():
        finding_id = clave_pr[len(github_bot.PREFIJO_PR) :]
        if guardado.get("number") != numero:
            continue
        if rama and rama != github_bot.rama_para(finding_id):
            continue
        _guard(
            meta.set_setting,
            project_id,
            clave_pr,
            {**guardado, "state": "merged" if fusionado else "closed", "closed_at": momento},
        )
        if not fusionado:
            continue
        actual = _guard(meta.get_setting, project_id, clave(finding_id)) or {}
        if actual.get("status") == "ignorado":
            continue
        _guard(
            meta.set_setting,
            project_id,
            clave(finding_id),
            {
                "status": "arreglado",
                "at": momento,
                "note": t("github.fusionado", numero=numero, url=pr.get("html_url") or ""),
                "by": "github",
            },
        )
        logger.info("hallazgo %s arreglado al fusionar el PR #%s — %s", finding_id, numero,
                    project_id)
        arreglados.append(finding_id)
    return arreglados


class PullRequestIn(BaseModel):
    project_id: str
    finding_id: str = Field(max_length=500)
    days: int = Field(default=7, ge=1, le=90)


def _arreglo(store: Any, project_id: str, hallazgo: Any) -> dict[str, Any]:
    """Qué cambiar, dónde y con qué textos, según el arreglo del hallazgo (D-185, D-190).

    Lanza `SinPropuesta` con el motivo cuando no se puede proponer con seguridad.
    """
    from .insights.modelos import MIN_VUELTAS_BUCLE

    spans = (
        store.get_trace_spans(hallazgo.sample_trace_id, project_id)
        if hallazgo.sample_trace_id
        else []
    )
    # Las llamadas de ese paso; si el paso no se reconoce en los spans, todas las de la
    # traza, y `sitio` se queda con las llamadas al modelo.
    del_paso = [s for s in spans if s.step_key == hallazgo.step_key] or spans
    comun = {"titulo": hallazgo.title, "resumen": hallazgo.summary}

    if hallazgo.code_fix == "modelo" and hallazgo.model_change:
        de, a = hallazgo.model_change["from"], hallazgo.model_change["to"]
        ruta, linea = github_bot.sitio(del_paso, de)
        return {
            "ruta_anotada": ruta,
            "de": de,
            "a": a,
            "linea": linea,
            "titulo": t("pr.titulo", de=de, a=a),
            "cuerpo": t("pr.cuerpo", **comun, de=de, a=a, ruta=ruta, linea=linea),
            "mensaje": t("pr.commit", de=de, a=a),
        }

    if hallazgo.code_fix == "cache":
        ruta, linea = github_bot.sitio(del_paso, "")

        def _cache(fuente: str, _ruta: str) -> str:
            return arreglos_codigo.poner_cache(fuente, linea)

        return {
            "ruta_anotada": ruta,
            "cambiar": _cache,
            "titulo": t("pr.cache.titulo", ruta=ruta),
            "cuerpo": t("pr.cache.cuerpo", **comun, ruta=ruta, linea=linea),
            "mensaje": t("pr.cache.commit", ruta=ruta),
        }

    if hallazgo.code_fix == "tope":
        # La función raíz de la ejecución de ejemplo: la anota `@observe` (D-190).
        raiz = next((s for s in spans if not s.parent_span_id), None)
        attrs = (raiz.attributes if raiz is not None else None) or {}
        ruta, linea = attrs.get(github_bot.ATTR_RUTA), attrs.get(github_bot.ATTR_LINEA)
        if not ruta or not linea:
            raise github_bot.SinPropuesta("pr.sin_raiz")
        funcion = str(attrs.get("code.function.name") or raiz.name)
        tope = MIN_VUELTAS_BUCLE

        def _tope(fuente: str, _ruta: str) -> str:
            return arreglos_codigo.poner_tope(fuente, int(linea), tope)

        return {
            "ruta_anotada": str(ruta),
            "cambiar": _tope,
            "titulo": t("pr.tope.titulo", funcion=funcion, tope=tope),
            "cuerpo": t(
                "pr.tope.cuerpo", **comun, funcion=funcion, tope=tope, ruta=ruta, linea=linea
            ),
            "mensaje": t("pr.tope.commit", funcion=funcion, tope=tope),
        }

    raise github_bot.SinPropuesta("pr.sin_cambio")


def _abrir_pr(store: Any, meta: Any, project_id: str, body: PullRequestIn) -> dict[str, Any]:
    from .api import _window
    from .insights import detail as finding_detail

    ajustes = github_bot.leer(meta, project_id)
    if not github_bot.estado(meta, project_id).configured:
        raise HTTPException(status_code=409, detail=t("github.sin_configurar"))
    hallazgo = finding_detail(store, project_id, _window(body.days), body.finding_id)
    if hallazgo is None:
        raise HTTPException(status_code=404, detail=t("error.problema_no_aparece"))
    try:
        pr = github_bot.abrir(ajustes, body.finding_id, **_arreglo(store, project_id, hallazgo))
    except github_bot.SinPropuesta as exc:
        raise HTTPException(status_code=409, detail=t(exc.clave, **exc.datos)) from exc
    except github_bot.GitHubError as exc:
        raise HTTPException(status_code=502, detail=t(exc.clave, **exc.datos)) from exc
    _guard(
        meta.set_setting,
        project_id,
        github_bot.PREFIJO_PR + body.finding_id,
        {"url": pr.url, "number": pr.numero},
    )
    return {"url": pr.url, "number": pr.numero, "already_open": pr.ya_abierto}


@router.post("/github/pull-request")
async def open_pull_request(request: Request, body: PullRequestIn) -> dict[str, Any]:
    """Abre (o encuentra, si ya estaba) el pull request con el arreglo de un hallazgo."""
    # El proyecto va en el cuerpo, y por él acota el middleware (D-097), como en Stripe.
    return await run_in_threadpool(
        _abrir_pr, request.app.state.store, _meta(request), body.project_id, body
    )
