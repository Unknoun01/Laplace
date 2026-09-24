"""Regla 4 — un bucle que da vueltas sin avanzar.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from laplace.schema import Span

from .. import cifras
from ..pasos import identificador
from ..storage.base import LoopGroup, WindowSummary
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
        gasto = f" Las vueltas de más te han costado {_money(group.extra_cost_usd)}."
    elif sin_tarifa:
        gasto = (
            f" Las vueltas de más gastan {_miles(tokens)} tokens; cuánto dinero es, no lo "
            "sabemos, porque ese modelo no tiene tarifa conocida."
        )

    return Finding(
        id=f"bucle:{group.loop_hash}",
        kind="bucle",
        title=f"«{group.name}» da hasta {group.max_per_trace} vueltas sin avanzar",
        lead="Cada vuelta cambia un número y la respuesta no cambia: sale por el tope.",
        summary=(
            f"En una misma ejecución, tu agente llama a «{group.name}» hasta "
            f"{group.max_per_trace} veces seguidas. Las llamadas sólo se diferencian en "
            f"algún número —un contador de intentos, una página— y entre todas producen "
            f"{'un solo resultado' if group.distinct_outputs <= 1 else 'dos resultados'} "
            f"distinto{'' if group.distinct_outputs <= 1 else 's'}: el bucle no está "
            f"llegando a ninguna parte.{gasto} Se van "
            f"{_seconds(group.extra_duration_ms)} de espera."
        ),
        window_waste_usd=group.extra_cost_usd,
        window_waste_tokens=tokens,
        window_waste_ms=group.extra_duration_ms,
        cost_unavailable=(
            "ese modelo no tiene tarifa conocida, así que no podemos ponerle precio"
            if sin_tarifa
            else ""
        ),
        monthly_saving_usd=_to_monthly(group.extra_cost_usd, base) if cuesta else None,
        observed_days=days,
        costs_money=cuesta,
        **_floor_flags(group.extra_unknown_cost_spans, 0),
        difficulty="mid",
        difficulty_label="Revisar la condición de salida",
        scope_label=_scope_label(group.traces, summary.traces),
        tech=[
            TechItem(label="señal", value="bucle sin avance"),
            TechItem(label="laplace.loop_hash", value=group.loop_hash),
            TechItem(label="tipo", value=group.span_type),
            TechItem(label="vueltas de más", value=str(group.extra_spans)),
            TechItem(label="entradas distintas", value=str(group.distinct_inputs)),
            TechItem(label="salidas distintas", value=str(group.distinct_outputs)),
            TechItem(label="trazas", value=str(group.traces)),
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

    salidas = (
        "sólo sale un resultado distinto"
        if group.distinct_outputs <= 1
        else f"sólo salen {group.distinct_outputs} resultados distintos"
    )
    detalle.what_happens = (
        f"Dentro de una misma ejecución, «{group.name}» se llama hasta "
        f"{group.max_per_trace} veces. Las entradas no son idénticas —se diferencian en "
        f"algún número: un contador de intentos, una página, una hora—, pero entre las "
        f"{_miles(group.total_spans)} llamadas de la ventana {salidas}. Muchas "
        f"vueltas y casi ningún resultado nuevo es la definición medible de dar vueltas "
        f"sin avanzar."
    )
    detalle.why = (
        "El agente tiene una condición de salida que no se cumple nunca, o que depende de "
        "algo que no está mirando. Como en cada vuelta cambia un número, cree que está "
        "haciendo algo nuevo: pregunta otra vez, recibe la misma respuesta y vuelve a "
        "empezar hasta agotar el tope de intentos.\n\n"
        "Es el hermano difícil de la repetición exacta y por eso se detecta aparte: una "
        "repetición se ve comparando entradas idénticas, y aquí no hay dos entradas "
        "idénticas. Lo que delata al bucle no es la entrada, es que la salida no cambia."
    )
    detalle.detection_explanation = (
        f"Regla activa: **{MIN_VUELTAS_BUCLE} o más llamadas del mismo paso dentro de una "
        f"traza, con entradas distintas y como mucho {MAX_SALIDAS_BUCLE} salidas "
        f"distintas**. El `laplace.loop_hash` lo calcula la ingesta como el `dedup_hash` "
        f"pero **ignorando los números** de la entrada, que es lo que hace visible un "
        f"bucle con contador; el hash de salida no los ignora, porque ahí un número que "
        f"cambia sí es avance. Exigir entradas distintas es lo que impide que esta regla "
        f"y la de repetición cuenten el mismo dinero dos veces."
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title="Revisa la condición de salida",
            body=(
                "Es lo primero que hay que mirar: el bucle sale por el tope de intentos, no "
                "porque haya terminado. Casi siempre falta comprobar el caso en el que la "
                "respuesta ya es la definitiva."
            ),
            code=(
                f"for intento in range(MAX_INTENTOS):\n"
                f"    respuesta = {identificador(group.name)}(...)\n"
                f"    if respuesta == anterior:      # no avanza: no insistas\n"
                f"        break\n"
                f"    anterior = respuesta"
            ),
        ),
        FixStep(
            title="Corta cuando la respuesta se repite",
            body=(
                "Aunque la condición de salida esté bien, si dos vueltas seguidas devuelven "
                "lo mismo no hay nada que ganar con una tercera."
            ),
        ),
        FixStep(
            title="Y deja el tope puesto",
            body=(
                "El tope de intentos no sobra: es lo que ha impedido que esto fuera una "
                "cascada en vez de seis vueltas. Bájalo a lo que de verdad tenga sentido."
            ),
            advanced=True,
        ),
    ]

    vueltas = (
        f"{group.extra_spans} vueltas de más en {group.traces} "
        f"{'traza' if group.traces == 1 else 'trazas'} durante "
        f"{window_label(finding.observed_days)}"
    )
    if cuesta:
        detalle.savings_calculation = (
            f"{vueltas}, que suman {cifras.dinero_exacto(group.extra_cost_usd)} de coste "
            f"ya gastado."
            f"{_projection_sentence(finding)} Sólo cuenta a partir de la segunda vuelta de "
            f"cada traza: la primera es trabajo legítimo."
        )
        detalle.savings_note = (
            "El dinero ya gastado está medido, no estimado: es la suma del coste de las "
            "vueltas que sobran. Lo estimado es la proyección a un mes, que sale de "
            "suponer que el ritmo se mantiene."
            if finding.monthly_saving_usd is not None
            else "El dinero ya gastado está medido, no estimado: es la suma del coste de "
            "las vueltas que sobran. Lo que todavía no podemos decirte es a cuánto va el "
            "mes."
        )
    elif finding.window_waste_tokens > 0:
        detalle.savings_calculation = (
            f"{vueltas}, que mueven {_miles(finding.window_waste_tokens)} tokens y "
            f"{_seconds(group.extra_duration_ms)} de espera. Cuánto dinero es, no lo "
            f"sabemos: {group.model or 'ese modelo'} no está en la tabla de precios, y un "
            f"número inventado aquí sería peor que ninguno."
        )
        detalle.savings_note = (
            "Los tokens y el tiempo están medidos; el dinero no se puede calcular sin "
            "tarifa. Si ese modelo empieza a tener precio conocido, esta misma cifra "
            "aparecerá en dólares sin que cambies nada."
        )
    else:
        detalle.savings_calculation = (
            f"{vueltas}. Estas vueltas no consumen tokens, así que el ahorro en dinero es "
            f"cero: lo que se recupera es tiempo, {_seconds(group.extra_duration_ms)} en la "
            f"ventana analizada."
        )
        detalle.savings_note = (
            "Este problema no te cuesta dinero, te cuesta espera. Arreglarlo hace que tu "
            "agente responda antes."
        )

    detalle.evidence = evidence
    return detalle
