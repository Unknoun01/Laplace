"""Regla 10 — trabajo que podría ir por la API de lotes (D-199).

Parte del motor de detección (`laplace_backend.insights`, D-130).

OpenAI y Anthropic cobran la mitad por la misma llamada si se manda por su API de lotes:
el mismo modelo y la misma respuesta, que llega en horas en lugar de al momento. Desde
las trazas no se puede saber si alguien espera esa respuesta, así que la regla habla de
dos maneras:

* **Con la etiqueta `laplace-puede-esperar`** en la ejecución, el usuario lo ha dicho.
  Entonces hay dinero: lo que cuestan sus llamadas por el descuento que publica el
  proveedor.
* **Sin la etiqueta**, sólo se pregunta, y sin dinero en el total: ejecuciones sin
  usuario ni sesión en un proyecto que sí los pone en otras. Es un indicio, no una
  prueba. Si el proyecto no los pone nunca, no hay con qué comparar y se calla.

Sólo cuentan las llamadas que no esperan la respuesta de otra de la misma ejecución:
por lotes, cada salto de una cadena puede tardar hasta un día. Las encadenadas se
cuentan aparte, para decirlo.

Va la última: el descuento se aplica a lo que queda del paso después de lo que ya
reclaman las demás reglas, en proporción, y nunca al paso entero (D-117).
"""

from __future__ import annotations

from laplace.semconv import PUEDE_ESPERAR_TAG

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import DeferrableGroup, WindowSummary
from ..textos import t
from .modelos import (
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _money,
    _projection_sentence,
    _scope_label,
    _to_monthly,
    window_label,
)

KIND = "lotes"

#: Llamadas por paso y modelo para hablar: por debajo, mandar un lote no compensa el
#: trabajo de montarlo.
MIN_LLAMADAS_LOTES = 20
#: Ejecuciones del proyecto con usuario o sesión para que su ausencia diga algo. Si el
#: proyecto no los pone nunca, que una ejecución no los lleve no distingue nada.
MIN_TRAZAS_CON_PERSONA = 20

_CODIGO_POR_FUENTE = {
    "openai": "lotes.arreglo.lotes.codigo.openai",
    "anthropic": "lotes.arreglo.lotes.codigo.anthropic",
}


def factor_por_lotes(modelo: str) -> float | None:
    """Lo que se paga por lotes sobre la tarifa inmediata (0,5 = la mitad), si el
    proveedor del modelo lo publica. `None` si no lo sabemos: nunca se supone."""
    tabla = get_price_table()
    precio = tabla.lookup(modelo)
    if precio is None:
        return None
    fuente = tabla.sources.get(precio.source)
    return fuente.batch_multiplier if fuente else None


def _fuente(modelo: str) -> str:
    precio = get_price_table().lookup(modelo)
    return precio.source if precio else ""


def _parte(grupo: DeferrableGroup) -> tuple[bool, int, int, float, int, int] | None:
    """La parte del grupo de la que se habla: la declarada si llega al mínimo, y si no
    la que no lleva a nadie detrás, si el proyecto da con qué comparar."""
    if grupo.declared_calls >= MIN_LLAMADAS_LOTES:
        return (
            True, grupo.declared_calls, grupo.declared_traces, grupo.declared_cost_usd,
            grupo.declared_unknown_cost_spans, grupo.declared_assumed_rate_spans,
        )
    if grupo.open_calls >= MIN_LLAMADAS_LOTES and (
        grupo.traces_with_person >= MIN_TRAZAS_CON_PERSONA
    ):
        return (
            False, grupo.open_calls, grupo.open_traces, grupo.open_cost_usd,
            grupo.open_unknown_cost_spans, grupo.open_assumed_rate_spans,
        )
    return None


def _queda(reclamado: float, coste_paso: float) -> float:
    """La parte del paso que no reclaman ya otras reglas (D-117)."""
    if coste_paso <= 0:
        return 1.0
    return max(coste_paso - reclamado, 0.0) / coste_paso


