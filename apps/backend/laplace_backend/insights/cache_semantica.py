"""Regla 11 — la misma pregunta con la misma respuesta en ejecuciones distintas (D-200).

Parte del motor de detección (`laplace_backend.insights`, D-130).

Un paso recibe la misma entrada —igual salvo los números (`loop_hash`)— en ejecuciones
distintas, con el mismo modelo, y responde exactamente lo mismo (`loop_out_hash`). Una
caché de respuestas habría servido esas llamadas sin pagar al modelo. Es la prueba que
se puede medir; lo que no se puede es saber si la misma respuesta vale para quien
pregunta después: puede depender del usuario, de la hora o de un número que aquí no
cuenta. Por eso no pone dinero en el total: la tarjeta dice «hasta X», como la pregunta
sin marca de la regla de lotes (D-199), y la ficha pide validarlo con `laplace replay` y
una evaluación antes de cachear.

Fuera: las repeticiones dentro de una ejecución (son de la repetición y los bucles), las
tiradas de evaluación, las llamadas con error y lo parecido pero no idéntico.

Va detrás de lotes: su «hasta» es sobre lo que queda del paso después de lo que ya
reclaman las demás, en proporción (D-117).
"""

from __future__ import annotations

from .. import cifras
from ..evals import MIN_CASES_FOR_RATE, wilson
from ..pricing import get_price_table
from ..storage.base import CacheableGroup, WindowSummary
from ..textos import t
from .lotes import _queda
from .modelos import (
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _money,
    window_label,
)

KIND = "cache_semantica"

#: Llamadas repetidas con la misma respuesta, por paso y modelo, para hablar. El mismo
#: mínimo que la salida truncada y el JSON roto: cinco veces lo mismo en la ventana es
#: un patrón del agente, no una pregunta que alguien hizo dos veces.
MIN_MISMAS_CACHE = 5
#: Repetidas para dar el porcentaje de «misma respuesta»: el mínimo de una proporción
#: en las evaluaciones. Con menos se dice la cuenta, no el porcentaje.
MIN_REPETIDAS_PORCENTAJE = MIN_CASES_FOR_RATE


def _tasa(grupo: CacheableGroup) -> str:
    """«el 92 % (entre el 75 % y el 98 %)», o vacío si no hay muestra para decirlo."""
    if grupo.repeated < MIN_REPETIDAS_PORCENTAJE:
        return ""
    bajo, alto = wilson(grupo.same_answer, grupo.repeated)
    return t(
        "cache_semantica.tasa",
        porcentaje=cifras.porcentaje(grupo.same_answer / grupo.repeated),
        bajo=cifras.porcentaje(bajo),
        alto=cifras.porcentaje(alto),
    )


def hallazgo(
    grupo: CacheableGroup,
    reclamado: float,
    coste_paso: float,
    summary: WindowSummary,
    days: float,
) -> Finding | None:
    if grupo.same_answer < MIN_MISMAS_CACHE:
        return None
    queda = _queda(reclamado, coste_paso)
    coste = grupo.same_answer_cost_usd
    if coste > 0 and queda <= 0:
        return None  # las demás reglas ya reclaman el paso entero: no queda nada
    hasta = coste * queda

    if hasta > 0:
        gasto = t("cache_semantica.gasto.hasta", ahorro=_money(hasta))
    elif get_price_table().lookup(grupo.model) is None:
        gasto = t("hallazgo.fuera_de_tabla", modelo=grupo.model)
    else:
        gasto = ""

    tasa = _tasa(grupo)
    return Finding(
        id=f"{KIND}:{grupo.step_key}:{grupo.model}",
        kind=KIND,
        title=t("cache_semantica.titulo", paso=grupo.name),
        lead=t("cache_semantica.lead"),
        summary=t(
            "cache_semantica.resumen",
            paso=grupo.name,
            repetidas=_miles(grupo.repeated),
            preguntas=_miles(grupo.questions),
            mismas=_miles(grupo.same_answer),
            tasa=f" ({tasa})" if tasa else "",
            gasto=gasto,
        ).strip(),
        # Nunca en el total: no sabemos si la misma respuesta vale para el siguiente.
        window_waste_usd=0.0,
        cost_unavailable=t("cache_semantica.sin_dinero"),
        monthly_saving_usd=None,
        observed_days=days,
        costs_money=False,
        **_floor_flags(
            grupo.same_answer_unknown_cost_spans,
            grupo.same_answer_assumed_rate_spans,
            [grupo.model],
        ),
        difficulty="mid",
        difficulty_label=t("dificultad.un_rato"),
        tech=[
            TechItem(label=t("tec.paso"), value=grupo.name),
            TechItem(label=t("tec.modelo"), value=grupo.model),
            TechItem(label=t("tec.preguntas_repetidas"), value=str(grupo.questions)),
            TechItem(label=t("tec.llamadas_repetidas"), value=str(grupo.repeated)),
            TechItem(label=t("tec.misma_respuesta"), value=str(grupo.same_answer)),
            TechItem(label=t("tec.porcentaje_misma"), value=tasa or "—"),
        ],
        sample_trace_id=grupo.sample_trace_id,
        step_key=grupo.step_key,
    )


def detalle(
    finding: Finding,
    grupo: CacheableGroup,
    reclamado: float,
    coste_paso: float,
    consulta: str,
) -> FindingDetail:
    queda = _queda(reclamado, coste_paso)
    tasa = _tasa(grupo)
    detalle = FindingDetail(**finding.model_dump())
    detalle.what_happens = t(
        "cache_semantica.que_pasa",
        paso=grupo.name,
        preguntas=_miles(grupo.questions),
        repetidas=_miles(grupo.repeated),
        mismas=_miles(grupo.same_answer),
        tasa=(
            t("cache_semantica.que_pasa.tasa", tasa=tasa)
            if tasa
            else t(
                "cache_semantica.que_pasa.sin_tasa",
                n=_miles(grupo.repeated),
                minimo=MIN_REPETIDAS_PORCENTAJE,
            )
        ),
    )
    detalle.why = t("cache_semantica.por_que")
    detalle.detection_explanation = t(
        "cache_semantica.deteccion",
        minimo=MIN_MISMAS_CACHE,
        muestra=MIN_REPETIDAS_PORCENTAJE,
    )
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("cache_semantica.arreglo.probar.titulo"),
            body=t("cache_semantica.arreglo.probar.texto"),
            code=t("cache_semantica.arreglo.probar.codigo", modelo=grupo.model),
        ),
        FixStep(
            title=t("cache_semantica.arreglo.cache.titulo"),
            body=t("cache_semantica.arreglo.cache.texto"),
            code=t("cache_semantica.arreglo.cache.codigo"),
        ),
    ]
    coste = grupo.same_answer_cost_usd
    if coste > 0:
        detalle.savings_calculation = t(
            "cache_semantica.cuenta",
            mismas=_miles(grupo.same_answer),
            coste=cifras.dinero_exacto(coste),
            ahorro=cifras.dinero_exacto(coste * queda),
            ventana=window_label(finding.observed_days),
            reclamado=(
                t("lotes.ahorro.reclamado", parte=cifras.porcentaje(queda))
                if queda < 1
                else ""
            ),
        )
    detalle.savings_note = t("cache_semantica.nota")
    return detalle
