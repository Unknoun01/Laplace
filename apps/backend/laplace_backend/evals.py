"""Evaluación (Fase 5): acierto y coste de una versión del agente, y A vs B.

La pestaña de Diagnóstico responde a «¿cuesta lo que debe?». Ésta responde a «¿responde
bien?», y sobre todo a la única pregunta que se hace **antes** de desplegar: si la
versión nueva acierta igual y cuesta menos. Eso es lo valioso de aquí, así que es lo que
manda en el diseño de este módulo.

Tres reglas que lo condicionan todo:

1. **El veredicto de persona y el de máquina no se mezclan nunca.** No se promedian, no
   se rellenan el uno con el otro y no comparten cifra. Se calculan por separado y se
   enseñan por separado; si se contradicen, eso es información, no un fallo (D-083).
2. **Un porcentaje de acierto necesita su guarda.** Con cuatro casos anotados no se
   enseña un «94 %»: se dice cuántos hay y que no bastan. Es el mismo patrón del
   468 $/mes y del 193.100 %, y ya ha mordido tres veces (D-087).
3. **El acierto es una muestra; el coste es una factura.** Por eso el acierto va con su
   margen y el coste no: el coste de una tirada es dinero que se gastó, medido span a
   span, y ponerle una barra de error sería fingir una incertidumbre que no tiene.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Literal

from laplace.schema import Annotation, AnnotationSource, EvalRun, EvalRunItem, Span
from pydantic import BaseModel, Field

from . import cifras
from .storage.base import TraceCost
from .textos import t, tn

logger = logging.getLogger("laplace.evals")

# ---------------------------------------------------------------------------------
# Umbrales. Explícitos, con nombre y con su motivo.
# ---------------------------------------------------------------------------------

#: Casos con veredicto por debajo de los cuales NO se enseña un porcentaje. Se enseñan
#: los casos en bruto («3 de 4») y se dice que no bastan. El número no es mágico: por
#: debajo de diez, el intervalo de confianza es tan ancho que el porcentaje no informa
#: de nada, y un porcentaje que no informa igual se recuerda igual.
MIN_CASES_FOR_RATE = 10

#: z de un intervalo de confianza del 95 %. Se usa el de Wilson y no el normal simple
#: porque con pocos casos y aciertos cerca del 100 % el normal se sale del rango
#: [0, 1] y produce «acierta entre el 82 % y el 104 %».
Z_95 = 1.96

#: Diferencia relativa de coste por debajo de la cual se dice que cuestan lo mismo.
#: Un 3 % de diferencia entre dos tiradas es ruido de tokenización, no un ahorro.
MATERIAL_COST_CHANGE = 0.05

Verdict = Literal["sin-base", "mejor", "peor", "empate"]


# ---------------------------------------------------------------------------------
# Modelos de salida
# ---------------------------------------------------------------------------------


class Rate(BaseModel):
    """Una proporción de acierto, con su guarda puesta.

    `value` es `None` mientras no haya casos suficientes, y entonces `unavailable` dice
    por qué y `passed`/`judged` siguen ahí para que la pantalla enseñe los números en
    bruto. Un cero significaría «no acierta ninguno», que es una afirmación distinta.
    """

    #: De qué veredicto habla esta cifra. Nunca es una mezcla.
    source: AnnotationSource
    judged: int = 0
    passed: int = 0
    failed: int = 0
    #: Casos de la tirada sin veredicto de esta fuente. No cuentan como fallo: contarlos
    #: como fallo convertiría «no lo he mirado» en «está mal».
    unjudged: int = 0

    value: float | None = None
    #: Intervalo de Wilson al 95 %. `None` junto con `value`.
    low: float | None = None
    high: float | None = None
    unavailable: str = ""


class VariantSide(BaseModel):
    """Un lado de la comparación: una tirada, su acierto y su coste."""

    run_id: str
    variant: str
    cases: int = 0
    #: Casos en los que la ejecución del agente reventó. Cuentan como fallo.
    crashed: int = 0

    #: Una entrada por fuente de veredicto. Nunca se funden en una sola cifra.
    rates: list[Rate] = Field(default_factory=list)

    cost_usd: float = 0.0
    cost_per_case_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms_per_case: float | None = None
    #: El coste de esta tirada es un suelo: hay pasos sin tarifa o a tarifa asumida.
    cost_is_floor: bool = False
    #: Lo que costó juzgar esta tirada. Es coste real y se dice aparte del del agente:
    #: mezclarlos haría que una versión pareciese más cara por haberla mirado más.
    judge_cost_usd: float = 0.0
    judge_cost_unknown: bool = False

    #: Versiones de prompt gestionado con las que corrió esta tirada, como
    #: `resumen v8`. Sale de las trazas, no de lo que alguien escribiera en `variant`:
    #: la etiqueta de una tirada la pone una persona con prisa y se equivoca; la versión
    #: la escribió el SDK al hacer la llamada. Vacío si no se gestionan prompts (D-094).
    prompt_versions: list[str] = Field(default_factory=list)


class SourceComparison(BaseModel):
    """La comparación según **una** fuente de veredicto."""

    source: AnnotationSource
    verdict: Verdict
    headline: str
    detail: str
    a: Rate
    b: Rate


class Comparison(BaseModel):
    """A vs B: acierto y coste a la vez, que es lo que se mira antes de desplegar."""

    project_id: str
    dataset_id: str
    dataset_name: str = ""
    cases: int = 0

    a: VariantSide
    b: VariantSide

    #: Una comparación por fuente. Se pintan las dos, nunca se funden.
    by_source: list[SourceComparison] = Field(default_factory=list)
    #: True cuando las personas y el juez no dicen lo mismo. No es un error: es el dato
    #: más interesante que puede dar esta pantalla.
    sources_disagree: bool = False

    #: Variación relativa del coste de B respecto de A. `None` si A costó cero.
    cost_change: float | None = None
    #: La frase que junta las dos cosas. Es el titular de la pestaña.
    headline: str = ""
    detail: str = ""


class RunSummary(BaseModel):
    """Una tirada por sí sola, para la lista."""

    run_id: str
    variant: str
    dataset_id: str
    dataset_name: str = ""
    created_at: Any = None
    cases: int = 0
    cost_usd: float = 0.0
    judge_cost_usd: float = 0.0
    #: Llamadas de esta tirada sin tarifa conocida. Con esto la pantalla sabe si el
    #: coste de arriba es un total, un suelo o un cero que no significa nada: comparar
    #: dos versiones por dinero con la mitad de las llamadas sin precio es comparar
    #: cualquier cosa (D-107).
    unknown_cost_spans: int = 0
    rates: list[Rate] = Field(default_factory=list)
    #: Con qué versiones de prompt corrió. Sale de las trazas (D-094).
    prompt_versions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------------
# Estadística: la guarda
# ---------------------------------------------------------------------------------


def wilson(passed: int, judged: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalo de Wilson al 95 % para una proporción.

    El intervalo normal de toda la vida —p ± z·√(p(1-p)/n)— se sale del rango con pocos
    casos: diez de diez aciertos dan «entre el 100 % y el 100 %», y nueve de diez, «entre
    el 71 % y el 109 %». Wilson no se sale nunca y no colapsa cuando p vale 0 o 1, que
    son exactamente los dos casos que más aparecen en un conjunto pequeño.
    """
    if judged <= 0:
        return 0.0, 1.0
    p = passed / judged
    denom = 1 + z * z / judged
    centro = (p + z * z / (2 * judged)) / denom
    margen = z * math.sqrt(p * (1 - p) / judged + z * z / (4 * judged * judged)) / denom
    return max(centro - margen, 0.0), min(centro + margen, 1.0)


