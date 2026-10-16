"""Replay contrafactual: las llamadas reales de un paso, reenviadas a otro modelo (D-167).

    laplace replay "clasificar · 12 ejecuciones" --modelo gpt-5.6-luna --tope 1

o desde Python:

    import laplace

    laplace.init(project="mi-agente")
    laplace.replay_dataset("clasificar · 12 ejecuciones", model="gpt-5.6-luna", max_usd=1)

Es el paso «probar» del ciclo sin escribir código: en lugar de correr el agente sobre
cada caso, se reenvían **las mismas llamadas** que hizo el paso, con los mismos
mensajes, al modelo barato, y el juez compara cada respuesta con la que dio el original.

Tres garantías, por este orden:

1. **Corre aquí, con tus claves.** Laplace dice qué llamadas se pueden reenviar; las
   reenvía el cliente de OpenAI o Anthropic de este proceso, con la clave de su entorno.
   Laplace no guarda claves de proveedor ni gasta dinero de nadie (D-086).
2. **No se pasa del tope.** Antes de cada llamada se suma lo peor que puede costar —la
   entrada original con un 30 % de margen, porque otro tokenizador puede contar más, y
   la salida máxima permitida— y si con eso se pasaría, no se hace. Sin tarifa del
   modelo de destino no hay tope que cumplir, y no se reenvía nada.
3. **Con permiso.** Antes de gastar se dice cuántas llamadas, lo que costaron y lo que
   costarán como mucho, y se pregunta. `yes=True` (o `--si`) es para quien ya lo sabe.

Sólo se reenvía lo que no puede tener efectos: llamadas hoja del paso, con los mensajes
en texto. Lo demás se cuenta por motivo.

Las que usan herramientas se repiten como la conversación entera (D-205): el modelo
nuevo pide herramientas y recibe, como resultado, el que quedó grabado en la traza. La
herramienta de verdad no se ejecuta nunca. Si pide una herramienta o unos argumentos que
no se grabaron en ese punto, no se inventa nada: el caso se corta y cuenta como fallo.

El resultado son dos tiradas del mismo conjunto: «original», que apunta a las llamadas
reenviadas dentro de las trazas reales (sin gastar nada), y la del modelo nuevo. Se
comparan en la pestaña Probar como cualquier A vs B, con el coste de las mismas
llamadas a los dos lados.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import semconv
from ._http import quote
from ._tracer import flush, get_config
from .decorators import get_current_trace_id, span, update_current_span
from .evals import EvalError, _endpoint, _request, fetch_dataset

logger = logging.getLogger("laplace")

#: Margen sobre la entrada original al estimar lo peor: otro tokenizador cuenta distinto,
#: y un tope que se cumple sólo si los dos modelos tokenizan igual no es un tope.
MARGEN_ENTRADA = 1.3

#: Salida máxima cuando la llamada original no fijó una: cuatro veces lo que respondió,
#: entre 256 y 4096 tokens. Es lo que se reserva del tope para cada llamada.
SALIDA_MINIMA = 256
SALIDA_MAXIMA = 4096

#: Por qué no se reenvía una llamada, dicho para una persona.
MOTIVOS = {
    "herramientas": "usa herramientas",
    "herramientas_sin_cadena": "sus vueltas con herramientas no son una sola conversación",
    "herramientas_sin_grabar": "la traza no grabó lo que hace falta para simular sus herramientas",
    "herramientas_formato": "sus herramientas van en un formato que no es el del modelo de destino",
    "mensajes_no_texto": "sus mensajes no son sólo texto (o se guardaron recortados)",
    "fallo": "falló",
    "sin_respuesta": "no dejó respuesta con la que comparar",
    "tirada_de_evaluacion": "es de una tirada de evaluación",
    "no_es_hoja": "de ella cuelgan otras llamadas",
    "sin_mensajes": "no guardó sus mensajes",
}


@dataclass
class Plan:
    """Lo que se va a hacer, antes de gastar nada. Es lo que se enseña al pedir permiso."""

    dataset_id: str
    model: str
    provider: str
    calls: int
    cases: int
    #: Lo que costaron esas llamadas con el modelo original (medido, no estimado).
    original_usd: float
    original_unknown: bool
    #: Lo que costarían con el nuevo si respondiera lo mismo que el original.
    estimated_usd: float
    #: Lo peor que pueden costar, con el margen y la salida máxima.
    worst_usd: float
    max_usd: float
    excluded: dict[str, int] = field(default_factory=dict)
    rate_verified: bool = True
    #: Ejecuciones que se repiten como conversación con herramientas (D-205).
    chains: int = 0

    def describe(self) -> str:
        lineas = [
            f"Reenviar {self.calls} llamadas de {self.cases} ejecuciones a {self.model} "
            f"({self.provider}), con tu clave.",
            f"  Con el modelo original costaron {_dinero(self.original_usd)}"
            + (" como mínimo (alguna sin tarifa)." if self.original_unknown else "."),
            f"  Con {self.model}, unos {_dinero(self.estimated_usd)} si responde lo mismo; "
            f"como mucho {_dinero(self.worst_usd)}.",
            f"  Tope: {_dinero(self.max_usd)}. Se para antes de pasarlo.",
        ]
        if self.chains:
            lineas.append(
                f"  {self.chains} usan herramientas: cada resultado es el que grabó la traza "
                "y ninguna herramienta se ejecuta. Si el modelo pide otra cosa, ese caso se "
                "corta y cuenta como fallo."
            )
        if self.worst_usd > self.max_usd:
            lineas.append(
                "  Lo peor pasa del tope: puede que no se reenvíen todas y la prueba "
                "quede con menos casos."
            )
        if not self.rate_verified:
            lineas.append(
                "  La tarifa de este modelo sale de la tabla comunitaria de LiteLLM y no "
                "está verificada: el tope se cumple sobre esa tarifa."
            )
        for motivo, n in sorted(self.excluded.items(), key=lambda kv: -kv[1]):
            lineas.append(f"  No se reenvían {n}: {MOTIVOS.get(motivo, motivo)}.")
        return "\n".join(lineas)


@dataclass
class ReplayResult:
    """Lo que se hizo. Las cifras de coste de verdad las mide Laplace con las trazas."""

    plan: Plan
    cancelled: bool = False
    original_run_id: str = ""
    replay_run_id: str = ""
    calls_done: int = 0
    #: Llamadas que no se hicieron porque habrían pasado del tope.
    calls_skipped_by_cap: int = 0
    cases_failed: int = 0
    #: De los fallidos, los cortados porque el modelo pidió algo que no se grabó.
    cases_cut: int = 0
    #: Lo gastado, calculado con la tarifa y los tokens que devolvió el proveedor. Es un
    #: techo: no descuenta la caché. El coste medido sale en la comparación.
    spent_usd: float = 0.0
    judged: int = 0
    passed: int = 0
    judge_cost_usd: float = 0.0
    judge_note: str = ""
    trace_ids: list[str] = field(default_factory=list)


def _dinero(valor: float) -> str:
    return f"{valor:.4f} $" if valor < 1 else f"{valor:.2f} $"


def provider_for(model: str) -> str:
    """El proveedor por el nombre del modelo. Se puede forzar con `provider=`."""
    return "anthropic" if model.lower().startswith("claude") else "openai"


def _salida_maxima(llamada: dict[str, Any]) -> int:
    fijada = llamada.get("params", {}).get("max_tokens")
    if isinstance(fijada, int) and fijada > 0:
        return fijada
    return max(SALIDA_MINIMA, min(SALIDA_MAXIMA, 4 * int(llamada.get("output_tokens") or 0)))


def _coste(tokens_in: float, tokens_out: float, tarifa: dict[str, Any]) -> float:
    return (
        tokens_in * float(tarifa["input_usd_per_mtok"])
        + tokens_out * float(tarifa["output_usd_per_mtok"])
    ) / 1_000_000


def _peor(llamada: dict[str, Any], tarifa: dict[str, Any]) -> float:
    return _coste(
        int(llamada.get("input_tokens") or 0) * MARGEN_ENTRADA, _salida_maxima(llamada), tarifa
    )


def _peor_cadena(cadena: dict[str, Any], tarifa: dict[str, Any]) -> float:
    """Lo peor de una conversación con herramientas (D-205).

    Con N peticiones grabadas hay como mucho N + 1 vueltas: cada vuelta con peticiones
    gasta al menos una grabada o corta. La entrada de cada vuelta cabe en la de la última
    vuelta original, con margen (lleva la de partida y todos los resultados), más lo que
    el modelo nuevo respondió en las anteriores, que es como mucho la salida máxima.
    """
    maximo = _salida_maxima(cadena)
    entrada = int(cadena.get("input_tokens") or 0) * MARGEN_ENTRADA
    vueltas = len(cadena.get("recorded") or []) + 1
    return sum(_coste(entrada + k * maximo, maximo, tarifa) for k in range(vueltas))


# ---------------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------------


def _cliente(provider: str) -> Any:
    """El cliente del proveedor, con la clave de su variable de entorno de siempre."""
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.Anthropic()
        import openai

        return openai.OpenAI()
    except ImportError as exc:
        raise EvalError(
            f"para reenviar a {provider} hace falta su SDK: pip install {provider}"
        ) from exc


def _muestreo(metodo: Any) -> tuple[str, ...]:
    """`temperature` y `top_p`, si el método del SDK instalado los acepta.

    No todos: `anthropic` 1.x quitó los dos de `messages.create`, y pasarlos hacía
    fallar cada llamada. Se mira la firma en lugar de fijar versiones.
    """
    try:
        aceptados = inspect.signature(metodo).parameters
    except (TypeError, ValueError):
        return ()
    return tuple(k for k in ("temperature", "top_p") if k in aceptados)


def _pedir(
    cliente: Any,
    provider: str,
    model: str,
    mensajes: list[dict[str, Any]],
    maximo: int,
    params: dict[str, Any],
    tools: list[Any] | None = None,
) -> tuple[Any, int, int]:
    """Una petición al modelo. Devuelve (respuesta, tokens de entrada, tokens de salida)."""
    metodo = cliente.messages.create if provider == "anthropic" else cliente.chat.completions.create
    extra = {k: params[k] for k in _muestreo(metodo) if k in params}
    if tools:
        extra["tools"] = tools
    if provider == "anthropic":
        sistema = [m["content"] for m in mensajes if m["role"] in ("system", "developer")]
        resto = [m for m in mensajes if m["role"] not in ("system", "developer")]
        if sistema:
            # Una sola, tal cual: con herramientas puede venir en bloques.
            extra["system"] = sistema[0] if len(sistema) == 1 else "\n\n".join(sistema)
        respuesta = cliente.messages.create(model=model, max_tokens=maximo, messages=resto, **extra)
        uso = respuesta.usage
        entrada = (
            (uso.input_tokens or 0)
            + (getattr(uso, "cache_read_input_tokens", 0) or 0)
            + (getattr(uso, "cache_creation_input_tokens", 0) or 0)
        )
        return respuesta, entrada, uso.output_tokens or 0
    respuesta = cliente.chat.completions.create(
        model=model, messages=mensajes, max_completion_tokens=maximo, **extra
    )
    uso = respuesta.usage
    return respuesta, (uso.prompt_tokens if uso else 0), (uso.completion_tokens if uso else 0)


def _texto(respuesta: Any, provider: str) -> str:
    if provider == "anthropic":
        return "".join(getattr(b, "text", "") for b in respuesta.content)
    return respuesta.choices[0].message.content or ""


def _llamar(
    cliente: Any, provider: str, model: str, llamada: dict[str, Any]
) -> tuple[str, int, int]:
    """Reenvía una llamada. Devuelve (texto, tokens de entrada, tokens de salida)."""
    respuesta, entrada, salida = _pedir(
        cliente, provider, model, llamada["messages"], _salida_maxima(llamada),
        llamada.get("params", {}),
    )
    return _texto(respuesta, provider), entrada, salida


# ---------------------------------------------------------------------------------
# Con herramientas (D-205)
# ---------------------------------------------------------------------------------


class _Corte(Exception):
    """El modelo nuevo pidió algo que la traza no grabó: no se sabe qué devolvería."""


def _clave(nombre: str, argumentos: Any) -> str:
    """Nombre y argumentos como JSON, sin mirar espacios ni el orden de las claves."""
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except ValueError:
            pass
    return json.dumps([nombre, argumentos], sort_keys=True, ensure_ascii=False)


def _casar(
    pedidas: list[tuple[str, Any]], grabadas: list[dict[str, Any]], desde: int
) -> list[dict[str, Any]] | None:
    """Las grabadas que responden a `pedidas`, en su orden; `None` si no están grabadas.

    Las peticiones tienen que seguir a las grabadas en orden, desde `desde`. Sólo se
    pueden reordenar las que el original también pidió a la vez: él mismo las trató como
    independientes. Las de vueltas distintas, no: una puede depender de lo que hizo otra.
    """
    tramo = grabadas[desde : desde + len(pedidas)]
    if len(tramo) < len(pedidas):
        return None
    claves = [_clave(g["name"], g["arguments"]) for g in tramo]
    if len({g["turn"] for g in tramo}) > 1:
        return tramo if [_clave(*p) for p in pedidas] == claves else None
    libres = list(range(len(tramo)))
    casadas = []
    for pedida in pedidas:
        clave = _clave(*pedida)
        i = next((j for j in libres if claves[j] == clave), None)
        if i is None:
            return None
        libres.remove(i)
        casadas.append(tramo[i])
    return casadas


def _pedidas(respuesta: Any, provider: str) -> list[tuple[str, str, Any]]:
    """Las peticiones de herramienta de la respuesta: (id, nombre, argumentos)."""
    if provider == "anthropic":
        return [
            (b.id, b.name, b.input)
            for b in respuesta.content
            if getattr(b, "type", None) == "tool_use"
        ]
    llamadas = respuesta.choices[0].message.tool_calls or []
    return [(t.id, t.function.name, t.function.arguments) for t in llamadas]


def _respuestas(
    respuesta: Any, provider: str, pedidas: list[tuple[str, str, Any]], casadas: list[dict]
) -> list[Any]:
    """La respuesta del modelo nuevo y, detrás, los resultados grabados con sus ids."""
    pares = list(zip([p[0] for p in pedidas], casadas, strict=True))
    if provider == "anthropic":
        return [
            {"role": "assistant", "content": respuesta.content},
            {"role": "user", "content": [{**g["result"], "tool_use_id": i} for i, g in pares]},
        ]
    return [
        respuesta.choices[0].message,
        *({**g["result"], "tool_call_id": i} for i, g in pares),
    ]


def _conversar(
    cliente: Any,
    provider: str,
    model: str,
    cadena: dict[str, Any],
    pagar: Callable[[int, int], None],
) -> tuple[str, int]:
    """Repite la conversación con el modelo nuevo. Devuelve (respuesta final, vueltas).

    Termina por construcción: cada vuelta con peticiones gasta al menos una grabada, o
    corta; así que son como mucho las grabadas más una.
    """
    mensajes = list(cadena["messages"])
    grabadas = cadena.get("recorded") or []
    maximo = _salida_maxima(cadena)
    usadas = vueltas = 0
    while True:
        respuesta, entrada, salida = _pedir(
            cliente, provider, model, mensajes, maximo, cadena.get("params", {}),
            cadena.get("tools"),
        )
        pagar(entrada, salida)
        vueltas += 1
        pedidas = _pedidas(respuesta, provider)
        if not pedidas:
            return _texto(respuesta, provider), vueltas
        casadas = _casar([(n, a) for _, n, a in pedidas], grabadas, usadas)
        if casadas is None:
            pedido = ", ".join(
                f"{n}({a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)})"
                for _, n, a in pedidas
            )
            raise _Corte(
                f"cortado en la vuelta {vueltas}: {model} pidió {pedido} y la traza no grabó "
                "qué devuelve en ese punto; no se inventa un resultado"
            )
        usadas += len(casadas)
        mensajes += _respuestas(respuesta, provider, pedidas, casadas)


# ---------------------------------------------------------------------------------
# El replay
# ---------------------------------------------------------------------------------


def replay_dataset(
    dataset: str,
    *,
    model: str,
    max_usd: float,
    project: str | None = None,
    endpoint: str | None = None,
    provider: str | None = None,
    variant: str | None = None,
    judge: bool = True,
    confirm: Callable[[Plan], bool] | None = None,
    client: Any = None,
    on_call: Callable[[int, int], None] | None = None,
) -> ReplayResult:
    """Reenvía las llamadas del paso de un conjunto a `model`, sin pasar de `max_usd`.

    `confirm` recibe el plan antes de gastar nada y decide; sin él, se sigue (desde la
    línea de órdenes siempre se pregunta, salvo con `--si`). `client` es para pasar un
    cliente de OpenAI o Anthropic ya configurado; sin él se crea uno con la clave del
    entorno.
    """
    if max_usd <= 0:
        raise EvalError("el tope tiene que ser mayor que cero")
    config = get_config()
    project_id = project or (getattr(config, "project", "") if config else "")
    if not project_id:
        raise EvalError("no sé de qué proyecto es esto: pasa project= o llama a laplace.init()")
    base = _endpoint(endpoint)
    dataset_id, _casos = fetch_dataset(dataset, project=project_id, endpoint=base)

    datos = _request(f"{base}/api/datasets/{quote(dataset_id)}/replay?model={quote(model)}")
    tarifa = datos.get("target")
    if not tarifa:
        raise EvalError(
            f"Laplace no tiene tarifa para «{model}»: sin ella no se puede respetar el tope, "
            "así que no se reenvía nada. Ponle tarifa en Ajustes o elige otro modelo."
        )
    proveedor = provider or provider_for(model)
    llamadas: list[dict[str, Any]] = datos.get("calls") or []
    excluidas = dict(datos.get("excluded") or {})
    # Los mensajes de herramientas van en el formato de quien los grabó (D-205).
    cadenas: list[dict[str, Any]] = []
    for cadena in datos.get("chains") or []:
        if cadena.get("system") == proveedor:
            cadenas.append(cadena)
        else:
            excluidas["herramientas_formato"] = excluidas.get(
                "herramientas_formato", 0
            ) + len(cadena["span_ids"])
    if not llamadas and not cadenas:
        motivos = ", ".join(f"{n} {MOTIVOS.get(m, m)}" for m, n in excluidas.items())
        raise EvalError(
            "no hay ninguna llamada de este conjunto que se pueda reenviar"
            + (f" ({motivos})" if motivos else "")
        )

    plan = Plan(
        dataset_id=dataset_id,
        model=model,
        provider=proveedor,
        calls=len(llamadas) + sum(len(c["span_ids"]) for c in cadenas),
        cases=len({ll["case_id"] for ll in [*llamadas, *cadenas]}),
        original_usd=sum(float(ll.get("cost_usd") or 0) for ll in [*llamadas, *cadenas]),
        original_unknown=any(ll.get("cost_unknown") for ll in [*llamadas, *cadenas]),
        estimated_usd=sum(
            _coste(ll.get("input_tokens") or 0, ll.get("output_tokens") or 0, tarifa)
            for ll in llamadas
        )
        + sum(
            _coste(c.get("total_input_tokens") or 0, c.get("total_output_tokens") or 0, tarifa)
            for c in cadenas
        ),
        worst_usd=sum(_peor(ll, tarifa) for ll in llamadas)
        + sum(_peor_cadena(c, tarifa) for c in cadenas),
        max_usd=max_usd,
        excluded=excluidas,
        rate_verified=bool(tarifa.get("verified", True)),
        chains=len(cadenas),
    )
    resultado = ReplayResult(plan=plan)
    if confirm is not None and not confirm(plan):
        resultado.cancelled = True
        return resultado

    cliente = client if client is not None else _cliente(proveedor)
    nombre = variant or f"replay {model}"
    por_caso: dict[str, list[dict[str, Any]]] = {}
    for llamada in llamadas:
        por_caso.setdefault(llamada["case_id"], []).append(llamada)
    cadena_de = {c["case_id"]: c for c in cadenas}
    for case_id in cadena_de:
        por_caso.setdefault(case_id, [])

    originales: list[dict[str, Any]] = []
    reenviadas: list[dict[str, Any]] = []
    referencias: dict[str, str] = {}
    hechas = 0

    def pagar(tok_in: int, tok_out: int) -> None:
        nonlocal hechas
        resultado.spent_usd += _coste(tok_in, tok_out, tarifa)
        hechas += 1
        if on_call:
            on_call(hechas, plan.calls)

    parar = False
    for case_id, del_caso in por_caso.items():
        cadena = cadena_de.get(case_id)
        span_ids = [ll["span_id"] for ll in del_caso] + (cadena["span_ids"] if cadena else [])
        # Un caso se reenvía entero o no se reenvía: medio caso compararía menos
        # llamadas en un lado que en el otro.
        peor = sum(_peor(ll, tarifa) for ll in del_caso)
        if cadena:
            peor += _peor_cadena(cadena, tarifa)
        if parar or resultado.spent_usd + peor > max_usd:
            parar = True
            resultado.calls_skipped_by_cap += len(span_ids)
            continue
        fallado, error, trace_id, textos = False, "", "", []
        with span(
            f"eval:{nombre}",
            type=semconv.SPAN_TYPE_AGENT,
            input=[m for ll in del_caso for m in ll["messages"]]
            + (cadena["messages"] if cadena else []),
            tags=[semconv.EVAL_TAG, f"variant:{nombre}", "laplace-replay"],
            metadata={
                "eval.case_id": case_id,
                "eval.variant": nombre,
                "replay.model": model,
                "replay.spans": ",".join(span_ids),
            },
        ):
            trace_id = get_current_trace_id() or ""
            try:
                for llamada in del_caso:
                    texto, tok_in, tok_out = _llamar(cliente, proveedor, model, llamada)
                    pagar(tok_in, tok_out)
                    textos.append(texto)
                if cadena:
                    texto, vueltas = _conversar(cliente, proveedor, model, cadena, pagar)
                    textos.append(texto)
                    update_current_span(metadata={"replay.turns": vueltas})
                update_current_span(output="\n\n".join(textos))
            except _Corte as exc:
                fallado, error = True, str(exc)
                resultado.cases_cut += 1
                logger.warning("el caso %s se ha cortado: %s", case_id, error)
            except Exception as exc:  # noqa: BLE001 - un caso roto es un fallo, no un corte
                fallado, error = True, f"{type(exc).__name__}: {exc}"
                logger.warning("el caso %s ha fallado al reenviarlo: %s", case_id, error)
        originales.append(
            {
                "case_id": case_id,
                "trace_id": del_caso[0]["trace_id"] if del_caso else cadena["trace_id"],
                "span_ids": span_ids,
            }
        )
        reenviadas.append(
            {"case_id": case_id, "trace_id": trace_id, "failed": fallado, "error": error[:500]}
        )
        if trace_id and not fallado:
            referencias[trace_id] = "\n\n".join(
                [ll["output"] for ll in del_caso] + ([cadena["output"]] if cadena else [])
            )
    resultado.calls_done = hechas
    resultado.cases_failed = sum(1 for r in reenviadas if r["failed"])
    resultado.trace_ids = [r["trace_id"] for r in reenviadas if r["trace_id"]]
    if not reenviadas:
        return resultado

    # Las trazas tienen que estar escritas antes de registrar la tirada (como en
    # `run_dataset`): si no, la comparación buscaría costes que aún no existen.
    flush()
    modelo_original = ", ".join(
        sorted({ll["model"] for ll in [*llamadas, *cadenas] if ll.get("model")})
    )
    notas = (
        f"Replay contrafactual: {hechas} llamadas reenviadas a {model} con tope {_dinero(max_usd)}."
    )
    original = _request(
        f"{base}/api/runs",
        method="POST",
        payload={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "variant": f"original ({modelo_original})" if modelo_original else "original",
            "notes": notas + " Esta tirada no gastó nada: son las llamadas reales.",
            "items": originales,
        },
    )
    nueva = _request(
        f"{base}/api/runs",
        method="POST",
        payload={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "variant": nombre,
            "notes": notas,
            "items": reenviadas,
        },
    )
    resultado.original_run_id = original["id"]
    resultado.replay_run_id = nueva["id"]

    if judge:
        _juzgar(base, project_id, resultado, referencias)
    return resultado


def _juzgar(
    base: str, project_id: str, resultado: ReplayResult, referencias: dict[str, str]
) -> None:
    """El juez de Laplace, si está encendido, contra la respuesta de cada llamada."""
    estado = _request(f"{base}/api/judge")
    if not estado.get("enabled"):
        resultado.judge_note = (
            "El juez está apagado: la comparación dirá el coste y no si responde igual. "
            + (estado.get("detail") or "")
        ).strip()
        return
    try:
        juicio = _request(
            f"{base}/api/judge",
            method="POST",
            payload={
                "project_id": project_id,
                "run_id": resultado.replay_run_id,
                "expected": referencias,
            },
        )
    except EvalError as exc:
        resultado.judge_note = f"El juez no ha podido juzgar: {exc}"
        return
    anotaciones = juicio.get("annotations") or []
    resultado.judged = len(anotaciones)
    resultado.passed = sum(1 for a in anotaciones if a.get("verdict") == "pass")
    resultado.judge_cost_usd = float(juicio.get("cost_usd") or 0)
