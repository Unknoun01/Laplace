"""Regla 1 — el mismo paso repetido dentro de una traza.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..pasos import identificador
from ..storage.base import RepeatedGroup, WindowSummary
from ..textos import t
from .modelos import (
    MIN_REPEATS,
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _projection_sentence,
    _scope_label,
    _seconds,
    _to_monthly,
    window_label,
)

# ---------------------------------------------------------------------------------
# Regla 1 — el mismo paso repetido dentro de una traza
# ---------------------------------------------------------------------------------


def _repetition_finding(
    group: RepeatedGroup, summary: WindowSummary, days: float, base: float | None
) -> Finding:
    monthly = _to_monthly(group.extra_cost_usd, base)
    cuesta = group.extra_cost_usd > 0
    # Los tokens de las copias sobrantes se miden siempre; el dinero sólo si hay
    # tarifa. Decir «no gasta tokens de más» porque el coste salió 0 era convertir
    # «no lo sabemos» en «no cuesta» (D-107).
    tokens_de_mas = int(group.extra_input_tokens + group.extra_output_tokens)
    sin_tarifa = not cuesta and tokens_de_mas > 0

    valores = {"paso": group.name, "veces": group.max_per_trace}
    if cuesta:
        resumen = t("repeticion.resumen.cuesta", **valores)
    elif sin_tarifa:
        # Se gastan tokens de verdad, pero el modelo no está en la tabla de precios: se
        # dice lo que se mide y se dice por qué no hay dinero.
        resumen = t(
            "repeticion.resumen.sin_tarifa",
            **valores,
            tokens=_miles(tokens_de_mas),
            espera=_seconds(group.extra_duration_ms),
        )
    else:
        # Honestidad: una herramienta repetida no gasta tokens. Lo que se pierde es tiempo.
        resumen = t(
            "repeticion.resumen.tiempo", **valores, espera=_seconds(group.extra_duration_ms)
        )

    return Finding(
        id=f"repeticion:{group.step_key}",
        kind="repeticion",
        title=t("repeticion.titulo", **valores),
        summary=resumen,
        lead=(
            t("repeticion.lead.cuesta")
            if cuesta
            else t("repeticion.lead.sin_tarifa", tokens=_miles(tokens_de_mas))
            if sin_tarifa
            else t("repeticion.lead.tiempo")
        ),
        window_waste_usd=group.extra_cost_usd,
        monthly_saving_usd=monthly,
        observed_days=days,
        window_waste_ms=group.extra_duration_ms,
        window_waste_tokens=tokens_de_mas,
        cost_unavailable=t("hallazgo.sin_tarifa") if sin_tarifa else "",
        **_floor_flags(
            group.extra_unknown_cost_spans, group.extra_assumed_rate_spans, [group.model]
        ),
        difficulty="easy",
        difficulty_label=t("dificultad.linea_prompt"),
        scope_label=_scope_label(group.traces, summary.traces),
        costs_money=cuesta,
        tech=[
            TechItem(label=t("tec.senal"), value=t("tec.senal.repeticion_exacta")),
            TechItem(label="laplace.dedup_hash", value=group.dedup_hash),
            TechItem(label=t("tec.tipo"), value=group.span_type),
            TechItem(label=t("tec.pasos_de_mas"), value=str(group.extra_spans)),
            TechItem(label=t("tec.trazas"), value=str(group.traces)),
        ],
        sample_trace_id=group.sample_trace_id,
        step_key=group.step_key,
    )


def _repetition_detail(
    finding: Finding, group: RepeatedGroup, evidence: list[Span], query: str
) -> FindingDetail:
    cuesta = group.extra_cost_usd > 0
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = t(
        "repeticion.que_pasa", paso=group.name, veces=group.max_per_trace
    )
    detalle.why = t("repeticion.por_que")
    detalle.detection_explanation = t("repeticion.deteccion", minimo=MIN_REPEATS)
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title=t("repeticion.arreglo.no_repetir.titulo"),
            body=t("repeticion.arreglo.no_repetir.texto"),
            code=t("repeticion.arreglo.no_repetir.codigo"),
        ),
        FixStep(
            title=t("repeticion.arreglo.guardar.titulo"),
            body=t("repeticion.arreglo.guardar.texto"),
            code=(
                "from functools import lru_cache\n\n"
                "@lru_cache(maxsize=256)\n"
                f"def {identificador(group.name)}(...):\n"
                "    ..."
            ),
        ),
        FixStep(
            title=t("repeticion.arreglo.tope.titulo"),
            body=t("repeticion.arreglo.tope.texto"),
            code=t("repeticion.arreglo.tope.codigo"),
            advanced=True,
        ),
    ]

    if cuesta:
        detalle.savings_calculation = t(
            "repeticion.ahorro.cuesta",
            pasos=_miles(group.extra_spans),
            trazas=_miles(group.traces),
            ventana=window_label(finding.observed_days),
            coste=cifras.dinero_exacto(group.extra_cost_usd),
            proyeccion=_projection_sentence(finding),
        )
        detalle.savings_note = (
            t("ahorro.nota.medido_y_proyectado")
            if finding.monthly_saving_usd is not None
            else t("ahorro.nota.medido_sin_mes")
        )
    else:
        detalle.savings_calculation = t(
            "repeticion.ahorro.tiempo",
            pasos=_miles(group.extra_spans),
            trazas=_miles(group.traces),
            espera=_seconds(group.extra_duration_ms),
        )
        detalle.savings_note = t("ahorro.nota.solo_tiempo")

    detalle.evidence = evidence
    return detalle