def rate_for(source: AnnotationSource, passed: int, failed: int, unjudged: int) -> Rate:
    """Una proporción con su guarda ya aplicada."""
    judged = passed + failed
    rate = Rate(
        source=source, judged=judged, passed=passed, failed=failed, unjudged=unjudged
    )
    if judged == 0:
        rate.unavailable = (
            t("evals.tasa.sin_veredicto") if unjudged else t("evals.tasa.sin_casos")
        )
        return rate
    if judged < MIN_CASES_FOR_RATE:
        rate.unavailable = tn("evals.tasa.pocos", judged, minimo=MIN_CASES_FOR_RATE)
        return rate
    rate.value = passed / judged
    rate.low, rate.high = wilson(passed, judged)
    return rate


# ---------------------------------------------------------------------------------
# Acierto de una tirada
# ---------------------------------------------------------------------------------


def _verdicts(
    run: EvalRun, annotations: dict[str, list[Annotation]], source: AnnotationSource
) -> tuple[int, int, int]:
    """Aciertos, fallos y sin juzgar de una tirada según una fuente.

    Un caso que reventó cuenta como fallo aunque nadie lo haya anotado: si se contase
    como «sin juzgar», una versión que revienta la mitad de las veces saldría con el
    mismo acierto que una que funciona, y ésa es la peor mentira posible aquí.

    Un caso sin anotar **no** cuenta como fallo. Contarlo convertiría «no lo he mirado»
    en «está mal», y el acierto bajaría al añadir casos al conjunto.
    """
    passed = failed = unjudged = 0
    for item in run.items:
        if item.failed:
            failed += 1
            continue
        veredicto = next(
            (
                a.verdict
                for a in annotations.get(item.trace_id, [])
                if a.source == source and a.verdict in ("pass", "fail")
            ),
            None,
        )
        if veredicto == "pass":
            passed += 1
        elif veredicto == "fail":
            failed += 1
        else:
            unjudged += 1
    return passed, failed, unjudged


