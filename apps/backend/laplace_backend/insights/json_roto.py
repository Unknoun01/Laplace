"""Regla 8 — una salida con el JSON roto, y rehecha (D-194).

Parte del motor de detección (`laplace_backend.insights`, D-130).

Un paso que pide JSON y recibe algo que empieza como JSON pero no se puede leer —una
coma de más, una comilla sin cerrar, texto pegado detrás— suele acabar llamando otra vez
al modelo. Lo que costó la primera respuesta se tiró entero, y eso es lo que se cobra.
La ingesta marca cada salida (`output_json`: `ok`, `roto` o vacía si no intenta ser
JSON), así que los dos almacenes leen la misma señal.

Va después de la salida truncada y no se pisa con ella: una respuesta cortada por el
tope casi siempre deja el JSON roto, pero su arreglo es el tope, y es de aquélla. Aquí
sólo entran las rotas que terminaron por su cuenta. Como en las demás, lo que reclaman
la repetición y los bucles queda fuera, y lo que reclama ésta se descuenta del modelo
caro, el contexto fijo y la caché compartida (D-117).
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..storage.base import RedoneGroup, WindowSummary
from ..textos import t, tn
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
    _seconds,
    _to_monthly,
    window_label,
)

KIND = "json_roto"

#: Rotas y rehechas en la ventana para hablar: el mismo mínimo que la salida truncada.
MIN_JSON_ROTO_REHECHAS = 5


def hallazgo(
    grupo: RedoneGroup, summary: WindowSummary, days: float, base: float | None
) -> Finding:
    tokens = grupo.redone_input_tokens + grupo.redone_output_tokens
    cuesta = grupo.redone_cost_usd > 0
    sin_tarifa = not cuesta and tokens > 0

    gasto = ""
    if cuesta:
        gasto = t("jsonroto.gasto.cuesta", coste=_money(grupo.redone_cost_usd))
    elif sin_tarifa:
        gasto = t("jsonroto.gasto.sin_tarifa", tokens=_miles(tokens))

    return Finding(
        id=f"{KIND}:{grupo.step_key}:{grupo.model}",
        kind=KIND,
        title=tn("jsonroto.titulo", grupo.redone, paso=grupo.name, veces=_miles(grupo.redone)),
        lead=t("jsonroto.lead"),
        summary=tn(
            "jsonroto.resumen",
            grupo.redone,
            paso=grupo.name,
            veces=_miles(grupo.redone),
            trazas=_miles(grupo.traces),
            gasto=gasto,
            espera=_seconds(grupo.redone_duration_ms),
        ),
        window_waste_usd=grupo.redone_cost_usd,
        window_waste_tokens=tokens,
        window_waste_ms=grupo.redone_duration_ms,
        cost_unavailable=(
            t("hallazgo.fuera_de_tabla", modelo=grupo.model) if sin_tarifa else ""
        ),
        monthly_saving_usd=_to_monthly(grupo.redone_cost_usd, base) if cuesta else None,
        observed_days=days,
        costs_money=cuesta,
        **_floor_flags(
            grupo.redone_unknown_cost_spans, grupo.redone_assumed_rate_spans, [grupo.model]
        ),
        difficulty="easy",
        difficulty_label=t("dificultad.salida_estructurada"),
        scope_label=_scope_label(grupo.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=grupo.name),
            TechItem(label=t("tec.modelo"), value=grupo.model),
            TechItem(label=t("tec.rotas_rehechas"), value=str(grupo.redone)),
            TechItem(label=t("tec.rotas_sin_rehacer"), value=str(grupo.not_redone)),
            TechItem(label=t("tec.trazas"), value=str(grupo.traces)),
        ],
        sample_trace_id=grupo.sample_trace_id,
        step_key=grupo.step_key,
    )


def detalle(
    finding: Finding, grupo: RedoneGroup, evidence: list[Span], consulta: str
) -> FindingDetail:
    detalle = FindingDetail(**finding.model_dump())
    detalle.what_happens = t(
        "jsonroto.que_pasa",
        paso=grupo.name,
        rehechas=_miles(grupo.redone),
        trazas=_miles(grupo.traces),
        solas=(
            tn("jsonroto.solas", grupo.not_redone, n=_miles(grupo.not_redone))
            if grupo.not_redone
            else t("jsonroto.solas.ninguna")
        ),
    )
    detalle.why = t("jsonroto.por_que")
    detalle.detection_explanation = t("jsonroto.deteccion", minimo=MIN_JSON_ROTO_REHECHAS)
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("jsonroto.arreglo.estructurada.titulo"),
            body=t("jsonroto.arreglo.estructurada.texto"),
            code=t("jsonroto.arreglo.estructurada.codigo"),
        ),
        FixStep(
            title=t("jsonroto.arreglo.reparar.titulo"),
            body=t("jsonroto.arreglo.reparar.texto"),
        ),
        FixStep(
            title=t("jsonroto.arreglo.error.titulo"),
            body=t("jsonroto.arreglo.error.texto"),
            advanced=True,
        ),
    ]
    rehechas = tn(
        "jsonroto.rehechas",
        grupo.traces,
        veces=_miles(grupo.redone),
        n=_miles(grupo.traces),
        ventana=window_label(finding.observed_days),
    )
    if finding.costs_money:
        detalle.savings_calculation = t(
            "jsonroto.ahorro.cuesta",
            rehechas=rehechas,
            coste=cifras.dinero_exacto(grupo.redone_cost_usd),
            proyeccion=_projection_sentence(finding),
        )
        detalle.savings_note = t("jsonroto.nota.cuesta")
    elif finding.window_waste_tokens > 0:
        detalle.savings_calculation = t(
            "truncada.ahorro.sin_tarifa",
            rehechas=rehechas,
            tokens=_miles(finding.window_waste_tokens),
            modelo=grupo.model or t("hallazgo.ese_modelo"),
        )
        detalle.savings_note = t("bucle.nota.sin_tarifa")
    else:
        detalle.savings_calculation = t(
            "truncada.ahorro.tiempo",
            rehechas=rehechas,
            espera=_seconds(grupo.redone_duration_ms),
        )
        detalle.savings_note = t("ahorro.nota.solo_tiempo")
    detalle.evidence = evidence
    return detalle