def hallazgo(
    grupo: DeferrableGroup,
    reclamado: float,
    coste_paso: float,
    summary: WindowSummary,
    days: float,
    base: float | None,
) -> Finding | None:
    parte = _parte(grupo)
    if parte is None:
        return None
    declarada, llamadas, trazas, coste, sin_tarifa, asumida = parte
    factor = factor_por_lotes(grupo.model)
    queda = _queda(reclamado, coste_paso)
    ahorro = coste * (1 - factor) * queda if factor is not None and coste > 0 else 0.0
    if factor is not None and coste > 0 and queda <= 0:
        return None  # las demás reglas ya reclaman el paso entero: no queda nada

    con_tarifa = get_price_table().lookup(grupo.model) is not None
    if ahorro > 0:
        gasto = t(
            "lotes.gasto.cuesta" if declarada else "lotes.gasto.hasta",
            ahorro=_money(ahorro),
        )
    elif con_tarifa:
        gasto = t("lotes.gasto.sin_descuento", modelo=grupo.model)
    else:
        gasto = t("hallazgo.fuera_de_tabla", modelo=grupo.model)

    dinero = ahorro if declarada else 0.0
    if dinero > 0:
        no_hay = ""
    elif not declarada:
        no_hay = t("lotes.sin_declarar")
    elif con_tarifa:
        no_hay = t("lotes.gasto.sin_descuento", modelo=grupo.model)
    else:
        no_hay = t("hallazgo.fuera_de_tabla", modelo=grupo.model)

    clave = "lotes" if declarada else "lotes.abierta"
    return Finding(
        id=f"{KIND}:{grupo.step_key}:{grupo.model}",
        kind=KIND,
        title=t(f"{clave}.titulo", paso=grupo.name),
        lead=t(f"{clave}.lead"),
        summary=t(
            f"{clave}.resumen",
            paso=grupo.name,
            llamadas=_miles(llamadas),
            trazas=_miles(trazas),
            gasto=gasto,
        ),
        window_waste_usd=dinero,
        cost_unavailable=no_hay,
        monthly_saving_usd=_to_monthly(dinero, base) if dinero > 0 else None,
        observed_days=days,
        costs_money=dinero > 0,
        **_floor_flags(sin_tarifa, asumida, [grupo.model]),
        difficulty="mid",
        difficulty_label=t("dificultad.un_rato"),
        scope_label=_scope_label(trazas, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=grupo.name),
            TechItem(label=t("tec.modelo"), value=grupo.model),
            TechItem(label=t("tec.llamadas_lotes"), value=str(llamadas)),
            TechItem(label=t("tec.encadenadas"), value=str(grupo.chained)),
            TechItem(label=t("tec.trazas"), value=str(trazas)),
            TechItem(
                label=t("tec.factor_lotes"),
                value=cifras.porcentaje(factor) if factor is not None else "—",
            ),
        ],
        sample_trace_id=grupo.sample_trace_id,
        step_key=grupo.step_key,
    )


def detalle(
    finding: Finding,
    grupo: DeferrableGroup,
    reclamado: float,
    coste_paso: float,
    consulta: str,
) -> FindingDetail:
    declarada, llamadas, trazas, coste, _, _ = _parte(grupo)  # type: ignore[misc]
    factor = factor_por_lotes(grupo.model)
    queda = _queda(reclamado, coste_paso)
    detalle = FindingDetail(**finding.model_dump())
    detalle.what_happens = t(
        "lotes.que_pasa" if declarada else "lotes.abierta.que_pasa",
        paso=grupo.name,
        llamadas=_miles(llamadas),
        trazas=_miles(trazas),
        encadenadas=(
            t("lotes.encadenadas", n=_miles(grupo.chained)) if grupo.chained else ""
        ),
    )
    detalle.why = t("lotes.por_que")
    detalle.detection_explanation = t(
        "lotes.deteccion",
        etiqueta=PUEDE_ESPERAR_TAG,
        persona=MIN_TRAZAS_CON_PERSONA,
        minimo=MIN_LLAMADAS_LOTES,
    )
    detalle.detection_query = consulta.strip()

    codigo = _CODIGO_POR_FUENTE.get(_fuente(grupo.model))
    lotes = FixStep(
        title=t("lotes.arreglo.lotes.titulo"),
        body=t("lotes.arreglo.lotes.texto"),
        code=t(codigo) if codigo else "",
    )
    marcar = FixStep(
        title=t("lotes.arreglo.marcar.titulo"),
        body=t("lotes.arreglo.marcar.texto"),
        code=t("lotes.arreglo.marcar.codigo", etiqueta=PUEDE_ESPERAR_TAG),
    )
    detalle.fix_steps = [lotes] if declarada else [marcar, lotes]

    if factor is not None and coste > 0:
        ahorro = coste * (1 - factor) * queda
        detalle.savings_calculation = t(
            "lotes.ahorro" if declarada else "lotes.abierta.ahorro",
            llamadas=_miles(llamadas),
            coste=cifras.dinero_exacto(coste),
            factor=cifras.porcentaje(factor),
            ahorro=cifras.dinero_exacto(ahorro),
            ventana=window_label(finding.observed_days),
            reclamado=(
                t("lotes.ahorro.reclamado", parte=cifras.porcentaje(queda))
                if queda < 1
                else ""
            ),
            proyeccion=_projection_sentence(finding) if declarada else "",
        )
    detalle.savings_note = t("lotes.nota")
    return detalle
