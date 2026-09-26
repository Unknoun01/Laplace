"""Regla 1 — el mismo paso repetido dentro de una traza.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..pasos import identificador
from ..storage.base import RepeatedGroup, WindowSummary
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

    if cuesta:
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. La primera ya trae la respuesta; "
            f"las demás se pagan igual."
        )
    elif sin_tarifa:
        # Se gastan tokens de verdad, pero el modelo no está en la tabla de precios: se
        # dice lo que se mide y se dice por qué no hay euros.
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. Las copias de más gastan "
            f"{_miles(tokens_de_mas)} tokens y {_seconds(group.extra_duration_ms)} de espera. "
            "No podemos decirte cuánto dinero es: ese modelo no tiene tarifa conocida."
        )
    else:
        # Honestidad: una herramienta repetida no gasta tokens. Lo que se pierde es tiempo.
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. No gasta tokens de más, pero "
            f"cada vuelta añade espera: {_seconds(group.extra_duration_ms)} tirados en total."
        )

    return Finding(
        id=f"repeticion:{group.step_key}",
        kind="repeticion",
        title=f"Tu agente repite «{group.name}» hasta {group.max_per_trace} veces seguidas",
        summary=resumen,
        lead=(
            "Con los mismos datos cada vez: la primera ya trae la respuesta."
            if cuesta
            else f"Con los mismos datos cada vez. Gasta {_miles(tokens_de_mas)} tokens de más."
            if sin_tarifa
            else "Con los mismos datos cada vez. No gasta tokens, pero añade espera."
        ),
        window_waste_usd=group.extra_cost_usd,
        monthly_saving_usd=monthly,
        observed_days=days,
        window_waste_ms=group.extra_duration_ms,
        window_waste_tokens=tokens_de_mas,
        cost_unavailable=(
            "ese modelo no tiene tarifa conocida, así que no podemos ponerle precio"
            if sin_tarifa
            else ""
        ),
        **_floor_flags(
            group.extra_unknown_cost_spans, group.extra_assumed_rate_spans, [group.model]
        ),
        difficulty="easy",
        difficulty_label="Una línea en el prompt",
        scope_label=_scope_label(group.traces, summary.traces),
        costs_money=cuesta,
        tech=[
            TechItem(label="señal", value="repetición exacta"),
            TechItem(label="laplace.dedup_hash", value=group.dedup_hash),
            TechItem(label="tipo", value=group.span_type),
            TechItem(label="pasos de más", value=str(group.extra_spans)),
            TechItem(label="trazas", value=str(group.traces)),
        ],
        sample_trace_id=group.sample_trace_id,
        step_key=group.step_key,
    )


def _repetition_detail(
    finding: Finding, group: RepeatedGroup, evidence: list[Span], query: str
) -> FindingDetail:
    cuesta = group.extra_cost_usd > 0
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = (
        f"Dentro de una misma ejecución, «{group.name}» se llama {group.max_per_trace} veces "
        f"con los mismos datos de entrada. La respuesta es idéntica todas las veces: la "
        f"primera ya resolvía la pregunta."
    )
    detalle.why = (
        "El agente no se da cuenta de que ya tiene la respuesta. La recibe, vuelve a leer la "
        "petición y, como en sus instrucciones no hay nada que le diga cuándo parar, decide "
        "buscar otra vez. Se queda dando vueltas hasta agotar el número máximo de intentos.\n\n"
        "Es el fallo más común en agentes que usan herramientas, y casi nunca da error: el "
        "agente acaba respondiendo bien. Sólo que hace el trabajo varias veces."
    )
    detalle.detection_explanation = (
        f"Regla activa: **{MIN_REPEATS} o más pasos con el mismo `laplace.dedup_hash` dentro "
        f"de una misma traza**. El hash lo calcula la ingesta a partir del tipo de span, su "
        f"nombre, el modelo y la entrada normalizada, así que un cambio de orden de claves o "
        f"de espaciado no cuenta como entrada distinta. Se agrupa primero por traza y luego "
        f"por hash: repetirse dentro de una ejecución es un bucle, aparecer en ejecuciones "
        f"distintas es uso normal."
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title="Dile al agente que no repita",
            body=(
                "Añade una línea a sus instrucciones. Es un minuto de trabajo y resuelve la "
                "mayoría de los casos."
            ),
            code=(
                "# en tu prompt de sistema\n"
                "Si ya has consultado una herramienta y tienes la respuesta,\n"
                "no vuelvas a consultarla. Responde con lo que ya sabes."
            ),
        ),
        FixStep(
            title="Guarda la respuesta",
            body=(
                "Si vuelve a pedir lo mismo dentro de la misma ejecución, devuélvele lo que "
                "ya tienes en vez de consultar otra vez."
            ),
            code=(
                "from functools import lru_cache\n\n"
                "@lru_cache(maxsize=256)\n"
                f"def {identificador(group.name)}(...):\n"
                "    ..."
            ),
        ),
        FixStep(
            title="Pon un tope de seguridad",
            body=(
                "Aunque arregles lo anterior, limita las vueltas. Así, si algún día vuelve a "
                "pasar, te cuesta unas pocas llamadas y no una cascada."
            ),
            code=("for vuelta in range(5):\n    ...  # y sal del bucle al tener respuesta"),
            advanced=True,
        ),
    ]

    if cuesta:
        detalle.savings_calculation = (
            f"{_miles(group.extra_spans)} pasos de más en {_miles(group.traces)} trazas durante "
            f"{window_label(finding.observed_days)}, que suman "
            f"{cifras.dinero_exacto(group.extra_cost_usd)} de coste ya gastado."
            f"{_projection_sentence(finding)} Sólo cuenta las ocurrencias "
            f"posteriores a la primera de cada traza; la primera es trabajo legítimo."
        )
        detalle.savings_note = (
            "El dinero ya gastado está medido, no estimado: es la suma del coste de las "
            "ocurrencias que sobran. Lo estimado es la proyección a un mes, que sale de "
            "suponer que el ritmo se mantiene."
            if finding.monthly_saving_usd is not None
            else "El dinero ya gastado está medido, no estimado: es la suma del coste de "
            "las ocurrencias que sobran. Lo que todavía no podemos decirte es a cuánto "
            "va el mes."
        )
    else:
        detalle.savings_calculation = (
            f"{_miles(group.extra_spans)} pasos de más en {_miles(group.traces)} trazas. "
            f"Estos pasos no "
            f"consumen tokens, así que el ahorro en dinero es cero: lo que se recupera es "
            f"tiempo, {_seconds(group.extra_duration_ms)} en la ventana analizada. Si en tu "
            f"agente cada vuelta arrastrase una llamada al modelo, aparecería además como un "
            f"hallazgo de repetición sobre spans `llm`."
        )
        detalle.savings_note = (
            "Este problema no te cuesta dinero, te cuesta espera. Arreglarlo hace que tu "
            "agente responda antes."
        )

    detalle.evidence = evidence
    return detalle
