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
  herramienta entre los mensajes. Una llamada con herramientas no se entiende sin
  ejecutarlas, y ejecutarlas es correr el agente;
* con **todos sus mensajes en texto** y con rol: un mensaje recortado por el tamaño, una
  imagen o un bloque que no es texto no se puede reenviar tal cual a otro proveedor;
* que salió **bien** y dejó una **respuesta** con la que comparar;
* que es **tráfico real**, no de una tirada de evaluación.

Lo que no pasa el filtro no desaparece: se cuenta por motivo, para que «se reenvían 12
de 40 llamadas» diga por qué no las otras 28.
"""

from __future__ import annotations

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
    "herramientas",
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


class Reenviables(BaseModel):
    dataset_id: str
    step_key: str = ""
    calls: list[Llamada] = Field(default_factory=list)
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


def reenviables(
    dataset_id: str, step_key: str, casos: list[tuple[str, list[Span]]]
) -> Reenviables:
    """Las llamadas reenviables de un conjunto: `casos` es (case_id, spans de su traza)."""
    llamadas: list[Llamada] = []
    fuera: Counter[str] = Counter()
    for case_id, spans in casos:
        con_hijos = {s.parent_span_id for s in spans if s.parent_span_id}
        for span in spans:
            if span.type != "llm" or span.llm is None:
                continue
            porque = motivo(span, con_hijos, step_key)
            if porque == "no_es_del_paso":
                continue  # otro paso de la misma ejecución: no es una exclusión
            if porque:
                fuera[porque] += 1
            else:
                llamadas.append(llamada(span, case_id))
    return Reenviables(
        dataset_id=dataset_id, step_key=step_key, calls=llamadas, excluded=dict(fuera)
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
