"""Regla 2 — modelo caro (o lento) para una tarea corta.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from typing import Literal

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import ModelUsage, WindowSummary
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
    """
    por_modelo: dict[str, list[float]] = {}
    for uso in usos:
        # La mediana por llamada, no la media: ver `ModelUsage.p50_duration_ms`.
        if uso.model == excepto or uso.calls < MIN_CALLS_MODELO_RAPIDO or not uso.p50_duration_ms:
            continue
        por_modelo.setdefault(uso.model, []).append(uso.p50_duration_ms)
    if not por_modelo:
        return None
    modelo = min(por_modelo, key=lambda m: sum(por_modelo[m]) / len(por_modelo[m]))
    medias = por_modelo[modelo]
    return modelo, sum(medias) / len(medias)


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
        porque = (
            f" Cuánto dinero, no lo sabemos: {usage.model} no tiene tarifa conocida."
        )
        sin_dinero = f"{usage.model} no está en la tabla de precios"
    else:
        porque = (
            f" Cuánto dinero te ahorraría, no lo sabemos: {usage.model} ya es el más barato "
            f"de los que conocemos, así que no hay con qué comparar su precio. Lo que sí "
            f"está medido es el tiempo."
        )
        sin_dinero = (
            f"{usage.model} ya es el más barato de la tabla: no hay precio con el que "
            f"comparar, así que aquí sólo se puede afirmar el tiempo"
        )

    return Finding(
        id=f"modelo_caro:{usage.key}:{usage.model}",
        kind="modelo_caro",
        title=f"Respuestas cortas con el modelo más lento: «{usage.name}»",
        lead=f"{nombre_rapido}, que ya usas, respondería lo mismo en menos tiempo.",
        summary=(
            f"«{usage.name}» responde con {salida:.0f} tokens en una llamada normal y usa "
            f"{usage.model}, que en tu propio tráfico tarda "
            f"{cifras.decimal(ms_actual / ms_rapido)} veces "
            f"más por llamada que {nombre_rapido}, un modelo que ya usas. Cambiarlo te "
            f"ahorraría {_seconds(ahorro_ms)} en esta ventana.{porque}"
        ),
        window_waste_usd=0.0,
        window_waste_ms=ahorro_ms,
        window_waste_tokens=usage.output_tokens,
        cost_unavailable=sin_dinero,
        monthly_saving_usd=None,
        observed_days=days,
        costs_money=False,
        **_floor_flags(usage.unknown_cost_spans, usage.assumed_rate_spans),
        difficulty="easy",
        difficulty_label="Cambiar el nombre del modelo",
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label="paso", value=usage.name),
            TechItem(label="modelo", value=f"{usage.model} → {nombre_rapido}"),
            TechItem(label="llamadas", value=str(usage.calls)),
            TechItem(
                label="salida mediana", value=f"{_salida_tipica(usage):.0f} tok"
            ),
            TechItem(label="salida media", value=f"{usage.avg_output_tokens:.0f} tok"),
            TechItem(label="ms por llamada", value=f"{ms_actual:.0f} vs {ms_rapido:.0f}"),
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
    veces_txt = f"{veces:.0f} veces" if veces >= 2 else "algo"

    return Finding(
        id=f"modelo_caro:{usage.key}:{usage.model}",
        kind="modelo_caro",
        title=f"Usas el modelo caro para respuestas cortas: «{usage.name}»",
        lead=f"Con {price.alternative}, el mismo trabajo costaría {veces_txt} menos.",
        summary=(
            f"Ese paso responde con {_salida_tipica(usage):.0f} tokens en una llamada normal, "
            f"que es una respuesta muy breve. Con {price.alternative} en lugar de "
            f"{usage.model}, el mismo trabajo costaría {veces_txt} menos."
        ),
        window_waste_usd=ahorro,
        monthly_saving_usd=_to_monthly(ahorro, base),
        observed_days=days,
        **_floor_flags(usage.unknown_cost_spans, usage.assumed_rate_spans),
        difficulty="easy",
        difficulty_label="Cambiar el nombre del modelo",
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label="paso", value=usage.name),
            TechItem(label="modelo", value=f"{usage.model} → {price.alternative}"),
            TechItem(label="llamadas", value=str(usage.calls)),
            TechItem(
                label="salida mediana", value=f"{_salida_tipica(usage):.0f} tok"
            ),
            TechItem(label="salida media", value=f"{usage.avg_output_tokens:.0f} tok"),
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
        (t.value.split("→")[-1].strip() for t in finding.tech if t.label == "modelo"), ""
    )
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = (
        f"El paso «{usage.name}» ha hecho {_miles(usage.calls)} llamadas a {usage.model} en la "
        f"ventana analizada, y responde con {_salida_tipica(usage):.0f} tokens en una normal: "
        f"una etiqueta o una frase corta, no un texto elaborado. En tu propio tráfico hay "
        f"un modelo que tarda bastante menos por llamada."
    )
    detalle.why = (
        "Un paso que sólo tiene que decidir entre unas pocas opciones no necesita el "
        "modelo más capaz, y el más capaz suele ser también el más lento. Cuando un "
        "agente crece, todos los pasos heredan el modelo con el que se empezó a "
        "probar.\n\n"
        f"{finding.cost_unavailable.capitalize()}, así que aquí no te prometemos dinero: "
        f"te enseñamos el tiempo, que está medido llamada a llamada."
    )
    detalle.detection_explanation = (
        f"Regla activa: **un paso `llm` cuya salida mediana es de "
        f"{MAX_SALIDA_TRIVIAL_SIN_TARIFA} tokens o menos y que tarda al menos "
        f"{MIN_VECES_MAS_LENTO} veces más por llamada que otro modelo que ya usas** —con "
        f"{MIN_CALLS_MODELO_RAPIDO} llamadas como mínimo, para que la comparación no salga "
        f"de una muestra suelta. Se comparan **medianas**, no medias: una generación "
        f"desbocada mueve la media de un paso que normalmente contesta tres palabras. La "
        f"alternativa sale de tu propio tráfico, nunca de una lista nuestra de modelos."
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title=f"Prueba ese paso con {rapido}" if rapido else "Prueba con el otro modelo",
            body=(
                "Es un cambio de una palabra y afecta sólo a ese paso. Lo proponemos porque "
                "ya lo usas en otro sitio, no porque lo hayamos elegido nosotros."
            ),
            code=f'model="{rapido}"  # antes: "{usage.model}"' if rapido else None,
        ),
        FixStep(
            title="Comprueba que la calidad aguanta",
            body=(
                "Un modelo más rápido no siempre decide igual. Pasa unos cuantos casos "
                "reales por los dos y compáralos en Evaluaciones antes de dejarlo fijo."
            ),
        ),
    ]
    detalle.savings_calculation = (
        f"{_miles(usage.calls)} llamadas en {window_label(finding.observed_days)}, a "
        f"{_seconds(usage.p50_duration_ms)} de mediana cada una. Con el modelo rápido de tu "
        f"tráfico se recuperan {_seconds(finding.window_waste_ms)} en esta ventana. No hay "
        f"cifra en dólares y no la inventamos: {finding.cost_unavailable}."
    )
    detalle.savings_note = (
        "El tiempo está medido, no estimado. Lo que no se puede afirmar aquí es el dinero, "
        "y por eso no aparece ninguno."
    )
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
    cierre = (
        "Esto no es un fallo: es lo que pasa cuando un agente crece y todos los pasos "
        "heredan el modelo con el que se empezó a probar."
    )

    if parte_entrada >= 0.5:
        explicacion = (
            f"El {cifras.porcentaje(parte_entrada)} de lo que cuesta este paso es lo que "
            f"recibe, no lo que responde: {_miles(round(usage.avg_input_tokens))} tokens "
            f"de entrada por llamada para una respuesta de {_salida_tipica(usage):.0f}. Con "
            f"{alternativa or 'un modelo más barato'} cada token de entrada cuesta mucho "
            f"menos, así que el ahorro sale casi entero de ahí."
        )
    else:
        explicacion = (
            "Los modelos grandes se pagan sobre todo por lo que escriben. Cuando un paso "
            "sólo tiene que decidir entre unas pocas opciones o extraer un dato, casi todo "
            "lo que pagas es capacidad que no se usa."
        )

    if usage.avg_input_tokens >= CONTEXTO_LARGO_TOKENS:
        riesgo = (
            "Ojo con este caso: el paso contesta con mucho contexto delante, y leer "
            "documentos largos para dar una respuesta corta es donde un modelo pequeño se "
            "equivoca más que al clasificar o extraer un dato. El ahorro es real; que "
            "responda igual de bien hay que comprobarlo antes de cambiarlo."
        )
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

    detalle.what_happens = (
        f"El paso «{usage.name}» ha hecho {_miles(usage.calls)} llamadas a {usage.model} "
        f"en la ventana "
        f"analizada. Una llamada normal responde con {_salida_tipica(usage):.0f} tokens, que es "
        f"lo que ocupa una etiqueta o una frase corta, no un texto elaborado."
    )
    detalle.why = _por_que_modelo_caro(usage, price.alternative if price else "")
    detalle.detection_explanation = (
        f"Regla activa: **un paso `llm` con al menos {MIN_CALLS_FOR_MODEL_RULE} llamadas y una "
        f"salida media de {MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK} tokens o menos**, cuyo modelo "
        f"tiene una alternativa más barata de la misma familia en la tabla de precios. El "
        f"ahorro se recalcula con los tokens reales, no con una estimación."
    )
    detalle.detection_query = query.strip()

    modelo_alt = price.alternative if price else ""
    detalle.fix_steps = [
        FixStep(
            title=f"Cambia el modelo de ese paso a {modelo_alt}",
            body=(
                "Es un cambio de una palabra. Afecta sólo a ese paso: el resto del agente "
                "sigue con el modelo que ya tenía."
            ),
            code=f'model="{modelo_alt}"  # antes: "{usage.model}"',
        ),
        FixStep(
            title="Comprueba que la calidad aguanta",
            body=(
                "Un modelo más pequeño no siempre decide igual. Guarda las ejecuciones "
                "reales de este paso como conjunto de casos (más abajo) y lánzalas con el "
                "modelo nuevo: Evaluaciones te dirá si acierta igual antes de dejarlo fijo."
            ),
        ),
    ]

    if price and cheaper:
        detalle.savings_calculation = (
            f"{_miles(usage.input_tokens)} tokens de entrada y {_miles(usage.output_tokens)} de "
            f"salida en {window_label(finding.observed_days)}. Con {usage.model}: "
            f"${price.input}/1M entrada y ${price.output}/1M salida. Con "
            f"{price.alternative}: ${cheaper.input}/1M y "
            f"${cheaper.output}/1M. La diferencia sobre esos mismos tokens es "
            f"{cifras.dinero_exacto(finding.window_waste_usd)} en "
            f"{window_label(finding.observed_days)}.{_projection_sentence(finding)}"
        )
    detalle.savings_note = (
        "El ahorro es aritmética sobre los tokens que ya has gastado. Lo que no podemos "
        "medir todavía es si el modelo pequeño acierta igual en tu caso: eso hay que probarlo."
    )
    return detalle
