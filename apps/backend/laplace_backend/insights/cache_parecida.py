"""Regla 12 — preguntas casi iguales con la misma respuesta (D-219).

Parte del motor de detección (`laplace_backend.insights`, D-130).

Lo parecido pero no idéntico que D-200 dejó fuera: preguntas del mismo paso y modelo a una
palabra de diferencia (simhash a 4 bits o menos), en ejecuciones distintas, que recibieron
exactamente la misma salida que la primera del grupo. Más débil que D-200: una palabra
puede ser una negación, y la huella no distingue un sinónimo de ella. Por eso no pone
dinero en el total y su ficha pide mirar las parejas a mano antes de cachear.

Cuenta la primera llamada de cada pregunta, que es justo la que la caché exacta no
reclama: las dos reglas no cuentan nunca la misma llamada.
"""

from __future__ import annotations

from .. import cifras
from ..pricing import get_price_table
from ..storage._parecido import MAX_PREGUNTAS_PASO, UMBRAL_BITS
from ..storage.base import CacheableGroup, WindowSummary
from ..textos import t
from .cache_semantica import MIN_MISMAS_CACHE, MIN_REPETIDAS_PORCENTAJE, _tasa
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

KIND = "cache_parecida"


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
        gasto = t("cache_parecida.gasto.hasta", ahorro=_money(hasta))
    elif get_price_table().lookup(grupo.model) is None:
        gasto = t("hallazgo.fuera_de_tabla", modelo=grupo.model)
    else:
        gasto = ""

    tasa = _tasa(grupo)
    return Finding(
        id=f"{KIND}:{grupo.step_key}:{grupo.model}",
        kind=KIND,
        title=t("cache_parecida.titulo", paso=grupo.name),
        lead=t("cache_parecida.lead"),
        summary=t(
            "cache_parecida.resumen",
            paso=grupo.name,
            parecidas=_miles(grupo.repeated),
            grupos=_miles(grupo.questions),
            mismas=_miles(grupo.same_answer),
            tasa=f" ({tasa})" if tasa else "",
            gasto=gasto,
        ).strip(),
        # Nunca en el total: no sabemos si la misma respuesta vale para el siguiente.
        window_waste_usd=0.0,
        cost_unavailable=t("cache_parecida.sin_dinero"),
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
            TechItem(label=t("tec.grupos_parecidos"), value=str(grupo.questions)),
            TechItem(label=t("tec.llamadas_parecidas"), value=str(grupo.repeated)),
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
        "cache_parecida.que_pasa",
        paso=grupo.name,
        grupos=_miles(grupo.questions),
        parecidas=_miles(grupo.repeated),
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
    detalle.why = t("cache_parecida.por_que")
    detalle.detection_explanation = t(
        "cache_parecida.deteccion",
        bits=UMBRAL_BITS,
        minimo=MIN_MISMAS_CACHE,
        tope=_miles(MAX_PREGUNTAS_PASO),
        muestra=MIN_REPETIDAS_PORCENTAJE,
    )
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("cache_parecida.arreglo.probar.titulo"),
            body=t("cache_parecida.arreglo.probar.texto"),
            code=t("cache_semantica.arreglo.probar.codigo", modelo=grupo.model),
        ),
        FixStep(
            title=t("cache_parecida.arreglo.cache.titulo"),
            body=t("cache_parecida.arreglo.cache.texto"),
            code=t("cache_semantica.arreglo.cache.codigo"),
        ),
    ]
    coste = grupo.same_answer_cost_usd
    if coste > 0:
        detalle.savings_calculation = t(
            "cache_parecida.cuenta",
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
    detalle.savings_note = t("cache_parecida.nota")
    return detalle