def prompt_labels(
    run: EvalRun, prompts: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """Las versiones de prompt que usaron las trazas de una tirada, sin repetir.

    Es la respuesta a «¿con qué prompt corrió cada lado?», que es lo primero que se
    pregunta cuando una comparación sale rara. Se lee de las trazas y no de la etiqueta
    de la tirada a propósito: `variant="prompt-v3"` es lo que alguien tecleó, y la mitad
    de las veces se queda sin actualizar.
    """
    vistas = {
        (nombre, version)
        for item in run.items
        for nombre, version in prompts.get(item.trace_id, [])
    }
    return sorted(
        f"{nombre} v{version}" if version else t("evals.reserva", nombre=nombre)
        for nombre, version in vistas
    )


def cost_key(item: EvalRunItem) -> str:
    """Con qué clave se busca el coste de un caso: la traza, o la traza y sus spans.

    Una misma traza puede contar entera en una tirada y sólo por unos spans en otra
    (D-167): con la misma clave, una pisaría a la otra.
    """
    if not item.span_ids:
        return item.trace_id
    return f"{item.trace_id}#{','.join(sorted(item.span_ids))}"


def coste_de_spans(trace_id: str, spans: list[Span], ids: set[str]) -> TraceCost:
    """El coste de unos spans concretos de una traza, con las marcas de siempre."""
    elegidos = [s for s in spans if s.span_id in ids]
    llms = [s.llm for s in elegidos if s.llm is not None]
    return TraceCost(
        trace_id=trace_id,
        cost_usd=sum(llm.cost.total_usd for llm in llms),
        input_tokens=sum(llm.usage.input_tokens for llm in llms),
        output_tokens=sum(llm.usage.output_tokens for llm in llms),
        duration_ms=sum(s.duration_ms for s in elegidos),
        spans=len(elegidos),
        error=any(s.status == "error" for s in elegidos),
        # Un span pedido que ya no está (caducado, borrado) no puede costar cero sin
        # decirlo: cuenta como coste desconocido, que convierte el total en un suelo.
        unknown_cost_spans=sum(1 for llm in llms if llm.cost.unknown) + len(ids) - len(elegidos),
        assumed_rate_spans=sum(1 for llm in llms if llm.cost.rate_assumed),
    )


def side_for(
    run: EvalRun,
    annotations: dict[str, list[Annotation]],
    costs: dict[str, TraceCost],
    prompts: dict[str, list[tuple[str, int]]] | None = None,
) -> VariantSide:
    """Un lado de la comparación, con su acierto por fuente y su coste medido."""
    casos = len(run.items)
    reventados = sum(1 for i in run.items if i.failed)

    trazas = [costs[cost_key(i)] for i in run.items if cost_key(i) in costs]
    coste = sum(t.cost_usd for t in trazas)
    suelo = any(t.unknown_cost_spans or t.assumed_rate_spans for t in trazas)

    # El coste del juez se suma aparte del del agente. Si se mezclaran, la versión que
    # alguien decidió mirar con más cuidado parecería más cara de ejecutar.
    anotaciones = [a for i in run.items for a in annotations.get(i.trace_id, [])]
    jueces = [a.judge for a in anotaciones if a.source == "llm_judge" and a.judge]

    lado = VariantSide(
        run_id=run.id,
        variant=run.variant,
        cases=casos,
        crashed=reventados,
        cost_usd=coste,
        cost_per_case_usd=(coste / casos) if casos else None,
        input_tokens=sum(t.input_tokens for t in trazas),
        output_tokens=sum(t.output_tokens for t in trazas),
        duration_ms_per_case=(sum(t.duration_ms for t in trazas) / casos) if casos else None,
        cost_is_floor=suelo,
        judge_cost_usd=sum(j.cost_usd for j in jueces),
        judge_cost_unknown=any(j.cost_unknown for j in jueces),
        prompt_versions=prompt_labels(run, prompts or {}),
    )
    for fuente in ("human", "llm_judge"):
        passed, failed, unjudged = _verdicts(run, annotations, fuente)
        lado.rates.append(rate_for(fuente, passed, failed, unjudged))
    return lado


# ---------------------------------------------------------------------------------
# A vs B
# ---------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return cifras.porcentaje(x)


def _money(x: float) -> str:
    """El mismo importe que el resto del producto: coma española (D-120)."""
    return cifras.dinero(x)


def compare_rates(source: AnnotationSource, a: Rate, b: Rate) -> SourceComparison:
    """Compara dos aciertos **sin declarar un ganador que no se sostenga**.

    Si los intervalos se solapan, la respuesta es «no se distinguen con estos casos», no
    «B es mejor por cuatro puntos». Declarar un ganador sobre una diferencia que cabe
    dentro del margen es exactamente cómo se despliega una regresión creyendo que es una
    mejora, y es la cuarta cara del mismo error que ya nos ha mordido tres veces.
    """
    fuente = t("evals.fuente.personas" if source == "human" else "evals.fuente.juez")

    if a.value is None or b.value is None:
        falta = a.unavailable or b.unavailable
        return SourceComparison(
            source=source,
            verdict="sin-base",
            headline=t("evals.cmp.sin_base.titulo", fuente=fuente),
            detail=t(
                "evals.cmp.sin_base.detalle",
                falta=falta[:1].upper() + falta[1:],
                a_ok=a.passed,
                a_n=a.judged,
                b_ok=b.passed,
                b_n=b.judged,
            ),
            a=a,
            b=b,
        )

    cifras_txt = t(
        "evals.cmp.cifras",
        a_pct=_pct(a.value),
        a_ok=a.passed,
        a_n=a.judged,
        b_pct=_pct(b.value),
        b_ok=b.passed,
        b_n=b.judged,
    )
    margenes = t(
        "evals.cmp.margenes",
        a_bajo=_pct(a.low),
        a_alto=_pct(a.high),
        b_bajo=_pct(b.low),
        b_alto=_pct(b.high),
    )

    # Solapan los intervalos: la diferencia cabe dentro del ruido de la muestra.
    if a.low <= b.high and b.low <= a.high:
        return SourceComparison(
            source=source,
            verdict="empate",
            headline=t("evals.cmp.empate.titulo", fuente=fuente),
            detail=t("evals.cmp.empate.detalle", cifras=cifras_txt, margenes=margenes),
            a=a,
            b=b,
        )

    mejor = b.value > a.value
    return SourceComparison(
        source=source,
        verdict="mejor" if mejor else "peor",
        headline=t(
            "evals.cmp.mejor.titulo" if mejor else "evals.cmp.peor.titulo", fuente=fuente
        ),
        detail=t("evals.cmp.separados", cifras=cifras_txt, margenes=margenes),
        a=a,
        b=b,
    )


def _rate(lado: VariantSide, source: AnnotationSource) -> Rate:
    return next(r for r in lado.rates if r.source == source)


def _headline(
    a: VariantSide, b: VariantSide, por_fuente: list[SourceComparison]
) -> tuple[str, str]:
    """El titular que junta acierto y coste, que es para lo que existe la pestaña.

    Se construye a partir de los veredictos que **sí** tienen base. Si ninguno la tiene,
    se dice eso y se habla sólo del coste, que sí está medido: con cuatro casos no se
    sabe si B acierta igual, pero se sabe perfectamente lo que costó.
    """
    coste_a = a.cost_per_case_usd or 0.0
    coste_b = b.cost_per_case_usd or 0.0
    cambio = ((coste_b - coste_a) / coste_a) if coste_a > 0 else None
    suelo = a.cost_is_floor or b.cost_is_floor

    def importe(valor: float) -> str:
        return t("evals.al_menos", x=_money(valor)) if suelo else _money(valor)

    if cambio is None:
        frase_coste = t("evals.coste.b_cuesta", coste=importe(coste_b))
    elif cambio < -MATERIAL_COST_CHANGE:
        frase_coste = t("evals.coste.menos", pct=_pct(-cambio))
    elif cambio > MATERIAL_COST_CHANGE:
        frase_coste = t("evals.coste.mas", pct=_pct(cambio))
    else:
        frase_coste = t("evals.coste.igual")

    detalle_coste = t(
        "evals.detalle_coste",
        a=importe(coste_a),
        a_total=_money(a.cost_usd),
        b=importe(coste_b),
        b_total=_money(b.cost_usd),
    )

    con_base = [c for c in por_fuente if c.verdict != "sin-base"]
    if not con_base:
        return (
            t("evals.titular.sin_base.con_cambio", coste=frase_coste)
            if cambio is not None
            else t("evals.titular.sin_base", coste=frase_coste),
            t("evals.detalle.sin_base", detalle=detalle_coste),
        )

    # Si alguna fuente dice que empeora, manda esa: es el lado por el que hay que
    # equivocarse cuando se está decidiendo si desplegar.
    peor = next((c for c in con_base if c.verdict == "peor"), None)
    if peor is not None:
        return (
            t("evals.titular.peor", coste=frase_coste),
            f"{peor.detail} {detalle_coste}",
        )

    mejor = next((c for c in con_base if c.verdict == "mejor"), None)
    if mejor is not None:
        return (t("evals.titular.mejor", coste=frase_coste), f"{mejor.detail} {detalle_coste}")

    # Empate: ninguna fuente distingue las dos versiones, así que lo que decide es el
    # coste. Es el caso más común de una comparación honesta y el más accionable.
    empate = con_base[0]
    return (
        t("evals.titular.igual", coste=frase_coste),
        f"{empate.detail} {detalle_coste}",
    )


def compare(
    run_a: EvalRun,
    run_b: EvalRun,
    annotations: dict[str, list[Annotation]],
    costs: dict[str, TraceCost],
    *,
    project_id: str,
    dataset_name: str = "",
    prompts: dict[str, list[tuple[str, int]]] | None = None,
) -> Comparison:
    """La comparación entera. Sin efectos: recibe todo ya leído."""
    a = side_for(run_a, annotations, costs, prompts)
    b = side_for(run_b, annotations, costs, prompts)

    por_fuente = [
        compare_rates(fuente, _rate(a, fuente), _rate(b, fuente))
        for fuente in ("human", "llm_judge")
    ]

    # Se contradicen cuando las dos tienen base y apuntan a lados distintos. El empate
    # no contradice a nadie: es «no lo sé», no «lo contrario».
    direcciones = {c.verdict for c in por_fuente if c.verdict in ("mejor", "peor")}
    contradicen = len(direcciones) > 1

    coste_a = a.cost_per_case_usd or 0.0
    coste_b = b.cost_per_case_usd or 0.0
    titular, detalle = _headline(a, b, por_fuente)

    return Comparison(
        project_id=project_id,
        dataset_id=run_a.dataset_id,
        dataset_name=dataset_name,
        cases=max(len(run_a.items), len(run_b.items)),
        a=a,
        b=b,
        by_source=por_fuente,
        sources_disagree=contradicen,
        cost_change=((coste_b - coste_a) / coste_a) if coste_a > 0 else None,
        headline=titular,
        detail=detalle,
    )


def summarize_run(
    run: EvalRun,
    annotations: dict[str, list[Annotation]],
    costs: dict[str, TraceCost],
    dataset_name: str = "",
    prompts: dict[str, list[tuple[str, int]]] | None = None,
) -> RunSummary:
    lado = side_for(run, annotations, costs, prompts)
    return RunSummary(
        run_id=run.id,
        variant=run.variant,
        dataset_id=run.dataset_id,
        dataset_name=dataset_name,
        created_at=run.created_at,
        cases=lado.cases,
        cost_usd=lado.cost_usd,
        judge_cost_usd=lado.judge_cost_usd,
        unknown_cost_spans=sum(
            costs[cost_key(i)].unknown_cost_spans for i in run.items if cost_key(i) in costs
        ),
        rates=lado.rates,
        prompt_versions=lado.prompt_versions,
    )
