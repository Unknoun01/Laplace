"""Regla 4 — un bucle que da vueltas sin avanzar.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..pasos import identificador
from ..storage.base import LoopGroup, WindowSummary
from ..textos import t, tn
from .modelos import (
    MAX_SALIDAS_BUCLE,
    MIN_VUELTAS_BUCLE,
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _money,
    _projection_sentence,
    _scope_label,
    _seconds,
    _to_monthly,
    window_label,
)

# ---------------------------------------------------------------------------------
# Regla 4 — un bucle que da vueltas sin avanzar
# ---------------------------------------------------------------------------------


def _loop_finding(
    group: LoopGroup, summary: WindowSummary, days: float, base: float | None
) -> Finding:
    """Un paso que se repite muchas veces por ejecución sin llegar a ninguna parte.

    Esta regla existía en la idea del producto desde el principio y no estaba escrita:
    lo único que había era repetición **exacta**, y un bucle de verdad casi nunca
    repite exacto —lleva el número de intento dentro—. Seis vueltas por ejecución, 312
    llamadas al modelo en una tanda de media hora, y el producto callado (D-109).

    Las dos mitades de la señal: las entradas se parecen salvo en los números, y hay
    muy pocas salidas distintas. Sin la segunda, un bucle que procesa seis pedidos
    distintos —trabajo legítimo— saldría señalado.
    """
    tokens = int(group.extra_input_tokens + group.extra_output_tokens)
    cuesta = group.extra_cost_usd > 0
    sin_tarifa = not cuesta and tokens > 0

    gasto = ""
    if cuesta:
        gasto = t("bucle.gasto.cuesta", coste=_money(group.extra_cost_usd))
    elif sin_tarifa:
        gasto = t("bucle.gasto.sin_tarifa", tokens=_miles(tokens))

    return Finding(
        # Por paso y no por `loop_hash`: el hash es de una entrada concreta, y el
        # hallazgo es del paso entero (D-135).
        id=f"bucle:{group.step_key or group.loop_hash}",
        kind="bucle",
        title=t("bucle.titulo", paso=group.name, veces=group.max_per_trace),
        lead=t("bucle.lead"),
        summary=tn(
            "bucle.resumen",
            max(group.distinct_outputs, 1),
            paso=group.name,
            veces=group.max_per_trace,
            gasto=gasto,
            espera=_seconds(group.extra_duration_ms),
        ),
        window_waste_usd=group.extra_cost_usd,
        window_waste_tokens=tokens,
        window_waste_ms=group.extra_duration_ms,
        cost_unavailable=t("hallazgo.sin_tarifa") if sin_tarifa else "",
        monthly_saving_usd=_to_monthly(group.extra_cost_usd, base) if cuesta else None,
        observed_days=days,
        costs_money=cuesta,
        **_floor_flags(group.extra_unknown_cost_spans, 0),
        difficulty="mid",
        difficulty_label=t("dificultad.condicion_salida"),
        scope_label=_scope_label(group.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.senal"), value=t("tec.senal.bucle")),
            TechItem(label="laplace.loop_hash", value=group.loop_hash),
            TechItem(label=t("tec.tipo"), value=group.span_type),
            TechItem(label=t("tec.vueltas_de_mas"), value=str(group.extra_spans)),
            TechItem(label=t("tec.entradas_distintas"), value=str(group.distinct_inputs)),
            TechItem(label=t("tec.salidas_distintas"), value=str(group.distinct_outputs)),
            TechItem(label=t("tec.trazas"), value=str(group.traces)),
        ],
        sample_trace_id=group.sample_trace_id,
        step_key=group.step_key,
    )


def _loop_detail(
    finding: Finding, group: LoopGroup, evidence: list[Span], query: str
) -> FindingDetail:
    """La ficha de un bucle.

    No existía. La regla entró por `detect()` y nunca salió por `detail()`, así que el
    hallazgo que más dinero devolvía del proyecto de demo llevaba a un 404 que el
    usuario leía como «enhorabuena, ya no lo tienes» (D-113).
    """
    cuesta = group.extra_cost_usd > 0
    detalle = FindingDetail(**finding.model_dump())

    salidas = tn("bucle.salidas", max(group.distinct_outputs, 1))
    detalle.what_happens = t(
        "bucle.que_pasa",
        paso=group.name,
        veces=group.max_per_trace,
        llamadas=_miles(group.total_spans),
        salidas=salidas,
    )
    detalle.why = t("bucle.por_que")
    detalle.detection_explanation = t(
        "bucle.deteccion", minimo=MIN_VUELTAS_BUCLE, maximo=MAX_SALIDAS_BUCLE
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title=t("bucle.arreglo.salida.titulo"),
            body=t("bucle.arreglo.salida.texto"),
            code=t("bucle.arreglo.salida.codigo", funcion=identificador(group.name)),
        ),
        FixStep(
            title=t("bucle.arreglo.repite.titulo"),
            body=t("bucle.arreglo.repite.texto"),
        ),
        FixStep(
            title=t("bucle.arreglo.tope.titulo"),
            body=t("bucle.arreglo.tope.texto"),
            advanced=True,
        ),
    ]

    vueltas = tn(
        "bucle.vueltas",
        group.traces,
        vueltas=_miles(group.extra_spans),
        n=_miles(group.traces),
        ventana=window_label(finding.observed_days),
    )
    if cuesta:
        detalle.savings_calculation = t(
            "bucle.ahorro.cuesta",
            vueltas=vueltas,
            coste=cifras.dinero_exacto(group.extra_cost_usd),
            proyeccion=_projection_sentence(finding),
        )
        detalle.savings_note = (
            t("bucle.nota.medido_y_proyectado")
            if finding.monthly_saving_usd is not None
            else t("bucle.nota.medido_sin_mes")
        )
    elif finding.window_waste_tokens > 0:
        detalle.savings_calculation = t(
            "bucle.ahorro.sin_tarifa",
            vueltas=vueltas,
            tokens=_miles(finding.window_waste_tokens),
            espera=_seconds(group.extra_duration_ms),
            modelo=group.model or t("hallazgo.ese_modelo"),
        )
        detalle.savings_note = t("bucle.nota.sin_tarifa")
    else:
        detalle.savings_calculation = t(
            "bucle.ahorro.tiempo", vueltas=vueltas, espera=_seconds(group.extra_duration_ms)
        )
        detalle.savings_note = t("ahorro.nota.solo_tiempo")

    detalle.evidence = evidence
    return detalle
