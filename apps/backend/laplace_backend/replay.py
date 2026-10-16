"""Replay contrafactual: qué llamadas reales de un conjunto se pueden reenviar (D-167).

El paso «probar» del ciclo pedía escribir una función que corriese el agente sobre cada
caso. Para la pregunta más común —«¿el modelo barato respondería igual en este paso?»—
no hace falta el agente: basta con reenviar **las mismas llamadas** que hizo ese paso,
con los mismos mensajes, a otro modelo, y comparar las respuestas.

Aquí sólo se decide qué se puede reenviar y se entrega. **El reenvío lo hace el SDK**
(`laplace replay`), en la máquina del usuario y con sus claves: Laplace no guarda claves
de proveedor ni gasta dinero de nadie (D-086).

Se reenvía sólo lo que no puede tener efectos ni cambiar de sentido al reenviarse:

* una llamada **hoja** del paso (nada cuelga de ella);
* **sin herramientas**: ni declaradas, ni pedidas en la respuesta, ni resultados de
  herramienta entre los mensajes. Las que sí las usan van aparte, como una
  **conversación** entera (D-205): el SDK la repite con el modelo nuevo y le da como
  resultado de cada herramienta el que quedó grabado; la de verdad no se ejecuta;
* con **todos sus mensajes en texto** y con rol: un mensaje recortado por el tamaño, una
  imagen o un bloque que no es texto no se puede reenviar tal cual a otro proveedor;
* que salió **bien** y dejó una **respuesta** con la que comparar;
* que es **tráfico real**, no de una tirada de evaluación.

Lo que no pasa el filtro no desaparece: se cuenta por motivo, para que «se reenvían 12
de 40 llamadas» diga por qué no las otras 28.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from laplace.schema import Span
from laplace.semconv import EVAL_TAG
from pydantic import BaseModel, Field

from .pricing import get_price_table

#: Los roles que se pueden reenviar. `tool` no: es el resultado de una herramienta.
ROLES = frozenset({"system", "developer", "user", "assistant"})

#: Los parámetros de la llamada original que se conservan al reenviarla. El resto
#: (`tools`, `response_format`, `logprobs`…) o cambia de sentido entre proveedores o
#: saca la llamada del filtro.
PARAMETROS = ("temperature", "top_p", "max_tokens")

#: Motivos de exclusión. La clave es estable (la lee el SDK); el texto se escribe allí.
MOTIVOS = (
    "no_es_del_paso",
    "tirada_de_evaluacion",
    "fallo",
    "no_es_hoja",
    "herramientas_sin_cadena",
    "herramientas_sin_grabar",
    "herramientas_formato",
    "sin_mensajes",
    "mensajes_no_texto",
    "sin_respuesta",
)


class Tarifa(BaseModel):
    """Lo que cobra el modelo de destino por millón de tokens, al metro estándar.

    Es la que usa el SDK para respetar el tope de gasto antes de cada llamada. Sin
    tarifa no hay tope que se pueda cumplir, y el SDK no reenvía nada.
    """

    model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    verified: bool = True


class Llamada(BaseModel):
    """Una llamada real que se puede reenviar, con lo que costó la primera vez."""

    case_id: str
    trace_id: str
    span_id: str
    system: str = ""
    model: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    #: Lo que respondió el modelo original: la referencia para el juez.
    output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_unknown: bool = False


class Grabada(BaseModel):
    """Una petición de herramienta del original, con lo que le devolvió la herramienta."""

    id: str
    name: str
    #: Tal cual: texto JSON en OpenAI, objeto en Anthropic. El SDK compara los dos.
    arguments: Any = None
    #: El mensaje `tool` de OpenAI o el bloque `tool_result` de Anthropic, en crudo.
    result: dict[str, Any] = Field(default_factory=dict)
    #: En qué vuelta la pidió el original: lo pedido a la vez se puede reordenar.
    turn: int = 0


class Cadena(BaseModel):
    """Una conversación con herramientas de un caso, lista para repetirla (D-205)."""

    case_id: str
    trace_id: str
    #: Todas sus vueltas: la tirada original cuesta el bucle entero.
    span_ids: list[str] = Field(default_factory=list)
    #: El proveedor que la grabó; sus mensajes van en su formato.
    system: str = ""
    model: str = ""
    #: El punto de partida: la entrada de la primera vuelta, en crudo.
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    recorded: list[Grabada] = Field(default_factory=list)
    #: La respuesta final del original: la referencia para el juez.
    output: str = ""
    #: De la última vuelta, que contiene todas las anteriores: la base de lo peor.
    input_tokens: int = 0
    #: La mayor salida de una vuelta: con ella se fija la salida máxima.
    output_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: float = 0.0
    cost_unknown: bool = False


class Reenviables(BaseModel):
    dataset_id: str
    step_key: str = ""
    calls: list[Llamada] = Field(default_factory=list)
    chains: list[Cadena] = Field(default_factory=list)
    #: Llamadas de modelo del conjunto que no se reenvían, por motivo.
    excluded: dict[str, int] = Field(default_factory=dict)
    target: Tarifa | None = None


def _texto(contenido: Any) -> str | None:
    """El texto de un contenido, si es sólo texto; `None` si lleva otra cosa."""
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list) and contenido:
        textos = []
        for bloque in contenido:
            if not isinstance(bloque, dict) or bloque.get("type") not in ("text", "input_text",
                                                                          "output_text"):
                return None
            textos.append(str(bloque.get("text", "")))
        return "".join(textos)
    return None


def _mensajes(span: Span) -> tuple[list[dict[str, str]] | None, str]:
    """Los mensajes en texto, o `None` y el motivo por el que no se pueden reenviar."""
    crudos = span.llm.input_messages if span.llm else []
    if not crudos:
        return None, "sin_mensajes"
    mensajes = []
    for mensaje in crudos:
        rol = str(mensaje.get("role", "")).lower()
        if rol == "tool" or mensaje.get("tool_calls") or mensaje.get("tool_call_id"):
            return None, "herramientas"
        texto = _texto(mensaje.get("content"))
        if rol not in ROLES or texto is None:
            return None, "mensajes_no_texto"
        mensajes.append({"role": rol, "content": texto})
    return mensajes, ""


def _salida(span: Span) -> tuple[str | None, str]:
    salidas = span.llm.output_messages if span.llm else []
    partes = []
    for mensaje in salidas:
        if mensaje.get("tool_calls"):
            return None, "herramientas"
        contenido = mensaje.get("content")
        if isinstance(contenido, list) and any(
            isinstance(b, dict) and b.get("type") in ("tool_use", "function_call")
            for b in contenido
        ):
            return None, "herramientas"
        texto = _texto(contenido)
        if texto:
            partes.append(texto)
    if not partes:
        return None, "sin_respuesta"
    return "".join(partes), ""


def motivo(span: Span, con_hijos: set[str], step_key: str) -> str:
    """Por qué no se reenvía esta llamada de modelo; cadena vacía si se reenvía."""
    if step_key and (span.step_key or span.name) != step_key:
        return "no_es_del_paso"
    if EVAL_TAG in span.tags:
        return "tirada_de_evaluacion"
    if span.status == "error":
        return "fallo"
    if span.span_id in con_hijos:
        return "no_es_hoja"
    if span.attributes.get("laplace.request.tools"):
        return "herramientas"
    mensajes, porque = _mensajes(span)
    if mensajes is None:
        return porque
    salida, porque = _salida(span)
    if salida is None:
        return porque
    return ""


def llamada(span: Span, case_id: str) -> Llamada:
    """La llamada lista para reenviar. Sólo para spans con `motivo(...) == ""`."""
    assert span.llm is not None
    mensajes, _ = _mensajes(span)
    salida, _ = _salida(span)
    params = {k: span.llm.params[k] for k in PARAMETROS if span.llm.params.get(k) is not None}
    return Llamada(
        case_id=case_id,
        trace_id=span.trace_id,
        span_id=span.span_id,
        system=span.llm.system or "",
        model=span.llm.response_model or span.llm.request_model or "",
        messages=mensajes or [],
        params=params,
        output=salida or "",
        input_tokens=span.llm.usage.input_tokens,
        output_tokens=span.llm.usage.output_tokens,
        cost_usd=span.llm.cost.total_usd,
        cost_unknown=span.llm.cost.unknown,
    )


def _herramientas(span: Span) -> list[Any] | None:
    """Las herramientas declaradas; `None` si no se pueden leer (recortadas por tamaño)."""
    crudo = span.attributes.get("laplace.request.tools")
    if not crudo:
        return []
    try:
        valor = json.loads(crudo) if isinstance(crudo, str) else crudo
    except ValueError:
        return None
    return valor if isinstance(valor, list) else None


def _peticiones(mensaje: dict[str, Any], system: str) -> list[tuple[str, str, Any]]:
    """Las peticiones de herramienta de un mensaje del modelo: (id, nombre, argumentos)."""
    if mensaje.get("role") != "assistant":
        return []
    if system == "anthropic":
        bloques = mensaje.get("content")
        return [
            (str(b.get("id", "")), str(b.get("name", "")), b.get("input"))
            for b in (bloques if isinstance(bloques, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
    return [
        (str(t.get("id", "")), str(f.get("name", "")), f.get("arguments"))
        for t in mensaje.get("tool_calls") or []
        for f in [t.get("function") or {}]
    ]


def _resultados(mensaje: dict[str, Any], system: str) -> list[tuple[str, dict[str, Any]]]:
    """Los resultados de herramienta de un mensaje, si no lleva otra cosa: (id, crudo)."""
    if system == "anthropic":
        bloques = mensaje.get("content")
        if mensaje.get("role") != "user" or not isinstance(bloques, list):
            return []
        if not all(isinstance(b, dict) and b.get("type") == "tool_result" for b in bloques):
            return []
        return [(str(b.get("tool_use_id", "")), b) for b in bloques]
    if mensaje.get("role") != "tool":
        return []
    return [(str(mensaje.get("tool_call_id", "")), mensaje)]


def cadena(spans: list[Span], case_id: str) -> tuple[Cadena | None, str]:
    """Las llamadas con herramientas de un caso como una sola conversación, o el motivo
    por el que no lo son. Ordenadas por hora, cada vuelta continúa la anterior, todas
    declaran las mismas herramientas, y lo que se añade entre la primera y la última son
    sólo peticiones de herramienta y sus resultados (D-205)."""
    vueltas = sorted(spans, key=lambda s: s.start_time)
    llms = [s.llm for s in vueltas if s.llm is not None]
    system = str(llms[0].system or "")
    entradas = [llm.input_messages for llm in llms]
    if system not in ("openai", "anthropic") or any(
        str(m.get("role", "")).lower() not in ROLES | {"tool"} for e in entradas for m in e
    ):
        return None, "herramientas_formato"
    herramientas = [_herramientas(s) for s in vueltas]
    if any(h is None for h in herramientas):
        return None, "herramientas_sin_grabar"
    if any(h != herramientas[0] for h in herramientas) or any(
        llm.system != system for llm in llms
    ):
        return None, "herramientas_sin_cadena"
    for antes, despues in zip(entradas, entradas[1:], strict=False):
        if len(despues) <= len(antes) or despues[: len(antes)] != antes:
            return None, "herramientas_sin_cadena"

    # Lo que pasó entre la primera vuelta y la última: peticiones y sus resultados.
    pedidas: list[Grabada] = []
    devueltos: dict[str, dict[str, Any]] = {}
    turno = 0
    for mensaje in entradas[-1][len(entradas[0]) :]:
        peticiones = _peticiones(mensaje, system)
        resultados = _resultados(mensaje, system)
        if peticiones:
            pedidas += [Grabada(id=i, name=n, arguments=a, turn=turno) for i, n, a in peticiones]
            turno += 1
        elif resultados:
            devueltos.update(resultados)
        else:
            return None, "herramientas_sin_cadena"
    if sorted(g.id for g in pedidas) != sorted(devueltos):
        return None, "herramientas_sin_grabar"
    for grabada in pedidas:
        grabada.result = devueltos[grabada.id]

    salida, _ = _salida(vueltas[-1])
    if salida is None:
        return None, "sin_respuesta"
    ultima = llms[-1]
    return Cadena(
        case_id=case_id,
        trace_id=vueltas[0].trace_id,
        span_ids=[s.span_id for s in vueltas],
        system=system,
        model=ultima.response_model or ultima.request_model or "",
        messages=entradas[0],
        tools=herramientas[0] or [],
        params={k: ultima.params[k] for k in PARAMETROS if ultima.params.get(k) is not None},
        recorded=pedidas,
        output=salida,
        input_tokens=ultima.usage.input_tokens,
        output_tokens=max(llm.usage.output_tokens for llm in llms),
        total_input_tokens=sum(llm.usage.input_tokens for llm in llms),
        total_output_tokens=sum(llm.usage.output_tokens for llm in llms),
        cost_usd=sum(llm.cost.total_usd for llm in llms),
        cost_unknown=any(llm.cost.unknown for llm in llms),
    ), ""


def reenviables(
    dataset_id: str, step_key: str, casos: list[tuple[str, list[Span]]]
) -> Reenviables:
    """Las llamadas reenviables de un conjunto: `casos` es (case_id, spans de su traza)."""
    llamadas: list[Llamada] = []
    cadenas: list[Cadena] = []
    fuera: Counter[str] = Counter()
    for case_id, spans in casos:
        con_hijos = {s.parent_span_id for s in spans if s.parent_span_id}
        con_herramientas: list[Span] = []
        for span in spans:
            if span.type != "llm" or span.llm is None:
                continue
            porque = motivo(span, con_hijos, step_key)
            if porque == "no_es_del_paso":
                continue  # otro paso de la misma ejecución: no es una exclusión
            if porque == "herramientas":
                con_herramientas.append(span)
            elif porque:
                fuera[porque] += 1
            else:
                llamadas.append(llamada(span, case_id))
        if con_herramientas:
            conversacion, porque = cadena(con_herramientas, case_id)
            if conversacion is None:
                fuera[porque] += len(con_herramientas)
            else:
                cadenas.append(conversacion)
    return Reenviables(
        dataset_id=dataset_id,
        step_key=step_key,
        calls=llamadas,
        chains=cadenas,
        excluded=dict(fuera),
    )


def tarifa(model: str) -> Tarifa | None:
    """La tarifa estándar del modelo de destino, o `None` si no se conoce."""
    precio = get_price_table().lookup(model)
    if precio is None:
        return None
    return Tarifa(
        model=precio.model,
        input_usd_per_mtok=precio.input,
        output_usd_per_mtok=precio.output,
        verified=precio.verified,
    )
