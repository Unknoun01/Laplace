"""Regla 5 — una versión nueva de un prompt que encarece cada ejecución (D-157).

Prompts era una pestaña a la que había que ir a mirar: enseñaba que la v8 cuesta un
40 % más por ejecución que la v7, y el Diagnóstico —que es donde se decide qué
arreglar— no se enteraba. Con esta regla, Prompts es una fuente más de hallazgos: el
cambio de versión que encarece el agente sale en la lista con su dinero, y el arreglo
es volver a la versión anterior desde la propia pestaña.

Parte del motor de detección (`laplace_backend.insights`, D-130).

Lo que la regla exige antes de afirmar nada, y por qué:

* **Las dos versiones con tráfico suficiente** (`MIN_TRACES_FOR_COST`, el mismo mínimo
  que la comparación de la pestaña): con tres ejecuciones por lado, un porcentaje es la
  diferencia entre dos anécdotas.
* **Que la cara sea la nueva y la que está corriendo ahora.** La que corre es la de la
  última llamada vista. Tras volver atrás, la que corre es la vieja y no hay nada que
  decir: el arreglo ya está hecho.
* **Una diferencia material** (`MATERIAL_COST_CHANGE`, la de Evaluaciones): un 3 % es
  ruido de tokenización.
* **Todo el coste con tarifa, en las dos.** Una resta con un sumando desconocido no es
  un suelo, es otro desconocido: sin tarifa, la regla se calla.
* **No contar dos veces el mismo ahorro.** Lo que otras reglas ya reclaman sobre las
  llamadas de la versión cara —su modelo, su contexto sin caché, sus repeticiones—
  abarata también la diferencia: se cuenta sólo la parte que queda (ver
  `_prompt_finding`); si no queda nada, no hay hallazgo.

Lo que no sabe: si la versión cara acierta más. Los veredictos viven en la base de
metadatos y el motor lee sólo trazas, así que no lo afirma en ningún sentido; la ficha
lleva a Prompts, donde las dos versiones se comparan con su acierto al lado, y a
probarlo en Evaluaciones antes de volver atrás.
"""

from __future__ import annotations

