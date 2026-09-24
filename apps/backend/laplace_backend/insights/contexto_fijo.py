"""Regla 3 — contexto fijo reenviado en cada llamada.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from typing import Any

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import ModelUsage, WindowSummary
from .modelos import (
    _MILLION,
    MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK,
    MIN_CALLS_FOR_CONTEXT_RULE,
    MIN_CALLS_FOR_MODEL_RULE,
    MIN_FIXED_INPUT_TOKENS,
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _money,
    _projection_sentence,
    _scope_label,
    _to_monthly,
    window_label,
)

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
    if (usage.p50_output_tokens or usage.avg_output_tokens) > MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK:
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


def _coste_de_escribir(price: Any, tokens: int) -> float:
    """El sobreprecio de escribir esos tokens en caché, sobre la tarifa de entrada."""
    return tokens * max(price.cache_write - price.input, 0.0) / _MILLION


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


#: Por debajo de esta parte del contexto fijo sin cachear, la caché ya se está llevando
#: casi todo y no hay nada que proponer por ese lado.
MIN_PARTE_SIN_CACHEAR = 0.35
#: …pero leer de caché **también se cobra** —entre un 10 % y un 50 % de la entrada según
#: el proveedor—, así que un prefijo fijo bien cacheado puede seguir siendo una parte
#: gorda de la factura. Por encima de esta parte del gasto del proyecto, se dice, aunque
#: la caché esté funcionando: lo que se propone entonces no es cachear, es acortar.
MIN_PARTE_DEL_GASTO_EN_LECTURAS = 0.05


def _coste_de_leer_cache(usage: ModelUsage) -> float:
    """Lo que cuesta, en dinero medido, servir de caché el prefijo fijo del paso.

    Leer de caché es más barato que la entrada normal, pero no es gratis: OpenAI cobra
    la lectura al 10-50 % de la entrada según el modelo, y Anthropic al 10 %. Un prefijo
    de tres mil tokens en diez mil llamadas sigue siendo una factura, y la regla del
    contexto fijo no lo miraba: sólo hablaba de lo que **no** estaba cacheado (D-111).
    """
    price = get_price_table().lookup(usage.model)
    if price is None or price.cached_input is None or not usage.cached_input_tokens:
        return 0.0
    return usage.cached_input_tokens * price.cached_input / _MILLION


def _fixed_context_finding(
    usage: ModelUsage, summary: WindowSummary, days: float, base: float | None
) -> Finding | None:
    """Cuánto contexto fijo se reenvía **sin cachear**, que es la pregunta útil.

    Antes la regla se apagaba en cuanto el paso traía un solo token de caché, con el
    razonamiento de «ya está usando caché de prompt». Eso la dejaba muerta justo contra
    el proveedor más usado: OpenAI cachea sola por encima de 1.024 tokens, así que
    cualquier paso con contexto largo trae caché y ninguno habría disparado nunca. Y
    contra un servidor local, igual: Ollama reutiliza el prefijo y lo reporta (D-105).

    La pregunta no es «¿hay caché?» sino «¿cuánto de lo que reenvías **no** se está
    sirviendo de caché?». Eso se mide: el prefijo fijo por llamada —el mínimo de entrada
    de las llamadas que respondieron— por el número de llamadas, menos lo que el
    proveedor dice haber servido de caché (D-108).
    """
    if usage.calls < MIN_CALLS_FOR_CONTEXT_RULE:
        return None
    if usage.min_input_tokens < MIN_FIXED_INPUT_TOKENS:
        return None

    reenviado = usage.min_input_tokens * usage.calls
    if not reenviado:
        return None
    sin_cachear = max(reenviado - usage.cached_input_tokens, 0)
    parte = sin_cachear / reenviado

    cuentas = _cache_arithmetic(usage)
    ahorro = 0.0
    if cuentas is not None:
        _, _, ahorro = cuentas
        ahorro = max(ahorro, 0.0)
    # El dinero de cachear es el de la parte que NO se está cacheando.
    ahorro *= parte

    # Y lo que cuesta lo que sí se cachea, que no es gratis: leer de caché se cobra.
    # Es la mitad de la pregunta que faltaba, y la que manda contra OpenAI y Anthropic,
    # donde la caché es automática o barata pero nunca libre (D-111).
    coste_lecturas = _coste_de_leer_cache(usage)
    pesan = (
        summary.total_cost_usd > 0
        and coste_lecturas / summary.total_cost_usd >= MIN_PARTE_DEL_GASTO_EN_LECTURAS
    )
    if parte < MIN_PARTE_SIN_CACHEAR and not pesan:
        return None  # la caché se lo lleva casi todo y lo que cuesta leerla es menor

    de_cache = (
        f"De ellos, {_miles(usage.cached_input_tokens)} sí se sirven de caché; "
        f"{_miles(sin_cachear)} no."
        if usage.cached_input_tokens
        else "Ninguno se está sirviendo de caché."
    )
    if parte < MIN_PARTE_SIN_CACHEAR:
        # No se propone cachear, así que tampoco se apunta el ahorro de cachear: sería
        # prometer dinero por hacer lo que ya se está haciendo.
        ahorro = 0.0
    if ahorro > 0:
        precio = f" Cachearlos ahorraría {_money(ahorro)} en esta ventana."
    elif coste_lecturas > 0:
        # Aquí no se propone cachear —ya lo está— sino mandar menos.
        parte_txt = (
            f", el {coste_lecturas / summary.total_cost_usd:.0%} de lo que gastas"
            if summary.total_cost_usd > 0
            else ""
        )
        precio = (
            f" La caché ya está haciendo su trabajo, pero **leerla también se cobra**: "
            f"esas lecturas son {_money(coste_lecturas)}{parte_txt}. Eso no baja "
            f"cacheando mejor; baja mandando menos."
        )
    else:
        precio = " Cuánto dinero es, no lo sabemos: ese modelo no tiene tarifa conocida."

    # El dinero del hallazgo es lo que de verdad se puede recuperar: lo que ahorraría
    # cachear lo que no se cachea, más lo que cuestan las lecturas del prefijo fijo.
    ahorro += coste_lecturas

    return Finding(
        id=f"contexto_fijo:{usage.key}:{usage.model}",
        kind="contexto_fijo",
        title=(
            f"Reenvías los mismos {_miles(usage.min_input_tokens)} tokens en cada llamada"
        ),
        summary=(
            f"Todas las llamadas del paso «{usage.name}» empiezan con al menos "
            f"{_miles(usage.min_input_tokens)} tokens idénticos: instrucciones, ejemplos o "
            f"catálogo que no cambian. Los envías {_miles(usage.calls)} veces. "
            f"{de_cache}{precio}"
        ),
        lead="Instrucciones o catálogo que no cambian, y se pagan enteros en cada llamada.",
        window_waste_usd=ahorro,
        window_waste_tokens=sin_cachear,
        cost_unavailable=(
            "" if ahorro > 0 else f"{usage.model} no está en la tabla de precios"
        ),
        costs_money=ahorro > 0,
        monthly_saving_usd=_to_monthly(ahorro, base) if ahorro > 0 else None,
        observed_days=days,
        **_floor_flags(usage.unknown_cost_spans, usage.assumed_rate_spans),
        difficulty="mid",
        difficulty_label="Un rato de trabajo",
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label="paso", value=usage.name),
            TechItem(label="entrada fija", value=f"{usage.min_input_tokens} tok"),
            TechItem(label="entrada media", value=f"{usage.avg_input_tokens:.0f} tok"),
            TechItem(label="llamadas", value=str(usage.calls)),
            TechItem(label="reenviado", value=f"{reenviado} tok"),
            TechItem(label="servido de caché", value=f"{usage.cached_input_tokens} tok"),
            TechItem(label="sin cachear", value=f"{sin_cachear} tok ({parte:.0%})"),
        ],
        sample_trace_id=usage.sample_trace_id,
        step_key=usage.key,
    )


def _fixed_context_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
    table = get_price_table()
    encadenado = _cheaper_model_if_recommended(usage)
    price = table.lookup(encadenado or usage.model)
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = (
        f"En las {_miles(usage.calls)} llamadas del paso «{usage.name}», la más corta ya lleva "
        f"{_miles(usage.min_input_tokens)} tokens de entrada. Ese suelo es la parte que no "
        f"cambia "
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
    detalle.detection_query = query.strip()

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
            f"cuestan {cifras.dinero_exacto(_coste_de_escribir(price, escrituras))}."
            if price.cache_write is not None
            else " Este proveedor no cobra aparte por escribir en caché."
        )
        detalle.savings_calculation = (
            f"{_miles(usage.min_input_tokens)} tokens fijos × "
            f"{usage.calls - max(usage.traces, 1)} llamadas que ya encontrarían la caché "
            f"caliente = {_miles(lecturas)} tokens que pasarían de ${price.input}/1M a "
            f"${price.cached_input}/1M.{escritura_txt} Neto: "
            f"{cifras.dinero_exacto(finding.window_waste_usd)} en "
            f"{window_label(finding.observed_days)}.{_projection_sentence(finding)}"
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
