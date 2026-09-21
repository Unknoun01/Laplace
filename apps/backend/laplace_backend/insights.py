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

from .coverage import Coverage
from .coverage import build as build_coverage
from .dinero import motivo_sin_dinero
from .pricing import get_price_table
from .storage.base import LoopGroup, ModelUsage, RepeatedGroup, Window, WindowSummary

logger = logging.getLogger("laplace.insights")

FindingKind = Literal["repeticion", "modelo_caro", "contexto_fijo", "bucle"]
Difficulty = Literal["easy", "mid", "hard"]

DAYS_PER_MONTH = 30
#: Tokens por unidad de tarifa. La tabla de precios da dólares por millón.
_MILLION = 1_000_000.0

#: Por encima de esta proporción del gasto, un ahorro deja de sonar creíble aunque los
#: números salgan. La interfaz lo presenta con cautela y enseña el desglose completo.
CAUTION_SAVINGS_RATIO = 0.60
#: Por debajo de esto NO se proyecta a mes. No se avisa de que la cifra es frágil: se
#: enseña lo que se ha gastado de verdad, con la ventana que lo respalda. Con una hora
#: de datos, multiplicar por 720 produce un número absurdo, y el número se lee antes
#: que el aviso: quema la confianza en todo lo demás en la primera pantalla, que es
#: justo el momento del modo local recién instalado (D-073).
MIN_DAYS_FOR_PROJECTION = 1.0

# Umbrales de las reglas. Configurables por proyecto cuando haya ajustes por proyecto;
# hoy son constantes explícitas para que se puedan leer y discutir.
MIN_REPEATS = 3
#: Vueltas mínimas para llamar bucle a algo. Con tres, un reintento normal —que es una
#: buena práctica— saldría señalado.
MIN_VUELTAS_BUCLE = 4
#: Y salidas distintas como mucho: muchas vueltas con dos resultados distintos es la
#: definición medible de «da vueltas sin avanzar». Un bucle que procesa seis pedidos
#: distintos produce seis salidas distintas y no entra aquí (D-109).
MAX_SALIDAS_BUCLE = 2
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
    #: Proyección a 30 días al ritmo de la ventana. Es una estimación, y es `None`
    #: cuando no hay días suficientes para hacerla: entonces sólo existe lo observado.
    monthly_saving_usd: float | None = None
    #: Días de datos reales detrás de `window_waste_usd`. La pantalla lo enseña junto
    #: a la cifra: una cantidad sin su ventana no quiere decir nada.
    observed_days: float = 0.0
    currency: str = "USD"
    #: Segundos perdidos. Es lo que se enseña cuando el desperdicio no es dinero.
    window_waste_ms: float = 0.0
    #: Tokens de más, medidos. Existen aunque el modelo no tenga tarifa, y son lo que
    #: se enseña cuando no se puede poner precio: un dato, no una estimación (D-107).
    window_waste_tokens: int = 0
    #: Por qué no hay cifra en dólares, cuando no la hay. La pantalla lo enseña **en
    #: lugar** del dinero, nunca un 0 con el aviso debajo.
    cost_unavailable: str = ""

    #: El dinero de este hallazgo es un SUELO, no un total: alguno de los pasos que
    #: lo componen tiene el coste incompleto (modelo sin tarifa) o cobrado a tarifa
    #: asumida. Quien afirme la cifra —la pantalla, una alerta— tiene que decirlo.
    cost_is_floor: bool = False
    unknown_cost_spans: int = 0
    assumed_rate_spans: int = 0

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
    #: Lo evitable y lo necesario DENTRO de la ventana: dinero ya gastado, medido.
    #: Existen siempre, hasta con minutos de datos, y son lo que se enseña cuando no
    #: se puede proyectar.
    window_avoidable_usd: float = 0.0
    window_necessary_usd: float = 0.0

    #: Proyección a 30 días al ritmo de la ventana. Las tres son `None` a la vez
    #: cuando no hay base para proyectar: o se proyectan gasto y ahorro sobre los
    #: mismos días, o la barra de reparto miente (D-058).
    monthly_cost_usd: float | None = None
    monthly_avoidable_usd: float | None = None
    monthly_necessary_usd: float | None = None

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
    #: Por qué no se puede poner precio, cuando ninguna llamada tiene tarifa. La
    #: pantalla enseña esto **en lugar** de la cifra grande: un «$0» enorme con el aviso
    #: debajo se lee como «no cuesta nada», que es lo contrario de lo que decimos
    #: (D-073, D-107).
    cost_unavailable: str = ""
    #: Pasos cobrados a tarifa estándar sin poder confirmar qué metro aplicó. El coste
    #: real podría ser mayor, así que la cifra se presenta como suelo, no como total.
    assumed_rate_spans: int = 0
    #: Lo que la caché ya ha ahorrado en la ventana. Es dinero medido —sale de restar
    #: la tarifa de lectura a la de entrada sobre tokens reales—, no una promesa.
    window_cache_saving_usd: float = 0.0

    #: Días reales de datos en la ventana, que no son los que pide el selector. Es la
    #: base de la proyección, y la pantalla la enseña siempre que proyecte.
    observed_days: float = 0.0
    #: True cuando hay días suficientes y los campos `monthly_*` traen cifra.
    projected: bool = False
    #: Días de datos que hacen falta para proyectar. La pantalla lo usa para decir
    #: cuánto queda en lugar de dejar el hueco sin explicar.
    min_days_for_projection: float = MIN_DAYS_FOR_PROJECTION
    #: Qué parte del gasto observado es evitable, entre 0 y 1. Se calcula sobre la
    #: ventana y no sobre la proyección: así existe también cuando no se proyecta, y
    #: es exactamente el mismo número en los dos casos.
    avoidable_ratio: float = 0.0
    #: True cuando el evitable pasa del umbral de cautela: la UI lo presenta con
    #: reservas en lugar de como promesa.
    savings_needs_caution: bool = False

    findings: list[Finding] = Field(default_factory=list)

    #: Cuánto de este proyecto entendemos. Va en el Overview y no en una pantalla
    #: aparte a propósito: si la cobertura es baja, hay que enterarse **antes** de leer
    #: la cifra de ahorro, no después de ir a buscarla (D-096).
    coverage: Coverage | None = None