from .. import cifras
from ..evals import MATERIAL_COST_CHANGE
from ..storage.base import PromptUsage, WindowSummary
from ..textos import t
from .modelos import (
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

#: El mismo mínimo que la comparación de versiones de la pestaña de Prompts
#: (`prompts.MIN_TRACES_FOR_COST`, que sale del panel): la pregunta «¿hay tráfico para
#: que este cociente signifique algo?» es la misma. No se importa porque el panel
#: importa el motor; `test_regla_prompt_caro.py` exige que sean iguales.
MIN_TRACES = 5

KIND = "prompt_caro"


def finding_id(nombre: str, version: int) -> str:
    """`prompt_caro:<nombre>:v<versión cara>`. La versión va dentro: si después la v9
    vuelve a encarecer, es otro problema y no hereda lo que se dijo de la v8."""
    return f"{KIND}:{nombre}:v{version}"


def _por_ejecucion(uso: PromptUsage) -> float:
    return uso.cost_usd / uso.traces


def pares(usos: list[PromptUsage]) -> list[tuple[PromptUsage, PromptUsage]]:
    """(anterior, actual) para cada prompt cuya versión actual encarece la anterior.

    `actual` es la versión de la última llamada vista; `anterior`, la más alta de las
    anteriores a ella con tráfico suficiente. Sin tarifa en cualquiera de las dos, nada.
    """
    por_nombre: dict[str, list[PromptUsage]] = {}
    for uso in usos:
        # La versión 0 es el texto de reserva del SDK (Laplace no respondió): no es
        # una versión que se pueda desplegar ni a la que volver.
        if uso.version > 0 and uso.traces > 0 and uso.last_seen is not None:
            por_nombre.setdefault(uso.name, []).append(uso)

    salida = []
    for nombre in sorted(por_nombre):
        versiones = por_nombre[nombre]
        actual = max(versiones, key=lambda u: (u.last_seen, u.version))
        anteriores = [
            u for u in versiones if u.version < actual.version and u.traces >= MIN_TRACES
        ]
        if actual.traces < MIN_TRACES or not anteriores:
            continue
        anterior = max(anteriores, key=lambda u: u.version)
        if any(u.unknown_cost_spans for u in (anterior, actual)):
            continue
        base = _por_ejecucion(anterior)
        if base <= 0:
            continue
        if (_por_ejecucion(actual) - base) / base <= MATERIAL_COST_CHANGE:
            continue
        salida.append((anterior, actual))
    return salida


def _prompt_finding(
    anterior: PromptUsage,
    actual: PromptUsage,
    reclamado: float,
    summary: WindowSummary,
    days: float,
    base: float | None,
) -> Finding | None:
    """El hallazgo, o `None` si no queda nada después de las otras reglas.

    `reclamado` es lo que otras reglas ya cuentan como evitable sobre las llamadas de la
    versión cara (por sus `step_keys`). Los arreglos **se componen, no se suman**: si
    cambiar de modelo abarata esas llamadas un 64 %, volver a la versión anterior ahorra
    después su diferencia a ese precio, no a la de hoy. Así que la diferencia se escala
    por lo que queda del coste de la versión cuando las otras reglas han cobrado lo
    suyo. La suma de todo nunca pasa del coste de esas llamadas. Restar la cantidad
    entera, que fue lo primero que se escribió, dejaba la regla muda justo en la demo,
    donde el modelo caro reclama más que la diferencia (D-157).
    """
    coste_a, coste_b = _por_ejecucion(anterior), _por_ejecucion(actual)
    bruto = (coste_b - coste_a) * actual.traces
    queda = max(0.0, 1.0 - reclamado / actual.cost_usd) if actual.cost_usd > 0 else 0.0
    neto = bruto * queda
    # Menos de un céntimo de milésima no es un hallazgo: es el resto de una división.
    if neto <= 1e-9:
        return None
    cambio = (coste_b - coste_a) / coste_a
    valores = {
        "prompt": actual.name,
        "a": anterior.version,
        "b": actual.version,
        "pct": cifras.porcentaje(cambio),
    }
    return Finding(
        id=finding_id(actual.name, actual.version),
        kind=KIND,
        title=t("prompt_caro.titulo", **valores),
        summary=t(
            "prompt_caro.resumen",
            **valores,
            coste_a=_money(coste_a),
            coste_b=_money(coste_b),
            trazas=_miles(actual.traces),
            extra=_money(neto),
        ),
        lead=t("prompt_caro.lead", **valores),
        window_waste_usd=neto,
        monthly_saving_usd=_to_monthly(neto, base),
        observed_days=days,
        window_waste_tokens=0,
        **_floor_flags(0, anterior.assumed_rate_spans + actual.assumed_rate_spans),
        difficulty="easy",
        difficulty_label=t("dificultad.volver_version"),
        scope_label=_scope_label(actual.traces, summary.traces),
        costs_money=True,
        tech=[
            TechItem(label=t("tec.senal"), value=t("tec.senal.prompt_caro")),
            TechItem(label=t("tec.prompt"), value=actual.name),
            TechItem(
                label=t("tec.por_ejecucion"),
                value=f"v{anterior.version} {_money(coste_a)} → v{actual.version} "
                f"{_money(coste_b)}",
            ),
            TechItem(
                label=t("tec.ejecuciones"),
                value=f"v{anterior.version} {_miles(anterior.traces)} · "
                f"v{actual.version} {_miles(actual.traces)}",
            ),
            TechItem(label=t("tec.ya_reclamado"), value=_money(reclamado)),
        ],
        # El paso de la traza de ejemplo: es el filtro de «ver las trazas afectadas» y
        # donde el gráfico reparte el dinero. Casi siempre un prompt vive en un solo
        # paso; si vive en varios, se ve el de la pareja mayor, sin azar.
        step_key=actual.sample_step_key,
        sample_trace_id=actual.sample_trace_id,
        last_seen=actual.last_seen,
    )


def _prompt_detail(
    finding: Finding, anterior: PromptUsage, actual: PromptUsage, reclamado: float
) -> FindingDetail:
    detalle = FindingDetail(**finding.model_dump())
    coste_a, coste_b = _por_ejecucion(anterior), _por_ejecucion(actual)
    valores = {"prompt": actual.name, "a": anterior.version, "b": actual.version}
    detalle.what_happens = t(
        "prompt_caro.que_pasa",
        **valores,
        coste_a=_money(coste_a),
        coste_b=_money(coste_b),
        trazas_a=_miles(anterior.traces),
        trazas_b=_miles(actual.traces),
    )
    detalle.why = t("prompt_caro.por_que", **valores)
    detalle.detection_explanation = t(
        "prompt_caro.deteccion",
        minimo=MIN_TRACES,
        umbral=cifras.porcentaje(MATERIAL_COST_CHANGE),
    )
    detalle.detection_query = t("prompt_caro.consulta")
    detalle.fix_steps = [
        FixStep(
            title=t("prompt_caro.arreglo.comparar.titulo", **valores),
            body=t("prompt_caro.arreglo.comparar.texto", **valores),
        ),
        FixStep(
            title=t("prompt_caro.arreglo.volver.titulo", **valores),
            body=t("prompt_caro.arreglo.volver.texto", **valores),
        ),
        FixStep(
            title=t("prompt_caro.arreglo.recortar.titulo"),
            body=t("prompt_caro.arreglo.recortar.texto", **valores),
            advanced=True,
        ),
    ]
    detalle.savings_calculation = t(
        "prompt_caro.ahorro",
        **valores,
        coste_a=cifras.dinero_exacto(coste_a),
        coste_b=cifras.dinero_exacto(coste_b),
        trazas=_miles(actual.traces),
        bruto=cifras.dinero_exacto((coste_b - coste_a) * actual.traces),
        reclamado=cifras.dinero_exacto(reclamado),
        total=cifras.dinero_exacto(actual.cost_usd),
        parte=cifras.porcentaje(max(0.0, 1.0 - reclamado / actual.cost_usd)),
        neto=cifras.dinero_exacto(finding.window_waste_usd),
        ventana=window_label(finding.observed_days),
        proyeccion=_projection_sentence(finding),
    )
    detalle.savings_note = t("prompt_caro.nota")
    return detalle
