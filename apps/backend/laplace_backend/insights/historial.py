"""Regla 9 — un historial que crece sin límite (D-195).

Parte del motor de detección (`laplace_backend.insights`, D-130).

Un paso que manda en cada turno toda la conversación anterior paga cada turno más caro
que el anterior: la entrada crece y no baja nunca, porque nadie la recorta. Se mide por
conversación —la sesión si la hay, si no la ejecución—, sobre las llamadas de un mismo
paso en orden: al menos `MIN_TURNOS_HISTORIAL` turnos, sin ninguna bajada, y creciendo
de media al menos `MIN_CRECIMIENTO_POR_TURNO` tokens por turno.

**Lo que se afirma son tokens, nunca dinero.** Los tokens de historial reenviados —lo
que cada turno manda por encima del primero— están medidos. Cuánto se ahorraría
recortándolo depende de cuánto historial necesita el paso para responder bien, y eso no
está en las trazas: cualquier tope que pusiéramos sería inventado. La ficha dice cómo
conseguir uno que se pueda defender: probarlo con `laplace replay` y una evaluación.
Como en D-108, sin cifra que defender se habla de tokens.

No se pisa con las demás: el almacén deja fuera las llamadas que ya cuentan la
repetición, los bucles y las rehechas (cortadas o con el JSON roto), y lo que se mide
—por encima del primer turno de cada conversación— no es el suelo común de la entrada
que reclama el contexto fijo.
"""

from __future__ import annotations

from laplace.schema import Span

from ..storage.base import HistoryGroup, WindowSummary
from ..textos import t
from .modelos import (
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _miles,
    _scope_label,
    window_label,
)

KIND = "historial"

#: Turnos del mismo paso en una conversación para hablar de historial. Con tres, una
#: pregunta y dos aclaraciones ya crecen sin que haya nada que recortar.
MIN_TURNOS_HISTORIAL = 4
#: Tokens que tiene que crecer de media cada turno. Por debajo, el historial existe pero
#: no es lo que pesa en la factura.
MIN_CRECIMIENTO_POR_TURNO = 200
#: Conversaciones con el historial creciendo en la ventana. Una o dos son una
#: conversación larga, no un patrón del agente.
MIN_CONVERSACIONES_HISTORIAL = 3


def hallazgo(grupo: HistoryGroup, summary: WindowSummary, days: float) -> Finding:
    crece = _miles(round(grupo.growth_per_turn))
    return Finding(
        id=f"{KIND}:{grupo.step_key}",
        kind=KIND,
        title=t("historial.titulo", paso=grupo.name, crece=crece),
        lead=t("historial.lead"),
        summary=t(
            "historial.resumen",
            paso=grupo.name,
            conversaciones=_miles(grupo.conversations),
            crece=crece,
            primera=_miles(grupo.sample_first_input),
            ultima=_miles(grupo.sample_last_input),
            turnos=_miles(grupo.sample_turns),
            tokens=_miles(grupo.history_tokens),
        ),
        window_waste_usd=0.0,
        window_waste_tokens=grupo.history_tokens,
        cost_unavailable=t("historial.sin_tope"),
        monthly_saving_usd=None,
        observed_days=days,
        costs_money=False,
        difficulty="mid",
        difficulty_label=t("dificultad.recortar_historial"),
        scope_label=_scope_label(grupo.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=grupo.name),
            TechItem(label=t("tec.modelo"), value=grupo.model),
            TechItem(label=t("tec.conversaciones"), value=str(grupo.conversations)),
            TechItem(label=t("tec.crece_por_turno"), value=t("tec.tok", n=crece)),
            TechItem(label=t("tec.max_turnos"), value=str(grupo.max_turns)),
            TechItem(
                label=t("tec.historial_reenviado"), value=t("tec.tok", n=grupo.history_tokens)
            ),
        ],
        sample_trace_id=grupo.sample_trace_id,
        step_key=grupo.step_key,
    )


def detalle(
    finding: Finding, grupo: HistoryGroup, evidence: list[Span], consulta: str
) -> FindingDetail:
    detalle = FindingDetail(**finding.model_dump())
    detalle.what_happens = t(
        "historial.que_pasa",
        paso=grupo.name,
        conversaciones=_miles(grupo.conversations),
        llamadas=_miles(grupo.calls),
        crece=_miles(round(grupo.growth_per_turn)),
        maximo=_miles(grupo.max_turns),
        primera=_miles(grupo.sample_first_input),
        ultima=_miles(grupo.sample_last_input),
    )
    detalle.why = t("historial.por_que")
    detalle.detection_explanation = t(
        "historial.deteccion",
        turnos=MIN_TURNOS_HISTORIAL,
        crece=_miles(MIN_CRECIMIENTO_POR_TURNO),
        conversaciones=MIN_CONVERSACIONES_HISTORIAL,
    )
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("historial.arreglo.ventana.titulo"),
            body=t("historial.arreglo.ventana.texto"),
            code=t("historial.arreglo.ventana.codigo"),
        ),
        FixStep(
            title=t("historial.arreglo.resumen.titulo"),
            body=t("historial.arreglo.resumen.texto"),
        ),
        FixStep(
            title=t("historial.arreglo.cache.titulo"),
            body=t("historial.arreglo.cache.texto"),
            advanced=True,
        ),
    ]
    detalle.savings_calculation = t(
        "historial.cuenta",
        tokens=_miles(grupo.history_tokens),
        conversaciones=_miles(grupo.conversations),
        ventana=window_label(finding.observed_days),
    )
    detalle.savings_note = t("historial.nota")
    detalle.evidence = evidence
    return detalle
