"""El motor: junta las reglas, quita los solapes y arma la vista y las fichas.

Parte del motor de detección (`laplace_backend.insights`, D-130).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from ..coverage import build as build_coverage
from ..dinero import motivo_sin_dinero
from ..pasos import SEPARADOR as SEPARADOR_DE_CAMINO
from ..pasos import con_pista
from ..storage.base import LoopGroup, ModelUsage, RepeatedGroup, Window, WindowSummary
from .bucle import _loop_detail, _loop_finding
from .contexto_fijo import _fixed_context_detail, _fixed_context_finding
from .grafico import construir as construir_grafico
from .modelo_caro import _expensive_model_detail, _expensive_model_finding
from .modelos import (
    CAUTION_SAVINGS_RATIO,
    MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK,
    MAX_SALIDAS_BUCLE,
    MIN_CALLS_FOR_MODEL_RULE,
    MIN_REPEATS,
    MIN_VUELTAS_BUCLE,
    Finding,
    FindingDetail,
    Overview,
    _projection_base,
    _to_monthly,
    observed_days,
)
from .repeticion import _repetition_detail, _repetition_finding

# ---------------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------------


def _sin_envoltorios(loops: list[LoopGroup]) -> list[LoopGroup]:
    """Un bucle visto desde dos alturas del árbol es un problema, no dos.

    Un agente decorado envuelve cada paso en un span propio, así que seis vueltas
    producen dos grupos: el del envoltorio —que no gasta tokens y sólo puede hablar de
    tiempo— y el de la llamada al modelo de dentro, que sí tiene dinero. El inicio los
    enseñaba como dos tarjetas, y después de D-115 con títulos distintos pero contando
    lo mismo: de diez cosas que arreglar, cuatro eran dos.

    **Que sean el mismo se sabe por los datos, no por el parecido.** El camino de
    llamada del span de modelo termina en el paso que lo envuelve —eso lo escribe el
    SDK, no lo deducimos— y además los dos tienen que cubrir las mismas trazas con las
    mismas vueltas. Si cualquiera de las dos cosas falla, son bucles distintos y se
    quedan los dos: ante la duda, enseñar de más, que es lo que ya hacíamos.

    Se queda el de dentro porque es el que **puede ponerle precio**: «este bucle te
    cuesta 0,60 $» acciona, y «te cuesta 2,4 s» acciona menos. Se pierden los pocos
    milisegundos del envoltorio alrededor de la llamada, que es el lado bueno por el que
    equivocarse (D-119).

    Lo que esto **no** toca: un bucle de herramientas sin llamada al modelo dentro no
    tiene quien lo sustituya, así que sigue saliendo con su tiempo, que es la promesa de
    no inventar dinero donde no lo hay.
    """
    # Los pasos desde los que se llama a un modelo en bucle, con sus trazas y vueltas.
    envueltos: dict[str, list[LoopGroup]] = {}
    for grupo in loops:
        if grupo.span_type != "llm" or not grupo.site:
            continue
        llamante = grupo.site.split(SEPARADOR_DE_CAMINO)[-1].strip()
        if llamante:
            envueltos.setdefault(llamante, []).append(grupo)

    def es_envoltorio(grupo: LoopGroup) -> bool:
        if grupo.span_type == "llm":
            return False
        # `step_key` de un span sin identidad de paso cae a su nombre, que es lo que
        # escribe el SDK en el camino del hijo. Se compara con eso y no con `name`,
        # que para entonces puede llevar ya el llamante pegado (D-115).
        dentro = envueltos.get(grupo.step_key.strip())
        if not dentro:
            return False
        return any(
            hijo.traces == grupo.traces and hijo.extra_spans == grupo.extra_spans
            for hijo in dentro
        )

    return [grupo for grupo in loops if not es_envoltorio(grupo)]


def _duplicate_tokens(
    groups: list[RepeatedGroup],
    loops: list[LoopGroup] | None = None,
) -> dict[tuple[str, str], tuple[int, int, int]]:
    """Lo que las reglas de repetición y de bucle ya reclaman, por (paso, modelo).

    Tres cuidados que este mapa ha necesitado aprender por las malas:

    1. **Se cruza por `step_key`, no por nombre.** Desde que las reglas agrupan por
       paso, cruzar por el nombre del span dejaría el descuento sin pareja y el ahorro
       de las repeticiones se contaría dos veces (D-061).
    2. **Se acumula, no se sobrescribe.** Un mismo paso genera un `dedup_hash` distinto
       por cada entrada repetida, así que varios grupos caen en la misma clave. El
       diccionario por comprensión que había antes se quedaba sólo con el último.
    3. **Los bucles cuentan igual que las repeticiones.** La regla de bucles entró en
       D-109 y nadie la enchufó aquí, así que la del modelo caro volvía a reclamar la
       diferencia de tarifa sobre las vueltas que el bucle ya daba por eliminadas. En el
       proyecto de demo eso prometía un 117 % de la factura, y el `min(suma, gasto)` de
       `overview()` lo convertía en un «puedes dejar de pagarlo todo» que la pantalla
       enseñaba como una buena noticia. Quinta cara del mismo fallo (D-117).

    Un bucle y una repetición exacta del mismo paso no se pisan entre sí: la consulta de
    bucles exige entradas distintas y la de repetición exige la misma. Por eso se suman
    los dos sin miedo a descontar de más.
    """
    total: dict[tuple[str, str], tuple[int, int, int]] = {}
    for group in [*groups, *(loops or [])]:
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


def _pasos_compartidos(usos: list[ModelUsage]) -> set[str]:
    """Los nombres de paso que en este proyecto llevan más de una identidad.

    Todas las llamadas al modelo hechas dentro de `responder` se llaman «responder»,
    aunque una clasifique, otra extraiga y otra conteste. Entre los usos por paso eso
    ya se desambigua (`disambiguate`); una repetición o un bucle, que salen de otra
    consulta, no se enteraban y se titulaban «Tu agente repite «responder»» cuando lo
    que se repetía era la extracción (D-135).
    """
    claves: dict[str, set[str]] = {}
    for uso in usos:
        claves.setdefault(_nombre_base(uso.name), set()).add(uso.key)
    return {nombre for nombre, ks in claves.items() if len(ks) > 1}


def _nombre_base(nombre: str) -> str:
    """El nombre de la función, sin llamante, pista, modelo ni variante."""
    return nombre.split(" — ")[0].split(" · ")[0].split(" (#")[0].split(" → ")[-1]


def _nombrar(grupos: list[Any], compartidos: set[str]) -> list[Any]:
    for grupo in grupos:
        if " — " in grupo.name:
            continue  # ya lleva su pista
        if _nombre_base(grupo.name) in compartidos and getattr(grupo, "hint", ""):
            grupo.name = con_pista(grupo.name, grupo.hint)
    return grupos


def _con_fecha(finding: Finding, grupo: Any) -> Finding:
    finding.last_seen = getattr(grupo, "last_seen", None)
    return finding


#: Cuánto tiempo sin ocurrir hace falta para dar un hallazgo por desaparecido: un día,
#: o la décima parte de la ventana si es mayor. Menos sería confundir una hora sin
#: tráfico con un arreglo.
DESAPARECIDO_MIN = timedelta(days=1)


def desaparecidos(
    findings: list[Finding], window: Window
) -> tuple[list[Finding], list[Finding]]:
    """Separa lo que sigue ocurriendo de lo que dejó de ocurrir dentro de la ventana.

    Con treinta días de ventana, un paso que se retiró hace nueve —la v1 de un prompt
    sustituida por la v2— salía como «te ahorras 34 $ al mes arreglándolo»: proyectaba
    a futuro un gasto que ya no existe. Lo que no ocurre desde hace tiempo se aparta con
    su fecha, sin dinero prometido, y el usuario lo ve igual (D-135).
    """
    margen = max(DESAPARECIDO_MIN, (window.until - window.since) / 10)
    corte = window.until - margen
    vigentes: list[Finding] = []
    idos: list[Finding] = []
    for finding in findings:
        if finding.last_seen is not None and finding.last_seen < corte:
            finding.state = "desaparecido"
            finding.state_at = finding.last_seen
            idos.append(finding)
        else:
            vigentes.append(finding)
    return vigentes, idos


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

    usos = store.model_usage(project_id, window, min_calls=1)
    compartidos = _pasos_compartidos(usos)

    grupos = _nombrar(
        store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS), compartidos
    )
    for group in grupos:
        findings.append(_con_fecha(_repetition_finding(group, summary, dias, base), group))

    # Los bucles van aparte de las repeticiones exactas y no se solapan con ellas: la
    # consulta exige entradas distintas, que es justo lo que la regla 1 no mira.
    bucles = _nombrar(
        _sin_envoltorios(
            store.loop_groups(
                project_id, window, min_vueltas=MIN_VUELTAS_BUCLE, max_salidas=MAX_SALIDAS_BUCLE
            )
        ),
        compartidos,
    )
    for bucle in bucles:
        findings.append(_con_fecha(_loop_finding(bucle, summary, dias, base), bucle))

    # Las reglas no pueden solaparse: si una llamada al modelo se repite, la regla de
    # repetición ya cuenta el 100% de las copias sobrantes. Contarlas otra vez en la
    # regla del modelo caro inflaría el ahorro total, que es el número que vendemos.
    # Se descuentan los tokens duplicados antes de evaluar el resto de reglas.
    duplicados = _duplicate_tokens(grupos, bucles)

    for uso in usos:
        neto = _without_duplicates(uso, duplicados)
        if neto.calls >= MIN_CALLS_FOR_MODEL_RULE and (
            (neto.p50_output_tokens or neto.avg_output_tokens)
            <= MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK
        ):
            hallazgo = _expensive_model_finding(neto, summary, dias, base, otros=usos)
            if hallazgo is not None:
                findings.append(_con_fecha(hallazgo, neto))
        contexto = _fixed_context_finding(neto, summary, dias, base)
        if contexto is not None:
            findings.append(_con_fecha(contexto, neto))

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
    store: Any,
    project_id: str,
    window: Window,
    *,
    has_managed_prompts: bool = False,
    states: dict[str, dict[str, Any]] | None = None,
) -> Overview:
    """El héroe del inicio: coste actual, coste evitable y métricas.

    Y, delante de todo eso, cuánto de este proyecto entendemos: un ahorro calculado
    sobre la mitad de las llamadas no es medio ahorro, es un número que no se puede
    leer sin saber que es la mitad (D-096).
    """
    # Cada lectura una sola vez: el resumen se pedía dos veces, y con volumen cada una
    # pasa del medio segundo (D-142).
    from .lecturas import Recordado

    store = Recordado(store)
    summary = store.summarize_window(project_id, window)
    findings = detect(store, project_id, window)
    apartados: list[Finding] = []
    if states:
        from ..seguimiento import aplicar_estados

        findings, apartados = aplicar_estados(store, project_id, findings, states)
    # Después de los estados: lo que el usuario marcó se sigue comprobando por su camino,
    # y aquí sólo se aparta lo que dejó de ocurrir sin que nadie dijera nada.
    findings, idos = desaparecidos(findings, window)
    apartados += idos
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
        set_aside=apartados,
        coverage=cobertura,
        chart=construir_grafico(store, project_id, window, findings),
    )


def _detalle_repeticion(
    store: Any, project_id: str, window: Window, key: str, ctx: _Contexto
) -> FindingDetail | None:
    # El mismo nombre que en la lista: la ficha y la tarjeta no pueden titularse distinto.
    compartidos = _pasos_compartidos(store.model_usage(project_id, window, min_calls=1))
    for group in _nombrar(
        store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS), compartidos
    ):
        if group.step_key != key:
            continue
        finding = _repetition_finding(group, ctx.summary, ctx.dias, ctx.base)
        evidencia = store.sample_repetition(project_id, window, group.dedup_hash)
        return _repetition_detail(finding, group, evidencia, store.repeated_groups_sql)
    return None


def _detalle_bucle(
    store: Any, project_id: str, window: Window, key: str, ctx: _Contexto
) -> FindingDetail | None:
    compartidos = _pasos_compartidos(store.model_usage(project_id, window, min_calls=1))
    for group in _nombrar(
        _sin_envoltorios(
            store.loop_groups(
                project_id, window, min_vueltas=MIN_VUELTAS_BUCLE, max_salidas=MAX_SALIDAS_BUCLE
            )
        ),
        compartidos,
    ):
        if (group.step_key or group.loop_hash) != key:
            continue
        finding = _loop_finding(group, ctx.summary, ctx.dias, ctx.base)
        evidencia = store.sample_loop(project_id, window, group.loop_hash)
        return _loop_detail(finding, group, evidencia, store.loop_groups_sql)
    return None


def _detalle_modelo(
    store: Any, project_id: str, window: Window, key: str, ctx: _Contexto
) -> FindingDetail | None:
    """Las reglas 2 y 3 comparten búsqueda: las dos cuelgan de un (paso, modelo)."""
    step_key, _, model = key.rpartition(":")
    duplicados = _duplicate_tokens(
        store.repeated_groups(project_id, window, min_repeats=MIN_REPEATS),
        _sin_envoltorios(
            store.loop_groups(
                project_id, window, min_vueltas=MIN_VUELTAS_BUCLE, max_salidas=MAX_SALIDAS_BUCLE
            )
        ),
    )
    for bruto in store.model_usage(project_id, window, min_calls=1):
        if bruto.key != step_key or bruto.model != model:
            continue
        uso = _without_duplicates(bruto, duplicados)
        if ctx.kind == "modelo_caro":
            todos = store.model_usage(project_id, window, min_calls=1)
            finding = _expensive_model_finding(uso, ctx.summary, ctx.dias, ctx.base, otros=todos)
            consulta = store.model_usage_sql
            return _expensive_model_detail(finding, uso, consulta) if finding else None
        finding = _fixed_context_finding(uso, ctx.summary, ctx.dias, ctx.base)
        return _fixed_context_detail(finding, uso, store.model_usage_sql) if finding else None
    return None


@dataclass
class _Contexto:
    """Lo que toda ficha necesita saber de la ventana, calculado una sola vez."""

    kind: str
    summary: WindowSummary
    dias: float
    base: float | None


#: De qué tipo de hallazgo sabe hacer ficha cada función. No es una lista decorativa:
#: `DETAILED_KINDS` sale de aquí y `test_catalogo_hallazgos` exige que coincida con
#: `FindingKind`, así que una regla nueva que se añada a `detect()` y no a esta tabla
#: pone la suite en rojo el mismo día. Antes no había nada que lo exigiera, y la regla
#: de bucles vivió cuatro tandas sin ficha: su hallazgo más caro llevaba a un 404 que
#: el usuario leía como «enhorabuena» (D-113).
_DETALLADORES = {
    "repeticion": _detalle_repeticion,
    "bucle": _detalle_bucle,
    "modelo_caro": _detalle_modelo,
    "contexto_fijo": _detalle_modelo,
}

#: Los tipos que `detail()` sabe reconstruir. Derivado, nunca escrito a mano: una lista
#: escrita a mano se queda desfasada afirmando que cubre algo que no cubre.
DETAILED_KINDS = frozenset(_DETALLADORES)


def detail(store: Any, project_id: str, window: Window, finding_id: str) -> FindingDetail | None:
    """Recompone la ficha de un hallazgo.

    Los identificadores son deterministas (`tipo:clave`), así que no hace falta guardar
    nada: se vuelve a calcular sobre la misma ventana y se busca el que coincide.
    """
    kind, _, key = finding_id.partition(":")
    detallador = _DETALLADORES.get(kind)
    if detallador is None:
        return None

    summary = store.summarize_window(project_id, window)
    ctx = _Contexto(
        kind=kind,
        summary=summary,
        dias=observed_days(summary, window),
        base=_projection_base(summary, window),
    )
    return detallador(store, project_id, window, key, ctx)
