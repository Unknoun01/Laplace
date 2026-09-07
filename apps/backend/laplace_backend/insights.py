"""Motor de detección de derroche (Fase 2, Norte B).

Reglas **deterministas**: nada de modelos, nada de heurísticas opacas. Cada hallazgo
sale de una consulta que el usuario puede ver, con una cifra que sale de sumar coste
realmente guardado por span.

Tres principios que condicionan todo lo de aquí:

1. **Nunca inventar dinero.** Si un bucle de herramientas no consume tokens, el ahorro
   es cero y se dice; lo que se ha perdido es tiempo, y eso se enseña en su lugar.
2. **Nunca prometer calidad.** Se puede afirmar cuánto costaría un paso con otro modelo,
   porque es aritmética sobre tokens reales. No se puede afirmar que acertaría igual:
   eso exige evaluaciones (Fase 4) y hoy no las hay.
3. **Toda cifra estimada lleva su cálculo detrás**, visible en modo avanzado.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Literal

from laplace.schema import Span
from pydantic import BaseModel, Field

from .pricing import get_price_table
from .storage import clickhouse as ch
from .storage.base import ModelUsage, RepeatedGroup, Window, WindowSummary

logger = logging.getLogger("laplace.insights")

FindingKind = Literal["repeticion", "modelo_caro", "contexto_fijo"]
Difficulty = Literal["easy", "mid", "hard"]

DAYS_PER_MONTH = 30

#: Por encima de esta proporción del gasto, un ahorro deja de sonar creíble aunque los
#: números salgan. La interfaz lo presenta con cautela y enseña el desglose completo.
CAUTION_SAVINGS_RATIO = 0.60
#: Por debajo de esto no hay datos para proyectar un mes sin decirlo en la propia cifra.
MIN_DAYS_FOR_PROJECTION = 1.0

# Umbrales de las reglas. Configurables por proyecto cuando haya ajustes por proyecto;
# hoy son constantes explícitas para que se puedan leer y discutir.
MIN_REPEATS = 3
MIN_CALLS_FOR_MODEL_RULE = 5
#: Por encima de esta salida media ya no es "una tarea corta" y cambiar de modelo
#: deja de ser una sugerencia inocua.
MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK = 60
MIN_CALLS_FOR_CONTEXT_RULE = 10
MIN_FIXED_INPUT_TOKENS = 2_000


class TechItem(BaseModel):
    """Un par clave-valor de la línea técnica (sólo visible en modo avanzado)."""

    label: str
    value: str


class FixStep(BaseModel):
    title: str
    body: str
    code: str | None = None
    #: Sólo se muestra en modo avanzado: detalles de implementación.
    advanced: bool = False


class Finding(BaseModel):
    """Un hallazgo, tal y como aparece en la lista del inicio."""

    id: str
    kind: FindingKind
    #: Título en lenguaje llano, sujeto "tu agente". Sin jerga.
    title: str
    summary: str

    #: Lo desperdiciado dentro de la ventana analizada (dinero real, ya gastado).
    window_waste_usd: float = 0.0
    #: Proyección a 30 días al ritmo de la ventana. Es una estimación.
    monthly_saving_usd: float = 0.0
    currency: str = "USD"
    #: Segundos perdidos. Es lo que se enseña cuando el desperdicio no es dinero.
    window_waste_ms: float = 0.0

    difficulty: Difficulty = "easy"
    difficulty_label: str = ""
    scope_label: str = ""
    #: `False` cuando el hallazgo cuesta tiempo pero no dinero.
    costs_money: bool = True

    tech: list[TechItem] = Field(default_factory=list)
    sample_trace_id: str = ""


class FindingDetail(Finding):
    """La ficha completa de un hallazgo."""

    what_happens: str = ""
    why: str = ""

    #: Modo avanzado: cómo se ha detectado, con la consulta que se ejecutó de verdad.
    detection_explanation: str = ""
    detection_query: str = ""

    fix_steps: list[FixStep] = Field(default_factory=list)
    #: Modo avanzado: de dónde sale la cifra de ahorro.
    savings_calculation: str = ""
    #: Modo diagnóstico: el aviso de que es una estimación.
    savings_note: str = ""

    #: Ocurrencias repetidas de una traza real, como evidencia.
    evidence: list[Span] = Field(default_factory=list)


class Overview(BaseModel):
    """El héroe: cuánto cuesta y cuánto sobra."""

    project_id: str
    days: int
    currency: str = "USD"

    window_cost_usd: float = 0.0
    #: Proyección a 30 días al ritmo de la ventana.
    monthly_cost_usd: float = 0.0
    monthly_avoidable_usd: float = 0.0
    monthly_necessary_usd: float = 0.0

    traces: int = 0
    spans: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error_rate: float = 0.0
    p95_duration_ms: float = 0.0
    cost_per_trace_usd: float = 0.0

    #: Mientras esto no sea cero, el coste mostrado está incompleto y hay que decirlo.
    unknown_cost_spans: int = 0
    models_without_price: list[str] = Field(default_factory=list)
    #: Pasos cobrados a tarifa estándar sin poder confirmar qué metro aplicó. El coste
    #: real podría ser mayor, así que la cifra se presenta como suelo, no como total.
    assumed_rate_spans: int = 0
    #: Lo que la caché ya ha ahorrado en la ventana. Es dinero medido —sale de restar
    #: la tarifa de lectura a la de entrada sobre tokens reales—, no una promesa.
    window_cache_saving_usd: float = 0.0

    #: Días reales de datos en la ventana. Extrapolar a 30 días desde menos de uno es
    #: una cifra que hay que presentar como lo que es.
    observed_days: float = 0.0
    #: True cuando la proyección mensual sale de menos de 24 h de datos.
    thin_projection: bool = False
    #: True cuando el evitable pasa del umbral de cautela: la UI lo presenta con
    #: reservas en lugar de como promesa.
    savings_needs_caution: bool = False

    findings: list[Finding] = Field(default_factory=list)


# ---------------------------------------------------------------------------------
# Utilidades de redacción
# ---------------------------------------------------------------------------------


def _to_monthly(amount: float, days: float) -> float:
    if days <= 0:
        return 0.0
    return amount / days * DAYS_PER_MONTH


def _observed_days(summary: WindowSummary, window: Window) -> float:
    """Días de datos reales, no los que pide el selector.

    Un proyecto que empezó a enviar trazas hace dos horas no tiene siete días de datos
    aunque el rango diga «7 días». Proyectar un mes desde esa ventana infla la cifra
    sola, así que se proyecta sobre lo observado y se avisa cuando es poco.

    **Lo usa todo el motor, no sólo el héroe.** Si el gasto total se proyectase sobre lo
    observado y el ahorro sobre los días del selector, las dos cifras no serían
    comparables y la barra de reparto del inicio mentiría: con una hora de datos en una
    ventana de siete días, el total se multiplicaría por 720 y el ahorro por 4,3.
    """
    if not summary.first_seen or not summary.last_seen:
        return 0.0
    span = (summary.last_seen - summary.first_seen).total_seconds() / 86_400
    return max(min(span, float(window.days)), 1 / 24)


def _scope_label(affected: int, total: int) -> str:
    """«9 de cada 10 ejecuciones». Lenguaje llano, sin porcentajes."""
    if total <= 0 or affected <= 0:
        return ""
    ratio = affected / total
    if ratio >= 0.995:
        return "En todas las ejecuciones"
    de_cada = round(ratio * 10)
    if de_cada >= 1:
        return f"{de_cada} de cada 10 ejecuciones"
    return f"{affected} de {total} ejecuciones"


def _miles(n: float) -> str:
    """12345 -> «12.345». Separador de millar español.

    Existe porque el atajo anterior —formatear en inglés y luego hacer
    `.replace(",", ".")` sobre la frase entera— también convertía en puntos las comas
    del texto, y partía las oraciones por la mitad.
    """
    return f"{n:,.0f}".replace(",", ".")


def _seconds(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.1f} s"


# ---------------------------------------------------------------------------------
# Regla 1 — el mismo paso repetido dentro de una traza
# ---------------------------------------------------------------------------------


def _repetition_finding(group: RepeatedGroup, summary: WindowSummary, days: int) -> Finding:
    monthly = _to_monthly(group.extra_cost_usd, days)
    cuesta = group.extra_cost_usd > 0

    if cuesta:
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. La primera ya trae la respuesta; "
            f"las demás se pagan igual."
        )
    else:
        # Honestidad: una herramienta repetida no gasta tokens. Lo que se pierde es tiempo.
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. No gasta tokens de más, pero "
            f"cada vuelta añade espera: {_seconds(group.extra_duration_ms)} tirados en total."
        )

    return Finding(
        id=f"repeticion:{group.dedup_hash}",
        kind="repeticion",
        title=f"Tu agente repite «{group.name}» hasta {group.max_per_trace} veces seguidas",
        summary=resumen,
        window_waste_usd=group.extra_cost_usd,
        monthly_saving_usd=monthly,
        window_waste_ms=group.extra_duration_ms,
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
    )


def _repetition_detail(
    finding: Finding, group: RepeatedGroup, evidence: list[Span]
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
    detalle.detection_query = ch.REPEATED_GROUPS_SQL.strip()

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
                f"def {group.name}(...):\n"
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
            f"{group.extra_spans} pasos de más en {group.traces} trazas durante la ventana, "
            f"que suman ${group.extra_cost_usd:.6f} de coste ya gastado. La proyección mensual "
            f"extrapola ese ritmo a {DAYS_PER_MONTH} días. Sólo cuenta las ocurrencias "
            f"posteriores a la primera de cada traza; la primera es trabajo legítimo."
        )
        detalle.savings_note = (
            "Es una estimación a partir de lo que ha pasado en la ventana analizada. Si tu "
            "tráfico cambia, cambia."
        )
    else:
        detalle.savings_calculation = (
            f"{group.extra_spans} pasos de más en {group.traces} trazas. Estos pasos no "
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


# ---------------------------------------------------------------------------------
# Regla 2 — modelo caro para una tarea corta
# ---------------------------------------------------------------------------------


def _expensive_model_finding(
    usage: ModelUsage, summary: WindowSummary, days: int
) -> Finding | None:
    table = get_price_table()
    price = table.lookup(usage.model)
    if price is None or not price.alternative:
        return None
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
        id=f"modelo_caro:{usage.name}:{usage.model}",
        kind="modelo_caro",
        title=f"Usas el modelo caro para un paso muy corto: «{usage.name}»",
        summary=(
            f"Ese paso responde con {usage.avg_output_tokens:.0f} tokens de media, que es una "
            f"respuesta muy breve. Con {price.alternative} en lugar de {usage.model}, el mismo "
            f"trabajo costaría {veces_txt} menos."
        ),
        window_waste_usd=ahorro,
        monthly_saving_usd=_to_monthly(ahorro, days),
        difficulty="easy",
        difficulty_label="Cambiar el nombre del modelo",
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label="paso", value=usage.name),
            TechItem(label="modelo", value=f"{usage.model} → {price.alternative}"),
            TechItem(label="llamadas", value=str(usage.calls)),
            TechItem(label="salida media", value=f"{usage.avg_output_tokens:.0f} tok"),
        ],
        sample_trace_id=usage.sample_trace_id,
    )


def _expensive_model_detail(finding: Finding, usage: ModelUsage) -> FindingDetail:
    table = get_price_table()
    price = table.lookup(usage.model)
    cheaper = table.lookup(price.alternative) if price and price.alternative else None
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = (
        f"El paso «{usage.name}» ha hecho {usage.calls} llamadas a {usage.model} en la ventana "
        f"analizada. La respuesta media es de {usage.avg_output_tokens:.0f} tokens, que es lo "
        f"que ocupa una etiqueta o una frase corta, no un texto elaborado."
    )
    detalle.why = (
        "Los modelos grandes se pagan sobre todo por lo que escriben. Cuando un paso sólo "
        "tiene que decidir entre unas pocas opciones o extraer un dato, casi todo lo que "
        "pagas es capacidad que no se usa.\n\n"
        "Esto no es un fallo: es lo que pasa cuando un agente crece y todos los pasos heredan "
        "el modelo con el que se empezó a probar."
    )
    detalle.detection_explanation = (
        f"Regla activa: **un paso `llm` con al menos {MIN_CALLS_FOR_MODEL_RULE} llamadas y una "
        f"salida media de {MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK} tokens o menos**, cuyo modelo "
        f"tiene una alternativa más barata de la misma familia en la tabla de precios. El "
        f"ahorro se recalcula con los tokens reales, no con una estimación."
    )
    detalle.detection_query = ch.MODEL_USAGE_SQL.strip()

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
                "Un modelo más pequeño no siempre decide igual. Pasa unos cuantos casos "
                "reales por los dos y compara antes de dejarlo fijo. Cuando exista la capa de "
                "evaluación podrás hacerlo desde aquí; hoy toca a mano."
            ),
        ),
    ]

    if price and cheaper:
        detalle.savings_calculation = (
            f"{_miles(usage.input_tokens)} tokens de entrada y {_miles(usage.output_tokens)} de "
            f"salida en la ventana. Con {usage.model}: ${price.input}/1M entrada y "
            f"${price.output}/1M salida. Con {price.alternative}: ${cheaper.input}/1M y "
            f"${cheaper.output}/1M. La diferencia sobre esos mismos tokens es "
            f"${finding.window_waste_usd:.6f}, extrapolada a {DAYS_PER_MONTH} días."
        )
    detalle.savings_note = (
        "El ahorro es aritmética sobre los tokens que ya has gastado. Lo que no podemos "
        "medir todavía es si el modelo pequeño acierta igual en tu caso: eso hay que probarlo."
    )
    return detalle


# ---------------------------------------------------------------------------------
# Regla 3 — contexto fijo reenviado en cada llamada
# ---------------------------------------------------------------------------------


def _cheaper_model_if_recommended(usage: ModelUsage) -> str | None:
    """El modelo que la regla 2 propondría para este paso, si es que propone alguno.

    Sirve para que las reglas 2 y 3 no cuenten dos veces el mismo dinero. Si al paso ya
    le estamos recomendando un modelo más barato, el ahorro de activar la caché hay que
    calcularlo **sobre ese modelo**, no sobre el caro: son dos arreglos que se aplican
    uno detrás del otro, y sumar los dos ahorros a tarifa cara regala dinero que no
    existe. Con este encadenado, ahorro total = cambiar de modelo + cachear ya en el
    modelo nuevo, que es exacto y sigue siendo la lectura conservadora.
    """
    if usage.calls < MIN_CALLS_FOR_MODEL_RULE:
        return None
    if usage.avg_output_tokens > MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK:
        return None
    table = get_price_table()
    price = table.lookup(usage.model)
    if price is None or not price.alternative:
        return None
    barato = table.lookup(price.alternative)
    if barato is None or barato.cached_input is None:
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
    return price.alternative if actual.total_usd > alternativo.total_usd else None


def _cache_arithmetic(usage: ModelUsage) -> tuple[int, int, float] | None:
    """Tokens que se leerían de caché, tokens que habría que escribir, y el ahorro neto.

    El ahorro se expresa en el mismo modelo de coste con el que facturamos (D-050):
    pasar entrada estándar a entrada cacheada, **descontando** lo que cuesta escribir
    la caché. Sin ese descuento estaríamos prometiendo un ahorro que el propio motor de
    precios sabe que no es entero.

    La parte conservadora está en suponer que la caché no sobrevive de una ejecución a
    la siguiente: se paga una escritura por traza. Si aguanta más, el ahorro real será
    mayor que el que anunciamos, que es el lado por el que hay que equivocarse.
    """
    table = get_price_table()
    # Si a este paso ya le recomendamos cambiar de modelo, la caché se tarifa sobre el
    # modelo nuevo: lo contrario sería cobrar dos veces la misma mejora.
    price = table.lookup(_cheaper_model_if_recommended(usage) or usage.model)
    if price is None or price.cached_input is None:
        return None

    trazas = max(usage.traces, 1)
    lecturas = usage.min_input_tokens * max(usage.calls - trazas, 0)
    escrituras = usage.min_input_tokens * trazas
    if lecturas <= 0:
        return None

    ahorro = lecturas * (price.input - price.cached_input) / 1_000_000
    if price.cache_write is not None:
        # La escritura cuesta por encima de la entrada normal (1,25x): sólo el exceso
        # es coste nuevo, porque esos tokens se pagaban igual sin caché.
        ahorro -= escrituras * max(price.cache_write - price.input, 0.0) / 1_000_000
    return lecturas, escrituras, ahorro


def _fixed_context_finding(usage: ModelUsage, summary: WindowSummary, days: int) -> Finding | None:
    if usage.calls < MIN_CALLS_FOR_CONTEXT_RULE:
        return None
    if usage.min_input_tokens < MIN_FIXED_INPUT_TOKENS:
        return None
    if usage.cached_input_tokens > 0 or usage.cache_write_tokens > 0:
        return None  # ya está usando caché de prompt

    cuentas = _cache_arithmetic(usage)
    if cuentas is None:
        return None
    _, _, ahorro = cuentas
    if ahorro <= 0:
        return None

    return Finding(
        id=f"contexto_fijo:{usage.name}:{usage.model}",
        kind="contexto_fijo",
        title=(
            f"Reenvías las mismas {_miles(usage.min_input_tokens)} palabras en cada llamada"
        ),
        summary=(
            f"Todas las llamadas del paso «{usage.name}» empiezan con al menos "
            f"{_miles(usage.min_input_tokens)} tokens idénticos: instrucciones, ejemplos o "
            f"catálogo que no cambian. Pagas por enviarlos {usage.calls} veces."
        ),
        window_waste_usd=ahorro,
        monthly_saving_usd=_to_monthly(ahorro, days),
        difficulty="mid",
        difficulty_label="Un rato de trabajo",
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label="paso", value=usage.name),
            TechItem(label="entrada fija", value=f"{usage.min_input_tokens} tok"),
            TechItem(label="entrada media", value=f"{usage.avg_input_tokens:.0f} tok"),
            TechItem(label="llamadas", value=str(usage.calls)),
            TechItem(label="caché de prompt", value="sin usar"),
            TechItem(label="lectura de caché", value="0 tok"),
        ],
        sample_trace_id=usage.sample_trace_id,
    )


def _fixed_context_detail(finding: Finding, usage: ModelUsage) -> FindingDetail:
    table = get_price_table()
    encadenado = _cheaper_model_if_recommended(usage)
    price = table.lookup(encadenado or usage.model)
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = (
        f"En las {usage.calls} llamadas del paso «{usage.name}», la más corta ya lleva "
        f"{usage.min_input_tokens} tokens de entrada. Ese suelo es la parte que no cambia "
        f"nunca: las instrucciones y los ejemplos que van pegados a cada petición."
    )
    detalle.why = (
        "El modelo no recuerda nada entre llamadas, así que hay que reenviarle el contexto "
        "cada vez. Lo que sí se puede evitar es pagarlo a precio completo: los proveedores "
        "cobran mucho menos por la parte del prompt que ya han visto, si se la marcas.\n\n"
        "La otra vía es no enviar lo que no se usa: si el catálogo entero está en las "
        "instrucciones pero cada consulta sólo necesita un trozo, se puede buscar ese trozo "
        "y mandarlo solo."
    )
    detalle.detection_explanation = (
        f"Regla activa: **un paso `llm` con al menos {MIN_CALLS_FOR_CONTEXT_RULE} llamadas "
        f"cuyo mínimo de tokens de entrada supera {_miles(MIN_FIXED_INPUT_TOKENS)}, y que no está "
        f"usando caché de prompt** (`laplace.usage.cached_input_tokens` y "
        f"`laplace.usage.cache_write_tokens` a cero). El mínimo se "
        f"usa como suelo del prompt fijo: es una aproximación conservadora, porque la parte "
        f"común real puede ser mayor."
    )
    detalle.detection_query = ch.MODEL_USAGE_SQL.strip()

    detalle.fix_steps = [
        FixStep(
            title="Activa la caché de prompt",
            body=(
                "Marca la parte fija de tus instrucciones para que el proveedor la reutilice. "
                "Es el cambio más barato: no toca lo que el agente hace, sólo lo que cuesta."
            ),
            code=(
                "# Anthropic\n"
                'system=[{"type": "text", "text": INSTRUCCIONES,\n'
                '         "cache_control": {"type": "ephemeral"}}]'
            ),
        ),
        FixStep(
            title="Manda sólo lo que hace falta",
            body=(
                "Si esas instrucciones incluyen un catálogo o un manual, busca el fragmento "
                "que responde a cada petición y envía sólo ese. Cuesta más trabajo, pero "
                "reduce la entrada de verdad en lugar de abaratarla."
            ),
        ),
    ]
    cuentas = _cache_arithmetic(usage)
    if price is not None and cuentas is not None:
        lecturas, escrituras, _ = cuentas
        escritura_txt = (
            f" Menos {_miles(escrituras)} tokens de escritura de caché a "
            f"${price.cache_write}/1M (una por ejecución), que sobre la tarifa de entrada "
            f"cuestan ${escrituras * max(price.cache_write - price.input, 0.0) / 1_000_000:.6f}."
            if price.cache_write is not None
            else " Este proveedor no cobra aparte por escribir en caché."
        )
        detalle.savings_calculation = (
            f"{_miles(usage.min_input_tokens)} tokens fijos × "
            f"{usage.calls - max(usage.traces, 1)} llamadas que ya encontrarían la caché "
            f"caliente = {_miles(lecturas)} tokens que pasarían de ${price.input}/1M a "
            f"${price.cached_input}/1M.{escritura_txt} Neto: "
            f"${finding.window_waste_usd:.6f} en la ventana, extrapolado a "
            f"{DAYS_PER_MONTH} días."
        )
    if encadenado:
        detalle.savings_calculation += (
            f" Las tarifas son las de {encadenado}, no las de {usage.model}: a este paso "
            f"ya le recomendamos cambiar de modelo, y sumar los dos ahorros a tarifa cara "
            f"sería contar dos veces la misma mejora."
        )
    detalle.savings_note = (
        "Es una estimación conservadora: suponemos que la parte fija del prompt es al menos "
        "la llamada más corta que hemos visto, que el proveedor acepta cachearla, y que la "
        "caché no sobrevive de una ejecución a la siguiente. Si aguanta más, ahorrarás más."
    )
    return detalle


# ---------------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------------


def _without_duplicates(
    usage: ModelUsage, duplicates: dict[tuple[str, str], tuple[int, int]]
) -> ModelUsage:
    """El mismo uso, descontando los tokens que ya cuenta la regla de repetición."""
    extra_in, extra_out = duplicates.get((usage.name, usage.model), (0, 0))
    if not extra_in and not extra_out:
        return usage

    input_tokens = max(usage.input_tokens - extra_in, 0)
    output_tokens = max(usage.output_tokens - extra_out, 0)
    return replace(
        usage,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        avg_output_tokens=(output_tokens / usage.calls) if usage.calls else 0.0,
        avg_input_tokens=(input_tokens / usage.calls) if usage.calls else 0.0,
    )


def detect(store: Any, project_id: str, window: Window) -> list[Finding]:
    """Ejecuta las tres reglas y devuelve los hallazgos ordenados por dinero."""
    summary = store.summarize_window(project_id, window)
    if summary.spans == 0:
        return []

    # Los mismos días sobre los que se proyecta el gasto total: si no, el ahorro y el
    # coste del héroe estarían en escalas distintas y su cociente no querría decir nada.
    dias = _observed_days(summary, window)
    findings: list[Finding] = []

    grupos = store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS)
    for group in grupos:
        findings.append(_repetition_finding(group, summary, dias))

    # Las reglas no pueden solaparse: si una llamada al modelo se repite, la regla de
    # repetición ya cuenta el 100% de las copias sobrantes. Contarlas otra vez en la
    # regla del modelo caro inflaría el ahorro total, que es el número que vendemos.
    # Se descuentan los tokens duplicados antes de evaluar el resto de reglas.
    duplicados = {
        (g.name, g.model): (g.extra_input_tokens, g.extra_output_tokens)
        for g in grupos
        if g.span_type == "llm" and g.model
    }

    for uso in store.model_usage(project_id, window, min_calls=1):
        neto = _without_duplicates(uso, duplicados)
        if neto.calls >= MIN_CALLS_FOR_MODEL_RULE and (
            neto.avg_output_tokens <= MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK
        ):
            hallazgo = _expensive_model_finding(neto, summary, dias)
            if hallazgo is not None:
                findings.append(hallazgo)
        contexto = _fixed_context_finding(neto, summary, dias)
        if contexto is not None:
            findings.append(contexto)

    # Primero lo que más dinero devuelve; los que sólo cuestan tiempo, al final,
    # ordenados por el tiempo que recuperan.
    findings.sort(key=lambda f: (f.monthly_saving_usd, f.window_waste_ms), reverse=True)
    return findings


def overview(store: Any, project_id: str, window: Window) -> Overview:
    """El héroe del inicio: coste actual, coste evitable y métricas."""
    summary = store.summarize_window(project_id, window)
    findings = detect(store, project_id, window)

    # Se proyecta sobre los días que de verdad hay datos, no sobre los que pide el
    # selector: extrapolar 30 días desde una ventana vacía infla la cifra sola.
    observados = _observed_days(summary, window)
    mensual = _to_monthly(summary.total_cost_usd, observados)
    evitable = min(sum(f.monthly_saving_usd for f in findings), mensual)

    return Overview(
        project_id=project_id,
        days=window.days,
        unknown_cost_spans=summary.unknown_cost_spans,
        models_without_price=summary.models_without_price,
        assumed_rate_spans=summary.assumed_rate_spans,
        window_cache_saving_usd=summary.cache_saving_usd,
        observed_days=round(observados, 2),
        thin_projection=observados < MIN_DAYS_FOR_PROJECTION,
        savings_needs_caution=bool(mensual > 0 and evitable / mensual > CAUTION_SAVINGS_RATIO),
        window_cost_usd=summary.total_cost_usd,
        monthly_cost_usd=mensual,
        monthly_avoidable_usd=evitable,
        monthly_necessary_usd=max(mensual - evitable, 0.0),
        traces=summary.traces,
        spans=summary.spans,
        llm_calls=summary.llm_calls,
        tool_calls=summary.tool_calls,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        error_rate=(summary.error_traces / summary.traces) if summary.traces else 0.0,
        p95_duration_ms=summary.p95_duration_ms,
        cost_per_trace_usd=(summary.total_cost_usd / summary.traces) if summary.traces else 0.0,
        findings=findings,
    )


def detail(store: Any, project_id: str, window: Window, finding_id: str) -> FindingDetail | None:
    """Recompone la ficha de un hallazgo.

    Los identificadores son deterministas (`tipo:clave`), así que no hace falta guardar
    nada: se vuelve a calcular sobre la misma ventana y se busca el que coincide.
    """
    kind, _, key = finding_id.partition(":")
    summary = store.summarize_window(project_id, window)
    dias = _observed_days(summary, window)

    if kind == "repeticion":
        for group in store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS):
            if group.dedup_hash != key:
                continue
            finding = _repetition_finding(group, summary, dias)
            evidencia = store.sample_repetition(project_id, window, group.dedup_hash)
            return _repetition_detail(finding, group, evidencia)
        return None

    if kind in ("modelo_caro", "contexto_fijo"):
        name, _, model = key.rpartition(":")
        duplicados = {
            (g.name, g.model): (g.extra_input_tokens, g.extra_output_tokens)
            for g in store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS)
            if g.span_type == "llm" and g.model
        }
        for bruto in store.model_usage(project_id, window, min_calls=1):
            if bruto.name != name or bruto.model != model:
                continue
            uso = _without_duplicates(bruto, duplicados)
            if kind == "modelo_caro":
                finding = _expensive_model_finding(uso, summary, dias)
                return _expensive_model_detail(finding, uso) if finding else None
            finding = _fixed_context_finding(uso, summary, dias)
            return _fixed_context_detail(finding, uso) if finding else None
        return None

    return None
