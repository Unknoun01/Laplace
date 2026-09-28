"""Regla 2 — modelo caro (o lento) para una tarea corta.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from typing import Literal

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import ModelUsage, WindowSummary
from ..textos import t
from .modelos import (
    MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK,
    MIN_CALLS_FOR_MODEL_RULE,
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
# Regla 2 — modelo caro para una tarea corta
# ---------------------------------------------------------------------------------


#: Sin tarifa, «tarea corta» se define mucho más estrecho que con ella: una etiqueta o
#: una clasificación, no una respuesta breve. Con el límite general de 60 tokens, el
#: paso que redacta la respuesta al cliente —45 tokens de media, trabajo legítimo con
#: el modelo grande— salía señalado en el agente sano, y una regla que acusa al que lo
#: hace bien está rota aunque acierte con el que lo hace mal (D-108).
MAX_SALIDA_TRIVIAL_SIN_TARIFA = 12

#: Cuántas veces más lento tiene que ser el modelo de un paso corto, comparado con el
#: modelo más rápido que ese mismo proyecto ya usa, para que merezca la pena decirlo.
#: Por debajo de esto la diferencia entra dentro del ruido de una máquina compartida.
MIN_VECES_MAS_LENTO = 1.8
#: Y hace falta ver de verdad el otro modelo, no una llamada suelta.
MIN_CALLS_MODELO_RAPIDO = 5


def _salida_tipica(usage: ModelUsage) -> float:
    """La salida de una llamada normal de ese paso: la mediana, con la media de reserva.

    Es **la misma cifra con la que decide la regla**, y por eso vive aquí y no escrita a
    mano en cada frase. La tarjeta explicaba el hallazgo con la media mientras la
    decisión usaba la mediana, así que un paso con una generación desbocada —once
    llamadas de 6 tokens y una de 3.000— disparaba por la mediana y después decía
    «responde con 256 tokens de media, que es una respuesta muy breve». La frase que
    justifica el hallazgo contradecía al hallazgo, y dejaba al lector sin forma de
    comprobarnos (D-118).

    La media sigue estando, en modo avanzado y al lado de la mediana: ver las dos juntas
    es justo lo que enseña que ese paso tiene una cola larga.
    """
    return usage.p50_output_tokens or usage.avg_output_tokens


def _modelo_mas_rapido(usos: list[ModelUsage], excepto: str) -> tuple[str, float] | None:
    """El modelo con menos milisegundos por llamada entre los que ya usa el proyecto.

    Se mira **dentro del propio tráfico del usuario**: proponer un modelo que no ha
    probado sería una recomendación inventada, y recomendar por tamaño de nombre es
    adivinar. Si sólo usa un modelo, no hay nada que proponer y la regla se calla.

    Un modelo aparece en varios pasos, cada uno con su mediana. Se juntan **ponderando
    por llamadas** (D-160): antes era la media simple de las medianas, y un paso con
    cinco llamadas pesaba lo mismo que uno con cinco mil, así que la recomendación
    podía salir de una muestra suelta y lenta.
    """
    por_modelo: dict[str, list[tuple[float, int]]] = {}
    for uso in usos:
        # La mediana por llamada, no la media: ver `ModelUsage.p50_duration_ms`.
        if uso.model == excepto or uso.calls < MIN_CALLS_MODELO_RAPIDO or not uso.p50_duration_ms:
            continue
        por_modelo.setdefault(uso.model, []).append((uso.p50_duration_ms, uso.calls))

    def ponderada(modelo: str) -> float:
        pares = por_modelo[modelo]
        return sum(ms * n for ms, n in pares) / sum(n for _, n in pares)

    if not por_modelo:
        return None
    # A igualdad, el nombre: que la recomendación no dependa del orden de las filas.
    modelo = min(por_modelo, key=lambda m: (ponderada(m), m))
    return modelo, ponderada(modelo)


#: Por qué esta regla no puede hablar de dinero. Son dos motivos **opuestos** y durante
#: cuatro tandas se contaron como uno: el modelo no está en la tabla (un hueco nuestro)
#: o está y ya es el más barato que conocemos (una respuesta). Decir lo primero cuando
#: pasa lo segundo es afirmar que no conocemos una tarifa que pintamos en las otras diez
#: pantallas, y quien lo lea deja de creerse la tabla entera (D-114).
MotivoSinDinero = Literal["sin_tarifa", "sin_alternativa"]


def _modelo_caro_sin_tarifa(
    usage: ModelUsage,
    otros: list[ModelUsage],
    summary: WindowSummary,
    days: float,
    *,
    motivo: MotivoSinDinero,
) -> Finding | None:
    """La misma regla cuando no hay dinero que prometer: se mide en tiempo.

    Un modelo local no tiene tarifa y nunca la tendrá, así que la versión de dinero de
    esta regla no puede disparar jamás: quien use Ollama —un estudiante, cualquiera
    probando— se quedaba sin dos de las tres reglas (D-108). Lo que sí se mide siempre
    es el tiempo y los tokens, y con eso se puede decir algo cierto: este paso responde
    cuatro palabras y lo hace con el modelo que más tarda de los que ya usas.

    Lo que **no** se dice es cuánto dinero ahorraría. `motivo` dice por qué, y los dos
    valores llevan a frases distintas porque son cosas distintas.
    """
    # La MEDIANA de salida, no la media: una generación desbocada mueve la media de un
    # paso que normalmente contesta tres palabras, y esta regla decide con ese número.
    salida = usage.p50_output_tokens or usage.avg_output_tokens
    if salida > MAX_SALIDA_TRIVIAL_SIN_TARIFA:
        return None
    rapido = _modelo_mas_rapido(otros, excepto=usage.model)
    if rapido is None or not usage.p50_duration_ms:
        return None
    nombre_rapido, ms_rapido = rapido
    ms_actual = usage.p50_duration_ms
    if ms_rapido <= 0 or ms_actual / ms_rapido < MIN_VECES_MAS_LENTO:
        return None

    ahorro_ms = (ms_actual - ms_rapido) * usage.calls

    if motivo == "sin_tarifa":
        porque = t("caro.lento.porque.sin_tarifa", modelo=usage.model)
        sin_dinero = t("hallazgo.fuera_de_tabla", modelo=usage.model)
    else:
        porque = t("caro.lento.porque.sin_alternativa", modelo=usage.model)
        sin_dinero = t("caro.lento.sin_dinero.sin_alternativa", modelo=usage.model)

    return Finding(
        id=f"modelo_caro:{usage.key}:{usage.model}",
        kind="modelo_caro",
        title=t("caro.lento.titulo", paso=usage.name),
        lead=t("caro.lento.lead", rapido=nombre_rapido),
        summary=t(
            "caro.lento.resumen",
            paso=usage.name,
            salida=cifras.miles(salida),
            modelo=usage.model,
            veces=cifras.decimal(ms_actual / ms_rapido),
            rapido=nombre_rapido,
            espera=_seconds(ahorro_ms),
            porque=porque,
        ),
        window_waste_usd=0.0,
        window_waste_ms=ahorro_ms,
        window_waste_tokens=usage.output_tokens,
        cost_unavailable=sin_dinero,
        monthly_saving_usd=None,
        observed_days=days,
        costs_money=False,
        **_floor_flags(usage.unknown_cost_spans, usage.assumed_rate_spans, [usage.model]),
        difficulty="easy",
        difficulty_label=t("dificultad.cambiar_modelo"),
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=usage.name),
            TechItem(label=t("tec.modelo"), value=f"{usage.model} → {nombre_rapido}"),
            TechItem(label=t("tec.llamadas"), value=str(usage.calls)),
            TechItem(
                label=t("tec.salida_mediana"),
                value=t("tec.tok", n=cifras.miles(_salida_tipica(usage))),
            ),
            TechItem(
                label=t("tec.salida_media"),
                value=t("tec.tok", n=cifras.miles(usage.avg_output_tokens)),
            ),
            TechItem(
                label=t("tec.ms_por_llamada"),
                value=t("tec.ms_vs", a=cifras.miles(ms_actual), b=cifras.miles(ms_rapido)),
            ),
        ],
        sample_trace_id=usage.sample_trace_id,
        step_key=usage.key,
    )


def _expensive_model_finding(
    usage: ModelUsage,
    summary: WindowSummary,
    days: float,
    base: float | None,
    otros: list[ModelUsage] | None = None,
) -> Finding | None:
    table = get_price_table()
    price = table.lookup(usage.model)
    if price is None or not price.alternative:
        # Sin dinero que prometer sigue habiendo algo que decir, pero el motivo no es el
        # mismo en los dos casos y durante cuatro tandas se dijo el primero para los dos
        # (D-114): que el modelo no esté en la tabla es un hueco nuestro; que no tenga
        # alternativa es que ya es el más barato que conocemos, y eso es una respuesta.
        motivo: MotivoSinDinero = "sin_tarifa" if price is None else "sin_alternativa"
        return _modelo_caro_sin_tarifa(usage, otros or [], summary, days, motivo=motivo)
    cheaper = table.lookup(price.alternative)
    if cheaper is None:
        return None

    actual = table.compute(
        usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    alternativo = table.compute(
        price.alternative,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    ahorro = actual.total_usd - alternativo.total_usd
    if ahorro <= 0:
        return None

    veces = actual.total_usd / alternativo.total_usd if alternativo.total_usd else 0
    veces_txt = t("caro.veces", n=cifras.miles(veces)) if veces >= 2 else t("caro.algo")

    return Finding(
        id=f"modelo_caro:{usage.key}:{usage.model}",
        kind="modelo_caro",
        title=t("caro.titulo", paso=usage.name),
        lead=t("caro.lead", barato=price.alternative, veces=veces_txt),
        summary=t(
            "caro.resumen",
            salida=cifras.miles(_salida_tipica(usage)),
            barato=price.alternative,
            modelo=usage.model,
            veces=veces_txt,
        ),
        window_waste_usd=ahorro,
        monthly_saving_usd=_to_monthly(ahorro, base),
        observed_days=days,
        **_floor_flags(
            usage.unknown_cost_spans, usage.assumed_rate_spans, [usage.model, cheaper.model]
        ),
        difficulty="easy",
        difficulty_label=t("dificultad.cambiar_modelo"),
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=usage.name),
            TechItem(label=t("tec.modelo"), value=f"{usage.model} → {price.alternative}"),
            TechItem(label=t("tec.llamadas"), value=str(usage.calls)),
            TechItem(
                label=t("tec.salida_mediana"),
                value=t("tec.tok", n=cifras.miles(_salida_tipica(usage))),
            ),
            TechItem(
                label=t("tec.salida_media"),
                value=t("tec.tok", n=cifras.miles(usage.avg_output_tokens)),
            ),
        ],
        sample_trace_id=usage.sample_trace_id,
        step_key=usage.key,
    )


def _modelo_lento_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
    """La ficha del camino por tiempo de la regla del modelo caro.

    Aquí no hay dinero que prometer: o el modelo no está en la tabla, o está y ya es el
    más barato que conocemos. Lo que sí está medido es el tiempo, y la ficha habla de
    eso y sólo de eso. El motivo concreto lo trae `finding.cost_unavailable`, que desde
    D-114 dice cuál de los dos es.
    """
    rapido = next(
        (i.value.split("→")[-1].strip() for i in finding.tech if i.label == t("tec.modelo")),
        "",
    )
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = t(
        "caro.lento.que_pasa",
        paso=usage.name,
        llamadas=_miles(usage.calls),
        modelo=usage.model,
        salida=cifras.miles(_salida_tipica(usage)),
    )
    motivo = finding.cost_unavailable
    detalle.why = t("caro.lento.por_que", motivo=motivo[:1].upper() + motivo[1:])
    detalle.detection_explanation = t(
        "caro.lento.deteccion",
        maximo=MAX_SALIDA_TRIVIAL_SIN_TARIFA,
        veces=cifras.decimal(MIN_VECES_MAS_LENTO),
        llamadas=MIN_CALLS_MODELO_RAPIDO,
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title=(
                t("caro.lento.arreglo.probar.titulo", rapido=rapido)
                if rapido
                else t("caro.lento.arreglo.probar.otro")
            ),
            body=t("caro.lento.arreglo.probar.texto"),
            code=t("caro.codigo.antes", nuevo=rapido, viejo=usage.model) if rapido else None,
        ),
        FixStep(
            title=t("caro.arreglo.calidad.titulo"),
            body=t("caro.lento.arreglo.calidad.texto"),
        ),
    ]
    detalle.savings_calculation = t(
        "caro.lento.ahorro",
        llamadas=_miles(usage.calls),
        ventana=window_label(finding.observed_days),
        mediana=_seconds(usage.p50_duration_ms),
        ahorro=_seconds(finding.window_waste_ms),
        motivo=finding.cost_unavailable,
    )
    detalle.savings_note = t("caro.lento.nota")
    return detalle


#: Entrada media por llamada a partir de la cual el paso trabaja con documentos delante.
#: Ahí un modelo pequeño se equivoca más que al clasificar o extraer, y la ficha lo dice.
CONTEXTO_LARGO_TOKENS = 4_000


def _por_que_modelo_caro(usage: ModelUsage, alternativa: str) -> str:
    """Por qué cuesta lo que cuesta ESTE paso, según dónde se va su dinero.

    La ficha decía siempre «los modelos grandes se pagan sobre todo por lo que
    escriben», también de un paso que recibe 20.000 tokens y contesta 19: casi todo su
    coste era entrada. El texto sale ahora del reparto real, y cuando el paso trabaja
    con mucho contexto se avisa del riesgo, que es justo donde más se nota.
    """
    coste = get_price_table().compute(
        usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    parte_entrada = coste.input_usd / coste.total_usd if coste.total_usd else 0.0
    cierre = t("caro.por_que.cierre")

    if parte_entrada >= 0.5:
        explicacion = t(
            "caro.por_que.entrada",
            parte=cifras.porcentaje(parte_entrada),
            entrada=_miles(round(usage.avg_input_tokens)),
            salida=cifras.miles(_salida_tipica(usage)),
            barato=alternativa or t("caro.un_modelo_barato"),
        )
    else:
        explicacion = t("caro.por_que.salida")

    if usage.avg_input_tokens >= CONTEXTO_LARGO_TOKENS:
        riesgo = t("caro.por_que.riesgo")
        return f"{explicacion}\n\n{riesgo}\n\n{cierre}"
    return f"{explicacion}\n\n{cierre}"


def _expensive_model_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
    table = get_price_table()
    price = table.lookup(usage.model)
    cheaper = table.lookup(price.alternative) if price and price.alternative else None

    # La regla tiene dos caminos y esta ficha sólo sabía contar el primero. Por el
    # segundo —cuando no hay precio con el que comparar y lo que se mide es tiempo— la
    # página decía «Cambia el modelo de ese paso a None» y afirmaba una alternativa más
    # barata que no existe. Un tipo de hallazgo con dos caminos son dos fichas (D-114).
    if cheaper is None:
        return _modelo_lento_detail(finding, usage, query)

    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = t(
        "caro.que_pasa",
        paso=usage.name,
        llamadas=_miles(usage.calls),
        modelo=usage.model,
        salida=cifras.miles(_salida_tipica(usage)),
    )
    detalle.why = _por_que_modelo_caro(usage, price.alternative if price else "")
    detalle.detection_explanation = t(
        "caro.deteccion",
        llamadas=MIN_CALLS_FOR_MODEL_RULE,
        maximo=MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK,
    )
    detalle.detection_query = query.strip()

    modelo_alt = price.alternative if price else ""
    detalle.fix_steps = [
        FixStep(
            title=t("caro.arreglo.cambiar.titulo", barato=modelo_alt),
            body=t("caro.arreglo.cambiar.texto"),
            code=t("caro.codigo.antes", nuevo=modelo_alt, viejo=usage.model),
        ),
        FixStep(
            title=t("caro.arreglo.calidad.titulo"),
            body=t("caro.arreglo.calidad.texto"),
        ),
    ]

    if price and cheaper:
        ventana = window_label(finding.observed_days)
        detalle.savings_calculation = t(
            "caro.ahorro",
            entrada=_miles(usage.input_tokens),
            salida=_miles(usage.output_tokens),
            ventana=ventana,
            modelo=usage.model,
            p_entrada=cifras.dinero(price.input),
            p_salida=cifras.dinero(price.output),
            barato=price.alternative,
            b_entrada=cifras.dinero(cheaper.input),
            b_salida=cifras.dinero(cheaper.output),
            diferencia=cifras.dinero_exacto(finding.window_waste_usd),
            proyeccion=_projection_sentence(finding),
        )
    detalle.savings_note = t("caro.nota")
    return detalle