# ---------------------------------------------------------------------------------
# Utilidades de redacción
# ---------------------------------------------------------------------------------


def _to_monthly(amount: float, days: float | None) -> float | None:
    """Extrapola a 30 días, o `None` si no hay base sobre la que extrapolar.

    `None` y no cero: cero se lee como «no cuesta nada», que es justo lo contrario de
    lo que queremos decir. Quien reciba `None` tiene que enseñar lo observado.
    """
    if days is None or days <= 0:
        return None
    return amount / days * DAYS_PER_MONTH


def _projection_base(summary: WindowSummary, window: Window) -> float | None:
    """Los días sobre los que se puede proyectar, o `None` si no dan para tanto.

    Un solo sitio decide si se proyecta, y devuelve la MISMA base para el gasto y para
    el ahorro. Que las dos cifras salgan de bases distintas ya nos mordió una vez
    (D-058): la barra de reparto del inicio comparaba un total multiplicado por 720 con
    un ahorro multiplicado por 4,3.
    """
    dias = observed_days(summary, window)
    return dias if dias >= MIN_DAYS_FOR_PROJECTION else None


def observed_days(summary: WindowSummary, window: Window) -> float:
    """Días de datos reales, no los que pide el selector.

    Un proyecto que empezó a enviar trazas hace dos horas no tiene siete días de datos
    aunque el rango diga «7 días». Proyectar un mes desde esa ventana infla la cifra
    sola, así que se proyecta sobre lo observado —y sólo cuando lo observado da para
    tanto (D-073)—.

    **Lo usa todo el motor, no sólo el héroe.** Si el gasto total se proyectase sobre lo
    observado y el ahorro sobre los días del selector, las dos cifras no serían
    comparables y la barra de reparto del inicio mentiría: con una hora de datos en una
    ventana de siete días, el total se multiplicaría por 720 y el ahorro por 4,3.

    Ya no hay suelo de una hora. Existía para que dividir no explotase, y de paso
    convertía «llevas diez segundos de datos» en «llevas una hora», que es una ventana
    que nadie ha observado. Ahora la división está protegida por la puerta de la
    proyección, así que este número puede decir la verdad y ser cero.
    """
    if not summary.first_seen or not summary.last_seen:
        return 0.0
    span = (summary.last_seen - summary.first_seen).total_seconds() / 86_400
    return max(min(span, float(window.days)), 0.0)


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


