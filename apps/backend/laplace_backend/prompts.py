"""Prompts (Fase 6): versiones con sus métricas reales pegadas.

Un gestor de prompts con diff y rollback lo tiene cualquiera, y no vale gran cosa: es
un repositorio de texto con otra cara. Lo que hace que esta pestaña sea de Laplace es
que **cada versión lleva pegado lo que costó y lo que acertó sobre el tráfico que la
usó**. «v8: 0,004 $ por ejecución, 94 % — v7: 0,007 $ por ejecución, 93 %» es una frase
que no se puede decir sin tener al lado las trazas y las anotaciones, y es la única
pantalla del producto donde lo que un prompt cuesta y lo que acierta se ven juntos.

Las guardas son las de siempre, y son **literalmente** las de Evaluaciones, no una
copia: `rate_for` y `compare_rates` se importan de `evals`. Si se hubieran reescrito
aquí, el día que alguien afinase el mínimo de casos en un sitio, el otro seguiría
enseñando un 94 % sacado de cuatro anotaciones (D-087).

Y una cosa que hay que decir en pantalla y no esconder: **el veredicto es de la
ejecución entera, no de este prompt**. Una traza pasa por varios pasos; si falla, no se
sabe cuál de ellos la estropeó. El acierto de una versión es el de las ejecuciones en
las que participó, que es una señal útil y no una atribución de culpa.
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime
from typing import Literal

from laplace.schema import Annotation, Prompt, PromptDeploy, PromptVersion
from pydantic import BaseModel, Field

from .evals import MATERIAL_COST_CHANGE, Rate, SourceComparison, compare_rates, rate_for
from .panel import MIN_TRACES_FOR_COMPARISON
from .storage.base import ObservedPrompt, PromptUsage

logger = logging.getLogger("laplace.prompts")

# ---------------------------------------------------------------------------------
# Umbrales, explícitos y con su motivo
# ---------------------------------------------------------------------------------

#: Ejecuciones que necesita una versión para que su coste por ejecución se compare en
#: porcentaje con el de otra. Es el mismo número que usa el panel para decidir si hay
#: periodo anterior con el que comparar, y se importa de allí a propósito: «¿hay
#: bastante tráfico para que este cociente signifique algo?» es la misma pregunta, y
#: dos respuestas distintas en dos pantallas serían un error esperando a que lo vean.
MIN_TRACES_FOR_COST = MIN_TRACES_FOR_COMPARISON

#: Juegos de instrucciones distintos que puede tener un paso antes de que dejemos de
#: leerlos como versiones. Por encima de esto, lo que pasa casi seguro es que el prompt
#: lleva datos variables dentro —una fecha, un nombre— y genera una huella por llamada:
#: eso no son doscientas versiones, es un prompt con plantilla, y decir lo contrario
#: sería llenar la pantalla de basura con aspecto de dato.
MAX_OBSERVED_VARIANTS = 8

#: La coletilla del coste, que se repite en cada lectura. El acierto lleva margen porque
#: es una muestra; el coste no, porque es la factura de lo que se ejecutó.
_SIN_MARGEN = "El coste no lleva margen: no es una muestra, es lo que se gastó."


# ---------------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------------


class DiffLine(BaseModel):
    """Una línea del diff entre dos versiones."""

    op: Literal["=", "+", "-"]
    text: str
    #: Número de línea en la versión de la izquierda y en la de la derecha. `None` en
    #: el lado donde la línea no existe.
    left: int | None = None
    right: int | None = None


class Diff(BaseModel):
    lines: list[DiffLine] = Field(default_factory=list)
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    #: La frase de una línea que resume el cambio, para la lista.
    summary: str = ""


def diff_versions(before: str, after: str) -> Diff:
    """Diff por líneas entre dos textos de prompt.

    Por líneas y no por palabras porque un prompt se edita por líneas: se añade una
    instrucción, se quita un ejemplo, se cambia el tono de una frase. Un diff por
    palabras de un párrafo reescrito es ilegible, y lo que hay que poder contestar es
    «¿qué le hice a este prompt?».
    """
    izquierda = before.splitlines()
    derecha = after.splitlines()
    diff = Diff()
    matcher = difflib.SequenceMatcher(None, izquierda, derecha, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, texto in enumerate(izquierda[i1:i2]):
                diff.lines.append(
                    DiffLine(op="=", text=texto, left=i1 + offset + 1, right=j1 + offset + 1)
                )
            diff.unchanged += i2 - i1
            continue
        for offset, texto in enumerate(izquierda[i1:i2]):
            diff.lines.append(DiffLine(op="-", text=texto, left=i1 + offset + 1))
            diff.removed += 1
        for offset, texto in enumerate(derecha[j1:j2]):
            diff.lines.append(DiffLine(op="+", text=texto, right=j1 + offset + 1))
            diff.added += 1

    partes = []
    if diff.added:
        partes.append(f"{diff.added} {'línea añadida' if diff.added == 1 else 'líneas añadidas'}")
    if diff.removed:
        partes.append(
            f"{diff.removed} {'línea quitada' if diff.removed == 1 else 'líneas quitadas'}"
        )
    diff.summary = ", ".join(partes) if partes else "sin cambios en el texto"
    return diff


# ---------------------------------------------------------------------------------
# Métricas de una versión
# ---------------------------------------------------------------------------------


class VersionMetrics(BaseModel):
    """Una versión y lo que hizo sobre el tráfico real.

    `cost_per_execution_usd` es `None` —y nunca cero— cuando esa versión no tiene
    tráfico en el rango: cero significaría «no cuesta nada», que es una afirmación
    distinta y normalmente falsa (D-073, otra vez).
    """

    version: int
    #: `v8`, o `reserva` para el tráfico que corrió con el texto del código porque
    #: Laplace no respondió. Ese tráfico existe y no es de ninguna versión guardada.
    label: str
    created_at: datetime | None = None
    author: str = ""
    notes: str = ""
    in_production: bool = False
    #: El texto sólo viaja en la ficha de un prompt, no en la lista: son kilobytes por
    #: versión y la lista de un proyecto puede tener decenas.
    text: str = ""

    traces: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    cost_per_execution_usd: float | None = None
    tokens_per_execution: float | None = None
    #: Por qué no hay coste por ejecución. Vacío cuando lo hay.
    cost_unavailable: str = ""
    #: Hay pasos sin tarifa o a tarifa asumida: la cifra es un suelo.
    cost_is_floor: bool = False
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    #: Una entrada por fuente de veredicto. Nunca se funden (D-083).
    rates: list[Rate] = Field(default_factory=list)
    #: La línea de la lista: «v8 · 0,004 $/ejecución · 94 %».
    headline: str = ""


class PromptComparison(BaseModel):
    """La versión en producción frente a la anterior que tuvo tráfico."""

    a_version: int
    b_version: int
    #: Variación del coste por ejecución de B respecto de A. `None` si no se puede.
    cost_change: float | None = None
    cost_unavailable: str = ""
    by_source: list[SourceComparison] = Field(default_factory=list)
    headline: str = ""
    detail: str = ""


class PromptCard(BaseModel):
    """Un prompt gestionado, con sus versiones y lo que cada una costó y acertó."""

    id: str
    name: str
    description: str = ""
    created_at: datetime
    production_version: int | None = None
    version_count: int = 0
    versions: list[VersionMetrics] = Field(default_factory=list)
    deploys: list[PromptDeploy] = Field(default_factory=list)
    comparison: PromptComparison | None = None
    #: Vacío cuando hay comparación. Cuando no, dice por qué no la hay.
    comparison_unavailable: str = ""
    #: Ejecuciones del rango que corrieron con el texto de reserva. Si esto no es cero,
    #: Laplace estuvo caído para ese agente y conviene saberlo.
    fallback_traces: int = 0


class ObservedVariant(BaseModel):
    """Un juego de instrucciones visto en las trazas, sin gestión de prompts."""

    step_key: str
    hint: str = ""
    traces: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    cost_per_execution_usd: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    rates: list[Rate] = Field(default_factory=list)


class ObservedStep(BaseModel):
    """Un paso del agente y los juegos de instrucciones con los que se le ha visto."""

    label: str
    variants: list[ObservedVariant] = Field(default_factory=list)
    traces: int = 0
    #: Las instrucciones cambian tantas veces que no pueden ser versiones: casi seguro
    #: llevan datos variables dentro. Se dice, y no se listan doscientas «versiones».
    unstable: bool = False
    note: str = ""


class PromptsView(BaseModel):
    """Todo lo que pinta la pestaña."""

    project_id: str
    days: int
    #: True cuando hay al menos un prompt gestionado. Cuando es False, la pestaña no se
    #: queda en blanco: enseña lo que se puede inferir de las trazas.
    managed: bool = False
    prompts: list[PromptCard] = Field(default_factory=list)
    observed: list[ObservedStep] = Field(default_factory=list)
    #: Por qué no hay nada que inferir, cuando tampoco hay eso.
    observed_unavailable: str = ""


# ---------------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------------


def _money(x: float) -> str:
    if x >= 1:
        return f"${x:.2f}"
    if x >= 0.01:
        return f"${x:.4f}".rstrip("0")
    return f"${x:.6f}".rstrip("0")


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.0f} %"


Verdicts = dict[tuple[str, int], dict[str, tuple[int, int]]]


def verdicts_by_version(
    annotations: list[Annotation], versions_by_trace: dict[str, list[tuple[str, int]]]
) -> Verdicts:
    """Aciertos y fallos por (prompt, versión) y fuente, a partir de lo anotado.

    Se cruza por traza: una anotación es de una ejecución, y una ejecución puede haber
    usado varias versiones de varios prompts. Cada una se lleva el veredicto entero,
    y **eso se dice en pantalla**: repartir un «mal» entre tres prompts sería inventarse
    una atribución de culpa que nadie ha medido.
    """
    salida: Verdicts = {}
    for anotacion in annotations:
        if anotacion.verdict not in ("pass", "fail"):
            continue
        for clave in versions_by_trace.get(anotacion.trace_id, []):
            por_fuente = salida.setdefault(clave, {})
            aciertos, fallos = por_fuente.get(anotacion.source, (0, 0))
            if anotacion.verdict == "pass":
                por_fuente[anotacion.source] = (aciertos + 1, fallos)
            else:
                por_fuente[anotacion.source] = (aciertos, fallos + 1)
    return salida


def _rates_for(traces: int, por_fuente: dict[str, tuple[int, int]]) -> list[Rate]:
    """Una tasa por fuente, con la guarda de Evaluaciones puesta (D-087).

    Las ejecuciones sin anotar no cuentan como fallo: contarlas convertiría «no lo he
    mirado» en «está mal», y el acierto de una versión bajaría por recibir más tráfico.
    """
    salida = []
    for fuente in ("human", "llm_judge"):
        aciertos, fallos = por_fuente.get(fuente, (0, 0))
        sin_juzgar = max(traces - aciertos - fallos, 0)
        salida.append(rate_for(fuente, aciertos, fallos, sin_juzgar))
    return salida


def _best_rate(rates: list[Rate]) -> Rate | None:
    """La tasa que se enseña en la línea de una versión.

    Si las personas han anotado bastante, manda la suya: un veredicto humano vale más
    que uno de máquina y esta línea sólo tiene sitio para una cifra. La del juez entra
    cuando la humana no tiene base, y siempre etiquetada como suya en la interfaz.
    """
    humana = next((r for r in rates if r.source == "human"), None)
    if humana is not None and humana.value is not None:
        return humana
    juez = next((r for r in rates if r.source == "llm_judge"), None)
    if juez is not None and juez.value is not None:
        return juez
    return humana or juez


def metrics_for(
    version: PromptVersion | None,
    usage: PromptUsage | None,
    por_fuente: dict[str, tuple[int, int]],
    *,
    in_production: bool = False,
    label: str = "",
    with_text: bool = False,
) -> VersionMetrics:
    """Una versión con sus cifras. Sin tráfico, las cifras son `None` y se dice."""
    numero = version.version if version else (usage.version if usage else 0)
    metrica = VersionMetrics(
        version=numero,
        label=label or (f"v{numero}" if numero else "reserva"),
        created_at=version.created_at if version else None,
        author=version.author if version else "",
        notes=version.notes if version else "",
        in_production=in_production,
        text=(version.text if version and with_text else ""),
    )
    if usage is None or usage.traces == 0:
        metrica.cost_unavailable = (
            "esta versión no ha corrido ninguna ejecución en el rango elegido"
        )
        metrica.rates = _rates_for(0, {})
        metrica.headline = f"{metrica.label} · sin tráfico en este rango"
        return metrica

    metrica.traces = usage.traces
    metrica.calls = usage.calls
    metrica.cost_usd = usage.cost_usd
    metrica.cost_per_execution_usd = usage.cost_usd / usage.traces
    metrica.tokens_per_execution = (usage.input_tokens + usage.output_tokens) / usage.traces
    metrica.cost_is_floor = bool(usage.unknown_cost_spans or usage.assumed_rate_spans)
    metrica.first_seen = usage.first_seen
    metrica.last_seen = usage.last_seen
    metrica.rates = _rates_for(usage.traces, por_fuente)

    suelo = "≥ " if metrica.cost_is_floor else ""
    mejor = _best_rate(metrica.rates)
    acierto = (
        _pct(mejor.value)
        if mejor is not None and mejor.value is not None
        else f"{mejor.passed}/{mejor.judged}"
        if mejor is not None and mejor.judged
        else "sin anotar"
    )
    metrica.headline = (
        f"{metrica.label} · {suelo}{_money(metrica.cost_per_execution_usd)} por ejecución "
        f"· {acierto}"
    )
    return metrica


# ---------------------------------------------------------------------------------
# La comparación entre dos versiones
# ---------------------------------------------------------------------------------


def compare_versions(a: VersionMetrics, b: VersionMetrics) -> PromptComparison:
    """La versión anterior (A) frente a la de producción (B).

    El acierto pasa por `compare_rates`, que es exactamente la función de la pestaña de
    Evaluaciones: si los márgenes se solapan **no hay ganador**, y se dice que no se
    distinguen con estos casos. El coste no lleva margen —es la factura de lo que se
    ejecutó— pero sí una guarda propia: con menos de un puñado de ejecuciones por lado,
    un porcentaje de ahorro es la diferencia entre dos anécdotas.
    """
    comparacion = PromptComparison(a_version=a.version, b_version=b.version)
    comparacion.by_source = [
        compare_rates(
            fuente,
            next(r for r in a.rates if r.source == fuente),
            next(r for r in b.rates if r.source == fuente),
        )
        for fuente in ("human", "llm_judge")
    ]

    coste_a, coste_b = a.cost_per_execution_usd, b.cost_per_execution_usd
    flojo = min(a.traces, b.traces) < MIN_TRACES_FOR_COST
    if coste_a is None or coste_b is None or coste_a <= 0:
        comparacion.cost_unavailable = (
            "alguna de las dos versiones no tiene tráfico en este rango, así que no hay "
            "dos costes que comparar"
        )
    elif flojo:
        comparacion.cost_unavailable = (
            f"una de las dos versiones tiene menos de {MIN_TRACES_FOR_COST} ejecuciones "
            f"en el rango: la diferencia de coste sería la que hay entre dos anécdotas"
        )
    else:
        comparacion.cost_change = (coste_b - coste_a) / coste_a

    comparacion.headline, comparacion.detail = _reading(a, b, comparacion)
    return comparacion


def _cost_phrase(comparacion: PromptComparison, b: VersionMetrics) -> str:
    if comparacion.cost_change is None:
        if b.cost_per_execution_usd is None:
            return "todavía no se sabe lo que cuesta"
        suelo = "al menos " if b.cost_is_floor else ""
        return f"cuesta {suelo}{_money(b.cost_per_execution_usd)} por ejecución"
    cambio = comparacion.cost_change
    if cambio < -MATERIAL_COST_CHANGE:
        return f"cuesta un {_pct(cambio)} menos por ejecución"
    if cambio > MATERIAL_COST_CHANGE:
        return f"cuesta un {_pct(cambio)} más por ejecución"
    return "cuesta prácticamente lo mismo"


def _reading(
    a: VersionMetrics, b: VersionMetrics, comparacion: PromptComparison
) -> tuple[str, str]:
    """La frase que junta coste y acierto para dos versiones de un prompt.

    Misma jerarquía que en Evaluaciones: si alguna fuente dice que empeora, manda ésa.
    Cuando se está decidiendo si dejar puesta una versión, ése es el lado por el que hay
    que equivocarse.
    """
    frase_coste = _cost_phrase(comparacion, b)
    detalle_coste = (
        f"v{a.version}: {_money(a.cost_per_execution_usd or 0.0)} por ejecución sobre "
        f"{a.traces} {'ejecución' if a.traces == 1 else 'ejecuciones'}. "
        f"v{b.version}: {_money(b.cost_per_execution_usd or 0.0)} sobre {b.traces} "
        f"{'ejecución' if b.traces == 1 else 'ejecuciones'}. "
        f"{comparacion.cost_unavailable or _SIN_MARGEN}"
    )

    con_base = [c for c in comparacion.by_source if c.verdict != "sin-base"]
    if not con_base:
        return (
            f"v{b.version} {frase_coste}; del acierto todavía no se puede decir nada.",
            f"Ninguna fuente de veredicto tiene casos suficientes sobre estas dos "
            f"versiones. {detalle_coste} Para saber si además acierta igual hacen falta "
            f"ejecuciones anotadas de las dos.",
        )

    peor = next((c for c in con_base if c.verdict == "peor"), None)
    if peor is not None:
        return (
            f"v{b.version} acierta menos que v{a.version}, aunque {frase_coste}.",
            f"{peor.detail} {detalle_coste}",
        )
    mejor = next((c for c in con_base if c.verdict == "mejor"), None)
    if mejor is not None:
        return (
            f"v{b.version} acierta más que v{a.version} y {frase_coste}.",
            f"{mejor.detail} {detalle_coste}",
        )
    return (
        f"v{b.version} acierta igual —hasta donde se puede saber— y {frase_coste}.",
        f"{con_base[0].detail} {detalle_coste}",
    )


# ---------------------------------------------------------------------------------
# Montaje de una ficha
# ---------------------------------------------------------------------------------


def build_card(
    prompt: Prompt,
    versions: list[PromptVersion],
    usage: list[PromptUsage],
    verdicts: Verdicts,
    deploys: list[PromptDeploy],
    *,
    with_text: bool = False,
) -> PromptCard:
    """Un prompt con sus versiones, sus métricas y su comparación."""
    por_version = {u.version: u for u in usage if u.name == prompt.name}
    card = PromptCard(
        id=prompt.id,
        name=prompt.name,
        description=prompt.description,
        created_at=prompt.created_at,
        production_version=prompt.production_version,
        version_count=prompt.version_count,
        deploys=deploys,
        fallback_traces=por_version.get(0).traces if por_version.get(0) else 0,
    )

    for version in sorted(versions, key=lambda v: v.version, reverse=True):
        card.versions.append(
            metrics_for(
                version,
                por_version.get(version.version),
                verdicts.get((prompt.name, version.version), {}),
                in_production=version.version == prompt.production_version,
                with_text=with_text,
            )
        )

    # El tráfico de reserva no es una versión, pero es tráfico real y se enseña al
    # final: si no se dijera, alguien leería el coste de producción como si fuera todo
    # el gasto del prompt, y faltaría justo el de los ratos en que Laplace no respondía.
    if 0 in por_version:
        card.versions.append(
            metrics_for(
                None,
                por_version[0],
                verdicts.get((prompt.name, 0), {}),
                label="reserva",
            )
        )

    card.comparison, card.comparison_unavailable = _comparison_for(card)
    return card


def _comparison_for(card: PromptCard) -> tuple[PromptComparison | None, str]:
    """La versión en producción frente a la otra versión con tráfico más cercana.

    Dos decisiones dentro de una frase corta:

    * **Con tráfico**, no la inmediatamente anterior. Si alguien guardó tres borradores
      seguidos y sólo desplegó el tercero, comparar contra un borrador que no corrió
      nunca sería comparar contra el vacío, que es D-080 con otra cara.
    * **La más cercana en cualquier dirección**, no sólo hacia atrás. Justo después de
      un rollback, la de producción es la vieja y la única con tráfico al lado es la
      nueva; si sólo se mirase hacia atrás, la pantalla se quedaría muda exactamente en
      el momento en que alguien acaba de tomar una decisión y quiere ver qué ha hecho.

    El par se ordena por número de versión, así que la frase siempre habla de la más
    nueva respecto de la más vieja, vaya cuál vaya puesta.
    """
    if card.production_version is None:
        return None, "este prompt todavía no tiene ninguna versión en producción"

    produccion = next(
        (v for v in card.versions if v.version == card.production_version), None
    )
    if produccion is None or produccion.traces == 0:
        return None, (
            "la versión en producción todavía no ha corrido ninguna ejecución en este "
            "rango, así que no hay nada suyo que comparar"
        )

    otras = [
        v
        for v in card.versions
        if v.traces > 0 and v.version > 0 and v.version != produccion.version
    ]
    if not otras:
        return None, (
            "no hay ninguna otra versión con tráfico en este rango: la de producción es "
            "la única que ha corrido, y compararla consigo misma no diría nada"
        )

    # La más cercana, y a igualdad de distancia gana la anterior: «qué he cambiado» se
    # pregunta más veces que «qué me queda por delante».
    vecina = min(
        otras,
        key=lambda v: (abs(v.version - produccion.version), v.version > produccion.version),
    )
    vieja, nueva = sorted((vecina, produccion), key=lambda v: v.version)
    return compare_versions(vieja, nueva), ""


# ---------------------------------------------------------------------------------
# El modo degradado: lo que se puede inferir de las trazas
# ---------------------------------------------------------------------------------


def observed_steps(
    observed: list[ObservedPrompt],
    verdicts_by_step: dict[str, dict[str, tuple[int, int]]] | None = None,
) -> list[ObservedStep]:
    """Agrupa lo visto en las trazas por paso, con la guarda de la huella partida.

    La identidad de un paso ya incluye la huella de sus instrucciones (D-060), así que
    dos claves bajo la misma etiqueta son dos juegos de instrucciones del mismo paso: un
    cambio de prompt que se puede fechar aunque nadie haya adoptado nada.

    La guarda importa tanto como el dato. Un prompt de sistema con una fecha dentro
    genera una huella por llamada, y sin esto la pantalla enseñaría doscientas
    «versiones» de un paso que en realidad tiene una. Cuando pasa, se dice lo que es.
    """
    por_paso: dict[str, list[ObservedPrompt]] = {}
    for fila in observed:
        por_paso.setdefault(fila.step_label or "(sin nombre)", []).append(fila)

    salida: list[ObservedStep] = []
    for etiqueta, filas in por_paso.items():
        filas.sort(key=lambda f: (f.last_seen or f.first_seen or datetime.min), reverse=True)
        paso = ObservedStep(
            label=etiqueta,
            traces=sum(f.traces for f in filas),
        )
        if len(filas) > MAX_OBSERVED_VARIANTS:
            paso.unstable = True
            paso.note = (
                f"Las instrucciones de este paso cambian casi en cada ejecución "
                f"({len(filas)} variantes distintas). Casi seguro llevan datos variables "
                f"dentro —una fecha, un nombre, el contexto del usuario—, así que no son "
                f"versiones de un prompt: son un prompt con plantilla. Sácalos a "
                f"variables y Laplace podrá contarte qué versión cuesta qué."
            )
            salida.append(paso)
            continue

        for fila in filas:
            paso.variants.append(
                ObservedVariant(
                    step_key=fila.step_key,
                    hint=fila.hint,
                    traces=fila.traces,
                    calls=fila.calls,
                    cost_usd=fila.cost_usd,
                    cost_per_execution_usd=(
                        fila.cost_usd / fila.traces if fila.traces else None
                    ),
                    first_seen=fila.first_seen,
                    last_seen=fila.last_seen,
                    rates=_rates_for(
                        fila.traces, (verdicts_by_step or {}).get(fila.step_key, {})
                    ),
                )
            )
        salida.append(paso)

    salida.sort(key=lambda p: sum(v.cost_usd for v in p.variants), reverse=True)
    return salida


__all__ = [
    "Diff",
    "DiffLine",
    "MAX_OBSERVED_VARIANTS",
    "MIN_TRACES_FOR_COST",
    "ObservedStep",
    "ObservedVariant",
    "PromptCard",
    "PromptComparison",
    "PromptsView",
    "VersionMetrics",
    "build_card",
    "compare_versions",
    "diff_versions",
    "metrics_for",
    "observed_steps",
    "verdicts_by_version",
]
