"""Tipos, umbrales y utilidades de redacción que comparten las reglas.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from laplace.schema import Span
from pydantic import BaseModel, Field

from .. import cifras
from ..coverage import Coverage
from ..storage.base import Window, WindowSummary
from ..textos import t, tn

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


class FixCheck(BaseModel):
    """Antes y después de marcar un hallazgo como arreglado (D-123).

    Todo por ejecución y no en totales: si después de marcarlo hay la mitad de tráfico,
    el total baja solo y parecería un arreglo que no ha ocurrido.
    """

    marked_at: datetime
    #: `usd`, `tokens` o `ms`: lo mismo que el hallazgo enseña en su tarjeta.
    unit: str = "usd"
    runs_before: int = 0
    runs_after: int = 0
    #: `None` cuando esa ventana no tiene ejecuciones: no es cero, es que no hay datos.
    before_per_run: float | None = None
    after_per_run: float | None = None
    #: Lo que ya no se ha gastado desde que se marcó, al ritmo de antes. `None` sin base.
    saved: float | None = None
    #: `pendiente` (pocas ejecuciones), `arreglado`, `mejor` o `sigue`.
    verdict: str = "pendiente"
    headline: str = ""
    #: Si el dinero de antes era un suelo, lo ahorrado también lo es.
    cost_is_floor: bool = False


class Finding(BaseModel):
    """Un hallazgo, tal y como aparece en la lista del inicio."""

    id: str
    kind: FindingKind
    #: Título en lenguaje llano, sujeto "tu agente". Sin jerga.
    title: str
    summary: str
    #: Una frase, para la tarjeta del inicio. El título ya dice qué pasa y la cifra
    #: cuánto cuesta: esto dice lo único que falta para decidir si abrirlo (D-124). El
    #: resumen entero sigue en la ficha y en modo avanzado.
    lead: str = ""

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
    #: El dinero sale, en parte o entero, de tarifas de LiteLLM sin verificar (D-138).
    #: No es un suelo: puede quedarse corto o pasarse, y quien afirme la cifra lo dice.
    cost_unverified: bool = False
    unverified_rate_models: list[str] = Field(default_factory=list)

    difficulty: Difficulty = "easy"
    difficulty_label: str = ""
    scope_label: str = ""
    #: `False` cuando el hallazgo cuesta tiempo pero no dinero.
    costs_money: bool = True

    tech: list[TechItem] = Field(default_factory=list)
    sample_trace_id: str = ""
    #: Identidad del paso implicado. Es con lo que la ficha filtra «las trazas
    #: afectadas»: el título no sirve, porque lleva el llamante o una pista del prompt.
    step_key: str = ""
    #: La última vez que ocurrió en la ventana. Si hace días que no ocurre, no se
    #: promete ahorro por arreglarlo: se aparta como `desaparecido` (D-135).
    last_seen: datetime | None = None

    #: Lo que el usuario ha dicho de él: `arreglado`, `ignorado`, o `reaparecido`
    #: cuando lo marcó como arreglado y sigue saliendo igual. Vacío si nada (D-123).
    state: str = ""
    state_at: datetime | None = None
    state_note: str = ""
    fix_check: FixCheck | None = None


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


class TramoGasto(BaseModel):
    """Un tramo del gráfico del Diagnóstico (D-152)."""

    start: datetime
    #: Gasto medido en el tramo, por el instante de cada span.
    cost_usd: float = 0.0
    #: Lo evitable **repartido** a este tramo: en proporción a lo que gastó aquí cada
    #: paso con un hallazgo. No es una medida del tramo, y la pantalla lo dice.
    avoidable_usd: float = 0.0


class GastoPaso(BaseModel):
    """Un paso del gráfico de coste por paso (D-152)."""

    key: str
    name: str
    cost_usd: float = 0.0
    avoidable_usd: float = 0.0


class Grafico(BaseModel):
    """El gasto de la ventana en el tiempo y por paso, con su parte evitable (D-152)."""

    bucket_minutes: int
    buckets: list[TramoGasto] = Field(default_factory=list)
    steps: list[GastoPaso] = Field(default_factory=list)
    #: Lo que gastaron los pasos que no caben en la lista.
    other_steps_usd: float = 0.0
    #: Evitable que no se ha podido repartir porque su paso no tiene gasto en la serie.
    unattributed_usd: float = 0.0


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
    #: Los que el usuario ha marcado como arreglados o ignorados. No suman al evitable:
    #: el evitable es lo que todavía se puede hacer, y éstos ya tienen respuesta.
    set_aside: list[Finding] = Field(default_factory=list)

    #: Cuánto de este proyecto entendemos. Va en el Overview y no en una pantalla
    #: aparte a propósito: si la cobertura es baja, hay que enterarse **antes** de leer
    #: la cifra de ahorro, no después de ir a buscarla (D-096).
    coverage: Coverage | None = None

    #: El gasto en el tiempo y por paso, con lo evitable encima (D-152). `None` cuando
    #: no hay gasto que dibujar.
    chart: Grafico | None = None


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
        return t("alcance.todas")
    de_cada = round(ratio * 10)
    if de_cada >= 1:
        return t("alcance.de_cada_10", n=de_cada)
    return t("alcance.de_total", n=cifras.miles(affected), total=cifras.miles(total))


def _miles(n: float) -> str:
    """12345 -> «12.345». Alias de `cifras.miles`, que es quien sabe hacerlo."""
    return cifras.miles(n)


def _seconds(ms: float) -> str:
    """Una espera con los separadores del idioma: «4,6 s» en español, «4.6 s» en inglés."""
    if ms < 1000:
        return f"{_miles(round(ms))} ms"
    return f"{cifras.decimal(ms / 1000)} s"


def _medida(days: float) -> tuple[str, float] | None:
    """La unidad que se lee mejor y la cifra en ella, o `None` si es menos de un minuto.

    Espejo de `medir()` en `apps/web/lib/format.ts`.
    """
    if days >= 1:
        return ("dias", 1 if abs(days - 1) < 0.05 else days)
    horas = days * 24
    if horas >= 1.5:
        return ("horas", horas)
    if horas >= 0.95:
        return ("horas", 1)
    minutos = round(horas * 60)
    return None if minutos <= 0 else ("minutos", minutos)


def span_label(days: float) -> str:
    """Una duración en palabras: «12 minutos», «1 hora», «5,2 horas», «2,5 días»."""
    medida = _medida(days)
    if medida is None:
        return t("tiempo.menos_de_un_minuto")
    unidad, n = medida
    return tn(f"tiempo.{unidad}", n, n=_decimal(n))


def _money(value: float) -> str:
    """Un importe, escrito por el único sitio que sabe escribir números (D-120)."""
    return cifras.dinero(value)


def _decimal(value: float) -> str:
    """Un decimal, coma española, y sin el «,0» que sobra en «7,0 días»."""
    return cifras.decimal(value)


def window_label(days: float) -> str:
    """La misma duración, como ventana: «la última hora», «los últimos 2,5 días».

    Una cifra de dinero sin la ventana que la respalda no quiere decir nada, y es
    exactamente lo que hacía la proyección mensual sobre una hora de datos. Vive aquí
    porque lo usan la API, las alertas y —traducido a TypeScript— la interfaz.
    """
    medida = _medida(days)
    if medida is None:
        return t("tiempo.menos_de_un_minuto")
    unidad, n = medida
    return tn(f"ventana.{unidad}", n, n=_decimal(n))


def _floor_flags(unknown: int, assumed: int, models: Any = ()) -> dict[str, Any]:
    """Marcas de «esta cifra es un suelo», compartidas por las tres reglas.

    Un paso cuyo modelo no está en la tabla aporta cero al dinero del hallazgo, y uno
    cobrado a tarifa asumida puede haber costado más de lo que decimos: en los dos
    casos la cifra se queda corta. Se marcan aquí, en un solo sitio, porque quien la
    afirma —la pantalla o una alerta a Slack— tiene que poder saberlo sin recalcular.
    """
    from ..pricing import modelos_no_verificados

    no_verificados = modelos_no_verificados(models)
    return {
        "unknown_cost_spans": unknown,
        "assumed_rate_spans": assumed,
        "cost_is_floor": bool(unknown or assumed),
        # Aparte del suelo: una tarifa sin verificar no dice «al menos» (D-141).
        "cost_unverified": bool(no_verificados),
        "unverified_rate_models": no_verificados,
    }


def _projection_sentence(finding: Finding) -> str:
    """La frase que explica la proyección… o la que explica por qué no la hay.

    Antes esta frase se escribía siempre, incluso cuando la cifra mensual salía de una
    hora de datos. Decir «extrapolado a 30 días» sin decir desde dónde es la mitad del
    problema que arregla D-073.
    """
    if finding.monthly_saving_usd is None:
        return t(
            "proyeccion.no",
            minimo=span_label(MIN_DAYS_FOR_PROJECTION),
            hay=span_label(finding.observed_days),
        )
    return t(
        "proyeccion.si", ventana=window_label(finding.observed_days), dias=DAYS_PER_MONTH
    )