def span_label(days: float) -> str:
    """Una duración en palabras: «12 minutos», «1 hora», «5,2 horas», «2,5 días»."""
    if days >= 1:
        if abs(days - 1) < 0.05:
            return "1 día"
        return f"{_decimal(days)} días"
    horas = days * 24
    if horas >= 1.5:
        return f"{_decimal(horas)} horas"
    if horas >= 0.95:
        return "1 hora"
    minutos = round(horas * 60)
    if minutos <= 0:
        return "menos de un minuto"
    return "1 minuto" if minutos == 1 else f"{minutos} minutos"


def _money(value: float) -> str:
    """Un importe pequeño escrito de forma legible. Los de un hallazgo suelen ser
    céntimos, y `$0.00` se lee como cero cuando no lo es."""
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def _decimal(value: float) -> str:
    """Un decimal, coma española, y sin el «,0» que sobra en «7,0 días»."""
    return f"{value:.1f}".removesuffix(".0").replace(".", ",")


def window_label(days: float) -> str:
    """La misma duración, como ventana: «la última hora», «los últimos 2,5 días».

    Una cifra de dinero sin la ventana que la respalda no quiere decir nada, y es
    exactamente lo que hacía la proyección mensual sobre una hora de datos. Vive aquí
    porque lo usan la API, las alertas y —traducido a TypeScript— la interfaz.
    """
    texto = span_label(days)
    if texto == "menos de un minuto":
        return texto
    if texto == "1 hora":
        return "la última hora"
    if texto == "1 día":
        return "el último día"
    if texto == "1 minuto":
        return "el último minuto"
    if texto.endswith("horas"):
        return f"las últimas {texto}"
    return f"los últimos {texto}"


def _floor_flags(unknown: int, assumed: int) -> dict[str, Any]:
    """Marcas de «esta cifra es un suelo», compartidas por las tres reglas.

    Un paso cuyo modelo no está en la tabla aporta cero al dinero del hallazgo, y uno
    cobrado a tarifa asumida puede haber costado más de lo que decimos: en los dos
    casos la cifra se queda corta. Se marcan aquí, en un solo sitio, porque quien la
    afirma —la pantalla o una alerta a Slack— tiene que poder saberlo sin recalcular.
    """
    return {
        "unknown_cost_spans": unknown,
        "assumed_rate_spans": assumed,
        "cost_is_floor": bool(unknown or assumed),
    }


def _projection_sentence(finding: Finding) -> str:
    """La frase que explica la proyección… o la que explica por qué no la hay.

    Antes esta frase se escribía siempre, incluso cuando la cifra mensual salía de una
    hora de datos. Decir «extrapolado a 30 días» sin decir desde dónde es la mitad del
    problema que arregla D-073.
    """
    if finding.monthly_saving_usd is None:
        return (
            f" No se proyecta a mes: el mínimo para proyectar es "
            f"{span_label(MIN_DAYS_FOR_PROJECTION)} de datos, y de momento hay "
            f"{span_label(finding.observed_days)}."
        )
    return (
        f" La proyección mensual extrapola ese ritmo de "
        f"{window_label(finding.observed_days)} a {DAYS_PER_MONTH} días."
    )


# ---------------------------------------------------------------------------------
# Regla 1 — el mismo paso repetido dentro de una traza
# ---------------------------------------------------------------------------------


