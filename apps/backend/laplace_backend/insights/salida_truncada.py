"""Regla 7 — una respuesta cortada por el tope de salida, y rehecha (D-193).

Parte del motor de detección (`laplace_backend.insights`, D-130).

Cuando el modelo llega al tope de tokens de salida, para a media frase y el proveedor lo
dice en el motivo de fin (`length`, `max_tokens`, `max_output_tokens`). Si el agente se
da cuenta y vuelve a llamar al mismo paso, lo que pagó por la primera respuesta lo ha
tirado entero: entrada y salida. Ese dinero es el del hallazgo, medido.

Lo que **no** se cobra es la respuesta cortada que nadie rehízo. Puede que el agente la
usara a medias, o que la continuara: no lo sabemos, y un «no lo sabemos» tiene que ser
verdad. Se cuenta en la ficha, sin dinero.

Para no reclamar dos veces el mismo dinero (D-117), el almacén deja fuera las cortadas
que ya cuentan la repetición exacta y los bucles, y el motor descuenta las de aquí de lo
que miran después el modelo caro, el contexto fijo y la caché compartida.
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..storage.base import MOTIVOS_DE_CORTE, RedoneGroup, WindowSummary
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

KIND = "salida_truncada"

#: Cortadas y rehechas en la ventana para hablar. Con menos, una pregunta rara que pidió
#: una respuesta larga basta para disparar, y eso no es un patrón del agente.
MIN_TRUNCADAS_REHECHAS = 5


def hallazgo(
    grupo: RedoneGroup, summary: WindowSummary, days: float, base: float | None
) -> Finding:
    tokens = grupo.redone_input_tokens + grupo.redone_output_tokens
    cuesta = grupo.redone_cost_usd > 0
    sin_tarifa = not cuesta and tokens > 0

    gasto = ""
    if cuesta:
        gasto = t("truncada.gasto.cuesta", coste=_money(grupo.redone_cost_usd))
    elif sin_tarifa:
        gasto = t("truncada.gasto.sin_tarifa", tokens=_miles(tokens))

    return Finding(
        id=f"{KIND}:{grupo.step_key}:{grupo.model}",
        kind=KIND,
        title=tn("truncada.titulo", grupo.redone, paso=grupo.name, veces=_miles(grupo.redone)),
        lead=t("truncada.lead"),
        summary=tn(
            "truncada.resumen",
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
        difficulty_label=t("dificultad.subir_tope"),
        scope_label=_scope_label(grupo.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=grupo.name),
            TechItem(label=t("tec.modelo"), value=grupo.model),
            TechItem(label=t("tec.cortadas_rehechas"), value=str(grupo.redone)),
            TechItem(label=t("tec.cortadas_sin_rehacer"), value=str(grupo.not_redone)),
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
        "truncada.que_pasa",
        paso=grupo.name,
        rehechas=_miles(grupo.redone),
        trazas=_miles(grupo.traces),
        solas=(
            tn("truncada.solas", grupo.not_redone, n=_miles(grupo.not_redone))
            if grupo.not_redone
            else t("truncada.solas.ninguna")
        ),
    )
    detalle.why = t("truncada.por_que")
    detalle.detection_explanation = t(
        "truncada.deteccion",
        motivos=", ".join(f"`{m}`" for m in MOTIVOS_DE_CORTE),
        minimo=MIN_TRUNCADAS_REHECHAS,
    )
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("truncada.arreglo.tope.titulo"),
            body=t("truncada.arreglo.tope.texto"),
            code=t("truncada.arreglo.tope.codigo"),
        ),
        FixStep(
            title=t("truncada.arreglo.corta.titulo"),
            body=t("truncada.arreglo.corta.texto"),
        ),
        FixStep(
            title=t("truncada.arreglo.sigue.titulo"),
            body=t("truncada.arreglo.sigue.texto"),
            advanced=True,
        ),
    ]
    rehechas = tn(
        "truncada.rehechas",
        grupo.traces,
        veces=_miles(grupo.redone),
        n=_miles(grupo.traces),
        ventana=window_label(finding.observed_days),
    )
    if finding.costs_money:
        detalle.savings_calculation = t(
            "truncada.ahorro.cuesta",
            rehechas=rehechas,
            coste=cifras.dinero_exacto(grupo.redone_cost_usd),
            proyeccion=_projection_sentence(finding),
        )
        detalle.savings_note = t("truncada.nota.cuesta")
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
