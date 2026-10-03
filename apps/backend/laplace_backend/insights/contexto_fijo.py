"""Regla 3 — contexto fijo reenviado en cada llamada.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from typing import Any

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import ModelUsage, WindowSummary
from ..textos import t
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
        t(
            "contexto.de_cache.parte",
            cacheados=_miles(usage.cached_input_tokens),
            sin_cachear=_miles(sin_cachear),
        )
        if usage.cached_input_tokens
        else t("contexto.de_cache.ninguno")
    )
    # El bot de PR sólo sabe escribir la caché de Anthropic: OpenAI cachea solo, sin
    # nada que poner en el código (D-190).
    propone_cachear = parte >= MIN_PARTE_SIN_CACHEAR
    if parte < MIN_PARTE_SIN_CACHEAR:
        # No se propone cachear, así que tampoco se apunta el ahorro de cachear: sería
        # prometer dinero por hacer lo que ya se está haciendo.
        ahorro = 0.0
    if ahorro <= 0 and coste_lecturas <= 0 and get_price_table().lookup(usage.model):
        # Hay tarifa y aun así no hay nada que recuperar cacheando: el paso manda su
        # prefijo una vez por ejecución y la caché no sobrevive de una a otra (la
        # suposición prudente), o el modelo no ofrece caché. Antes se llegaba aquí al
        # «no tiene tarifa conocida» de abajo, que era falso (D-135).
        return None
    if ahorro > 0:
        precio = t("contexto.precio.cachear", ahorro=_money(ahorro))
    elif coste_lecturas > 0:
        # Aquí no se propone cachear —ya lo está— sino mandar menos.
        parte_txt = (
            t(
                "contexto.precio.parte",
                parte=cifras.porcentaje(coste_lecturas / summary.total_cost_usd),
            )
            if summary.total_cost_usd > 0
            else ""
        )
        precio = t("contexto.precio.lecturas", coste=_money(coste_lecturas), parte=parte_txt)
    else:
        precio = t("contexto.precio.sin_tarifa")

    # El dinero del hallazgo es lo que de verdad se puede recuperar: lo que ahorraría
    # cachear lo que no se cachea, más lo que cuestan las lecturas del prefijo fijo.
    ahorro += coste_lecturas

    return Finding(
        id=f"contexto_fijo:{usage.key}:{usage.model}",
        kind="contexto_fijo",
        title=t("contexto.titulo", tokens=_miles(usage.min_input_tokens)),
        summary=t(
            "contexto.resumen",
            paso=usage.name,
            tokens=_miles(usage.min_input_tokens),
            llamadas=_miles(usage.calls),
            de_cache=de_cache,
            precio=precio,
        ),
        lead=t("contexto.lead"),
        window_waste_usd=ahorro,
        window_waste_tokens=sin_cachear,
        cost_unavailable="" if ahorro > 0 else t("hallazgo.fuera_de_tabla", modelo=usage.model),
        costs_money=ahorro > 0,
        monthly_saving_usd=_to_monthly(ahorro, base) if ahorro > 0 else None,
        observed_days=days,
        **_floor_flags(usage.unknown_cost_spans, usage.assumed_rate_spans, [usage.model]),
        difficulty="mid",
        difficulty_label=t("dificultad.un_rato"),
        scope_label=_scope_label(usage.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.paso"), value=usage.name),
            TechItem(label=t("tec.entrada_fija"), value=t("tec.tok", n=usage.min_input_tokens)),
            TechItem(
                label=t("tec.entrada_media"),
                value=t("tec.tok", n=cifras.miles(usage.avg_input_tokens)),
            ),
            TechItem(label=t("tec.llamadas"), value=str(usage.calls)),
            TechItem(label=t("tec.reenviado"), value=t("tec.tok", n=reenviado)),
            TechItem(
                label=t("tec.servido_cache"), value=t("tec.tok", n=usage.cached_input_tokens)
            ),
            TechItem(
                label=t("tec.sin_cachear"),
                value=t("tec.tok_parte", n=sin_cachear, parte=cifras.porcentaje(parte)),
            ),
        ],
        sample_trace_id=usage.sample_trace_id,
        step_key=usage.key,
        code_fix="cache" if propone_cachear and es_de_anthropic(usage.model) else "",
    )


def es_de_anthropic(modelo: str) -> bool:
    """Por el nombre, que es lo que hay en el paso: también `anthropic.claude-…` de
    Bedrock y `claude-…@fecha` de Vertex, que aceptan el mismo `cache_control`."""
    return "claude" in modelo.lower()


def _fixed_context_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
    table = get_price_table()
    encadenado = _cheaper_model_if_recommended(usage)
    price = table.lookup(encadenado or usage.model)
    detalle = FindingDetail(**finding.model_dump())

    detalle.what_happens = t(
        "contexto.que_pasa",
        llamadas=_miles(usage.calls),
        paso=usage.name,
        tokens=_miles(usage.min_input_tokens),
    )
    detalle.why = t("contexto.por_que")
    detalle.detection_explanation = t(
        "contexto.deteccion",
        llamadas=MIN_CALLS_FOR_CONTEXT_RULE,
        minimo=_miles(MIN_FIXED_INPUT_TOKENS),
    )
    detalle.detection_query = query.strip()

    detalle.fix_steps = [
        FixStep(
            title=t("contexto.arreglo.cache.titulo"),
            body=t("contexto.arreglo.cache.texto"),
            code=t("contexto.arreglo.cache.codigo"),
        ),
        FixStep(
            title=t("contexto.arreglo.menos.titulo"),
            body=t("contexto.arreglo.menos.texto"),
        ),
    ]
    cuentas = _cache_arithmetic(usage)
    if price is not None and cuentas is not None:
        lecturas, escrituras, _ = cuentas
        escritura_txt = (
            t(
                "contexto.escritura.cobra",
                tokens=_miles(escrituras),
                precio=cifras.dinero(price.cache_write),
                coste=cifras.dinero_exacto(_coste_de_escribir(price, escrituras)),
            )
            if price.cache_write is not None
            else t("contexto.escritura.gratis")
        )
        detalle.savings_calculation = t(
            "contexto.ahorro",
            tokens=_miles(usage.min_input_tokens),
            llamadas=_miles(usage.calls - max(usage.traces, 1)),
            lecturas=_miles(lecturas),
            entrada=cifras.dinero(price.input),
            cacheada=cifras.dinero(price.cached_input),
            escritura=escritura_txt,
            neto=cifras.dinero_exacto(finding.window_waste_usd),
            ventana=window_label(finding.observed_days),
            proyeccion=_projection_sentence(finding),
        )
    if encadenado:
        detalle.savings_calculation += t(
            "contexto.encadenado", barato=encadenado, modelo=usage.model
        )
    detalle.savings_note = t("contexto.nota")
    return detalle