def _repetition_finding(
    group: RepeatedGroup, summary: WindowSummary, days: float, base: float | None
) -> Finding:
    monthly = _to_monthly(group.extra_cost_usd, base)
    cuesta = group.extra_cost_usd > 0
    # Los tokens de las copias sobrantes se miden siempre; el dinero sólo si hay
    # tarifa. Decir «no gasta tokens de más» porque el coste salió 0 era convertir
    # «no lo sabemos» en «no cuesta» (D-107).
    tokens_de_mas = int(group.extra_input_tokens + group.extra_output_tokens)
    sin_tarifa = not cuesta and tokens_de_mas > 0

    if cuesta:
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. La primera ya trae la respuesta; "
            f"las demás se pagan igual."
        )
    elif sin_tarifa:
        # Se gastan tokens de verdad, pero el modelo no está en la tabla de precios: se
        # dice lo que se mide y se dice por qué no hay euros.
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. Las copias de más gastan "
            f"{_miles(tokens_de_mas)} tokens y {_seconds(group.extra_duration_ms)} de espera. "
            "No podemos decirte cuánto dinero es: ese modelo no tiene tarifa conocida."
        )
    else:
        # Honestidad: una herramienta repetida no gasta tokens. Lo que se pierde es tiempo.
        resumen = (
            f"En una misma ejecución, tu agente llama a «{group.name}» con exactamente los "
            f"mismos datos hasta {group.max_per_trace} veces. No gasta tokens de más, pero "
            f"cada vuelta añade espera: {_seconds(group.extra_duration_ms)} tirados en total."
        )

    return Finding(
        id=f"repeticion:{group.step_key}",
        kind="repeticion",
        title=f"Tu agente repite «{group.name}» hasta {group.max_per_trace} veces seguidas",
        summary=resumen,
        window_waste_usd=group.extra_cost_usd,
        monthly_saving_usd=monthly,
        observed_days=days,
        window_waste_ms=group.extra_duration_ms,
        window_waste_tokens=tokens_de_mas,
        cost_unavailable=(
            "ese modelo no tiene tarifa conocida, así que no podemos ponerle precio"
            if sin_tarifa
            else ""
        ),
        **_floor_flags(group.extra_unknown_cost_spans, group.extra_assumed_rate_spans),
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
    finding: Finding, group: RepeatedGroup, evidence: list[Span], query: str
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
    detalle.detection_query = query.strip()

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
            f"{group.extra_spans} pasos de más en {group.traces} trazas durante "
            f"{window_label(finding.observed_days)}, que suman ${group.extra_cost_usd:.6f} de "
            f"coste ya gastado.{_projection_sentence(finding)} Sólo cuenta las ocurrencias "
            f"posteriores a la primera de cada traza; la primera es trabajo legítimo."
        )
        detalle.savings_note = (
            "El dinero ya gastado está medido, no estimado: es la suma del coste de las "
            "ocurrencias que sobran. Lo estimado es la proyección a un mes, que sale de "
            "suponer que el ritmo se mantiene."
            if finding.monthly_saving_usd is not None
            else "El dinero ya gastado está medido, no estimado: es la suma del coste de "
            "las ocurrencias que sobran. Lo que todavía no podemos decirte es a cuánto "
            "va el mes."
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


def _modelo_caro_sin_tarifa(
    usage: ModelUsage, otros: list[ModelUsage], summary: WindowSummary, days: float
) -> Finding | None:
    """La misma regla cuando no hay tabla de precios: se mide en tiempo, no en dinero.

    Un modelo local no tiene tarifa y nunca la tendrá, así que la versión de dinero de
    esta regla no puede disparar jamás: quien use Ollama —un estudiante, cualquiera
    probando— se quedaba sin dos de las tres reglas (D-108). Lo que sí se mide siempre
    es el tiempo y los tokens, y con eso se puede decir algo cierto: este paso responde
    cuatro palabras y lo hace con el modelo que más tarda de los que ya usas.

    Lo que **no** se dice es cuánto dinero ahorraría, porque no se sabe.
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
    return Finding(
        id=f"modelo_caro:{usage.key}:{usage.model}",
        kind="modelo_caro",
        title=f"Un paso muy corto se lo lleva el modelo más lento: «{usage.name}»",
        summary=(
            f"«{usage.name}» responde con {salida:.0f} tokens en una llamada normal y usa "
            f"{usage.model}, que en tu propio tráfico tarda {ms_actual / ms_rapido:.1f} veces "
            f"más por llamada que {nombre_rapido}, un modelo que ya usas. Cambiarlo te "
            f"ahorraría {_seconds(ahorro_ms)} en esta ventana. Cuánto dinero, no lo sabemos: "
            f"{usage.model} no tiene tarifa conocida."
        ),
        window_waste_usd=0.0,
        window_waste_ms=ahorro_ms,
        window_waste_tokens=usage.output_tokens,
        cost_unavailable=f"{usage.model} no está en la tabla de precios",
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
            TechItem(label="salida media", value=f"{usage.avg_output_tokens:.0f} tok"),
            TechItem(label="ms por llamada", value=f"{ms_actual:.0f} vs {ms_rapido:.0f}"),
        ],
        sample_trace_id=usage.sample_trace_id,
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
        # Sin tarifa no hay dinero que prometer, pero sigue habiendo algo que decir.
        return _modelo_caro_sin_tarifa(usage, otros or [], summary, days)
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
        title=f"Usas el modelo caro para un paso muy corto: «{usage.name}»",
        summary=(
            f"Ese paso responde con {usage.avg_output_tokens:.0f} tokens de media, que es una "
            f"respuesta muy breve. Con {price.alternative} en lugar de {usage.model}, el mismo "
            f"trabajo costaría {veces_txt} menos."
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
            TechItem(label="salida media", value=f"{usage.avg_output_tokens:.0f} tok"),
        ],
        sample_trace_id=usage.sample_trace_id,
    )


def _expensive_model_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
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
                "Un modelo más pequeño no siempre decide igual. Pasa unos cuantos casos "
                "reales por los dos y compara antes de dejarlo fijo. Cuando exista la capa de "
                "evaluación podrás hacerlo desde aquí; hoy toca a mano."
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
            f"${finding.window_waste_usd:.6f} en {window_label(finding.observed_days)}."
            f"{_projection_sentence(finding)}"
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
            f"Reenvías las mismas {_miles(usage.min_input_tokens)} palabras en cada llamada"
        ),
        summary=(
            f"Todas las llamadas del paso «{usage.name}» empiezan con al menos "
            f"{_miles(usage.min_input_tokens)} tokens idénticos: instrucciones, ejemplos o "
            f"catálogo que no cambian. Los envías {usage.calls} veces. {de_cache}{precio}"
        ),
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
    )


def _fixed_context_detail(
    finding: Finding, usage: ModelUsage, query: str
) -> FindingDetail:
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
            f"cuestan ${escrituras * max(price.cache_write - price.input, 0.0) / 1_000_000:.6f}."
            if price.cache_write is not None
            else " Este proveedor no cobra aparte por escribir en caché."
        )
        detalle.savings_calculation = (
            f"{_miles(usage.min_input_tokens)} tokens fijos × "
            f"{usage.calls - max(usage.traces, 1)} llamadas que ya encontrarían la caché "
            f"caliente = {_miles(lecturas)} tokens que pasarían de ${price.input}/1M a "
            f"${price.cached_input}/1M.{escritura_txt} Neto: "
            f"${finding.window_waste_usd:.6f} en {window_label(finding.observed_days)}."
            f"{_projection_sentence(finding)}"
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


def _duplicate_tokens(
    groups: list[RepeatedGroup],
) -> dict[tuple[str, str], tuple[int, int, int]]:
    """Lo que la regla de repetición ya reclama, por (paso, modelo).

    Dos cuidados que este mapa ha necesitado aprender por las malas:

    1. **Se cruza por `step_key`, no por nombre.** Desde que las reglas agrupan por
       paso, cruzar por el nombre del span dejaría el descuento sin pareja y el ahorro
       de las repeticiones se contaría dos veces (D-061).
    2. **Se acumula, no se sobrescribe.** Un mismo paso genera un `dedup_hash` distinto
       por cada entrada repetida, así que varios grupos caen en la misma clave. El
       diccionario por comprensión que había antes se quedaba sólo con el último.
    """
    total: dict[tuple[str, str], tuple[int, int, int]] = {}
    for group in groups:
        if group.span_type != "llm" or not group.model or not group.step_key:
            continue
        clave = (group.step_key, group.model)
        entrada, salida, llamadas = total.get(clave, (0, 0, 0))
        total[clave] = (
            entrada + group.extra_input_tokens,
            salida + group.extra_output_tokens,
            llamadas + group.extra_spans,
        )
    return total


def _without_duplicates(
    usage: ModelUsage, duplicates: dict[tuple[str, str], tuple[int, int, int]]
) -> ModelUsage:
    """El mismo uso, descontando lo que ya cuenta la regla de repetición.

    Se descuentan los tokens **y las llamadas**. Las llamadas importan tanto como los
    tokens: la regla del contexto fijo cuenta cuántas veces se reenvía el prompt, y si
    de esas llamadas la mitad no deberían existir, prometer que las cachearemos es
    prometer un ahorro sobre trabajo que la otra regla ya ha dado por eliminado. Es la
    cuarta forma que ha encontrado este proyecto de contar dos veces el mismo dinero.
    """
    extra_in, extra_out, extra_calls = duplicates.get((usage.key, usage.model), (0, 0, 0))
    if not extra_in and not extra_out and not extra_calls:
        return usage

    input_tokens = max(usage.input_tokens - extra_in, 0)
    output_tokens = max(usage.output_tokens - extra_out, 0)
    calls = max(usage.calls - extra_calls, 0)
    return replace(
        usage,
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        avg_output_tokens=(output_tokens / calls) if calls else 0.0,
        avg_input_tokens=(input_tokens / calls) if calls else 0.0,
    )


def detect(store: Any, project_id: str, window: Window) -> list[Finding]:
    """Ejecuta las tres reglas y devuelve los hallazgos ordenados por dinero."""
    summary = store.summarize_window(project_id, window)
    if summary.spans == 0:
        return []

    # Los mismos días sobre los que se proyecta el gasto total: si no, el ahorro y el
    # coste del héroe estarían en escalas distintas y su cociente no querría decir nada.
    dias = observed_days(summary, window)
    base = _projection_base(summary, window)
    findings: list[Finding] = []

    grupos = store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS)
    for group in grupos:
        findings.append(_repetition_finding(group, summary, dias, base))

    # Los bucles van aparte de las repeticiones exactas y no se solapan con ellas: la
    # consulta exige entradas distintas, que es justo lo que la regla 1 no mira.
    for bucle in store.loop_groups(
        project_id, window, min_vueltas=MIN_VUELTAS_BUCLE, max_salidas=MAX_SALIDAS_BUCLE
    ):
        findings.append(_loop_finding(bucle, summary, dias, base))

    # Las reglas no pueden solaparse: si una llamada al modelo se repite, la regla de
    # repetición ya cuenta el 100% de las copias sobrantes. Contarlas otra vez en la
    # regla del modelo caro inflaría el ahorro total, que es el número que vendemos.
    # Se descuentan los tokens duplicados antes de evaluar el resto de reglas.
    duplicados = _duplicate_tokens(grupos)

    usos = store.model_usage(project_id, window, min_calls=1)
    for uso in usos:
        neto = _without_duplicates(uso, duplicados)
        if neto.calls >= MIN_CALLS_FOR_MODEL_RULE and (
            (neto.p50_output_tokens or neto.avg_output_tokens)
            <= MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK
        ):
            hallazgo = _expensive_model_finding(neto, summary, dias, base, otros=usos)
            if hallazgo is not None:
                findings.append(hallazgo)
        contexto = _fixed_context_finding(neto, summary, dias, base)
        if contexto is not None:
            findings.append(contexto)

    # Primero lo que más dinero devuelve; los que sólo cuestan tiempo, al final,
    # ordenados por el tiempo que recuperan. Se ordena por el dinero YA GASTADO y no
    # por el proyectado: los dos dan el mismo orden —la proyección multiplica a todos
    # por lo mismo—, pero el gasto observado existe siempre, también cuando no hay
    # días para proyectar y `monthly_saving_usd` es `None`.
    # Primero el dinero; cuando no hay tarifa no hay dinero que ordenar, y entonces
    # manda lo que sí se mide: tokens y después tiempo (D-108).
    findings.sort(
        key=lambda f: (f.window_waste_usd, f.window_waste_tokens, f.window_waste_ms),
        reverse=True,
    )
    return findings


def overview(
    store: Any, project_id: str, window: Window, *, has_managed_prompts: bool = False
) -> Overview:
    """El héroe del inicio: coste actual, coste evitable y métricas.

    Y, delante de todo eso, cuánto de este proyecto entendemos: un ahorro calculado
    sobre la mitad de las llamadas no es medio ahorro, es un número que no se puede
    leer sin saber que es la mitad (D-096).
    """
    summary = store.summarize_window(project_id, window)
    findings = detect(store, project_id, window)
    cobertura = build_coverage(
        store.coverage(project_id, window), has_managed_prompts=has_managed_prompts
    )

    # Se proyecta sobre los días que de verdad hay datos, no sobre los que pide el
    # selector; y por debajo de un día no se proyecta en absoluto (D-073).
    observados = observed_days(summary, window)
    base = _projection_base(summary, window)

    # Lo observado existe siempre. Es lo que se enseña cuando no se puede proyectar, y
    # el suelo del que sale la proyección cuando sí.
    evitable_ventana = min(
        sum(f.window_waste_usd for f in findings), summary.total_cost_usd
    )
    mensual = _to_monthly(summary.total_cost_usd, base)
    evitable = _to_monthly(evitable_ventana, base)
    ratio = (
        evitable_ventana / summary.total_cost_usd if summary.total_cost_usd > 0 else 0.0
    )
    # Una sola decisión, la misma que usa el Panel (D-107).
    sin_dinero = motivo_sin_dinero(
        llm_calls=summary.llm_calls, unknown_cost_calls=summary.unknown_cost_spans
    )

    return Overview(
        project_id=project_id,
        days=window.days,
        unknown_cost_spans=summary.unknown_cost_spans,
        models_without_price=summary.models_without_price,
        assumed_rate_spans=summary.assumed_rate_spans,
        window_cache_saving_usd=summary.cache_saving_usd,
        # Sin redondear: lo redondea quien lo pinta (`span_label`), y con dos
        # decimales de día —14 minutos— la ventana de un proyecto recién instalado se
        # deformaba justo donde más importa.
        observed_days=observados,
        projected=base is not None,
        avoidable_ratio=ratio,
        savings_needs_caution=ratio > CAUTION_SAVINGS_RATIO,
        cost_unavailable=sin_dinero,
        window_cost_usd=summary.total_cost_usd,
        window_avoidable_usd=evitable_ventana,
        window_necessary_usd=max(summary.total_cost_usd - evitable_ventana, 0.0),
        monthly_cost_usd=mensual,
        monthly_avoidable_usd=evitable,
        monthly_necessary_usd=(
            None if mensual is None or evitable is None else max(mensual - evitable, 0.0)
        ),
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
        coverage=cobertura,
    )


def detail(store: Any, project_id: str, window: Window, finding_id: str) -> FindingDetail | None:
    """Recompone la ficha de un hallazgo.

    Los identificadores son deterministas (`tipo:clave`), así que no hace falta guardar
    nada: se vuelve a calcular sobre la misma ventana y se busca el que coincide.
    """
    kind, _, key = finding_id.partition(":")
    summary = store.summarize_window(project_id, window)
    dias = observed_days(summary, window)
    base = _projection_base(summary, window)

    if kind == "repeticion":
        for group in store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS):
            if group.step_key != key:
                continue
            finding = _repetition_finding(group, summary, dias, base)
            evidencia = store.sample_repetition(project_id, window, group.dedup_hash)
            return _repetition_detail(finding, group, evidencia, store.repeated_groups_sql)
        return None

    if kind in ("modelo_caro", "contexto_fijo"):
        step_key, _, model = key.rpartition(":")
        duplicados = _duplicate_tokens(
            store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS)
        )
        for bruto in store.model_usage(project_id, window, min_calls=1):
            if bruto.key != step_key or bruto.model != model:
                continue
            uso = _without_duplicates(bruto, duplicados)
            if kind == "modelo_caro":
                finding = _expensive_model_finding(uso, summary, dias, base)
                consulta = store.model_usage_sql
                return _expensive_model_detail(finding, uso, consulta) if finding else None
            finding = _fixed_context_finding(uso, summary, dias, base)
            return _fixed_context_detail(finding, uso, store.model_usage_sql) if finding else None
        return None

    return None
