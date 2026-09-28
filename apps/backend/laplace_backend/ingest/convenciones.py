"""Las trazas que ya emiten otros instrumentadores, traducidas a las nuestras.

Laplace recibe OTLP en el puerto estándar, pero hasta aquí sólo entendía los atributos
de su propio SDK y los de las convenciones GenAI de OpenTelemetry. Un agente escrito con
LangGraph, CrewAI, LlamaIndex, el Agents SDK de OpenAI o el AI SDK de Vercel ya suele ir
instrumentado con una de estas dos familias, y sus trazas llegaban como spans `chain` sin
modelo, sin tokens y sin coste: cobertura cero, y el producto callado (D-136).

* **OpenInference** (Arize Phoenix y compañía): `openinference.span.kind`,
  `llm.model_name`, `llm.token_count.*`, `llm.input_messages.{i}.message.*`,
  `input.value`, `output.value`, `tool.name`, `session.id`, `user.id`.
* **OpenLLMetry** (Traceloop): `traceloop.span.kind`, `traceloop.entity.input/output`,
  `gen_ai.prompt.{i}.*`, `gen_ai.completion.{i}.*`, `gen_ai.usage.prompt_tokens` y
  `gen_ai.usage.completion_tokens` (los nombres anteriores de GenAI).
* **Las convenciones GenAI actuales** de OpenTelemetry, que ya emiten el AI SDK de Vercel
  y OpenLLMetry-js: los mensajes en `parts` en lugar de `content`, y las instrucciones
  aparte, en `gen_ai.system_instructions` (D-165).

La regla es una sola: **se rellena lo que falta, nunca se pisa lo que ya viene**. Si un
span trae a la vez nuestros atributos y los de otra familia, mandan los nuestros. Y lo
que no se puede traducir con seguridad no se traduce: se queda en `attributes`, en crudo,
que es donde se enseña en modo avanzado.
"""

from __future__ import annotations

import json
import re
from typing import Any

from laplace import semconv

#: `openinference.span.kind` → tipo del contrato. Los que no tienen equivalente (un
#: guardarraíl, un evaluador) son pasos de la cadena: no se inventa un tipo nuevo.
_TIPOS_OPENINFERENCE = {
    "LLM": semconv.SPAN_TYPE_LLM,
    "EMBEDDING": semconv.SPAN_TYPE_LLM,
    "CHAIN": semconv.SPAN_TYPE_CHAIN,
    "TOOL": semconv.SPAN_TYPE_TOOL,
    "AGENT": semconv.SPAN_TYPE_AGENT,
    "RETRIEVER": semconv.SPAN_TYPE_RETRIEVAL,
    "RERANKER": semconv.SPAN_TYPE_RETRIEVAL,
    "GUARDRAIL": semconv.SPAN_TYPE_CHAIN,
    "EVALUATOR": semconv.SPAN_TYPE_CHAIN,
    "PROMPT": semconv.SPAN_TYPE_CHAIN,
}

_TIPOS_TRACELOOP = {
    "workflow": semconv.SPAN_TYPE_CHAIN,
    "task": semconv.SPAN_TYPE_CHAIN,
    "agent": semconv.SPAN_TYPE_AGENT,
    "tool": semconv.SPAN_TYPE_TOOL,
}

_OPERACION_TRACELOOP = {
    "chat": semconv.OPERATION_CHAT,
    "completion": semconv.OPERATION_TEXT_COMPLETION,
    "embedding": semconv.OPERATION_EMBEDDINGS,
}


#: Lo que se traduce y no hace falta guardar otra vez en crudo: los mensajes y las
#: entradas y salidas aplanadas ocupan tanto como todo lo demás junto.
TRADUCIDOS = (
    "llm.input_messages.",
    "llm.output_messages.",
    "gen_ai.prompt.",
    "gen_ai.completion.",
    "retrieval.documents.",
    "input.value",
    "output.value",
    "traceloop.entity.input",
    "traceloop.entity.output",
    "gen_ai.system_instructions",
)


def normalizar(attrs: dict[str, Any]) -> dict[str, Any]:
    """Los atributos del span con los nuestros rellenos desde la familia que traiga."""
    if not any(k.startswith(("openinference.", "llm.", "traceloop.", "gen_ai.prompt.",
                             "gen_ai.completion.", "gen_ai.usage.prompt_tokens",
                             "gen_ai.usage.cache_", "gen_ai.system_instructions",
                             "input.value", "tool.name", "session.id"))
               for k in attrs) and not _en_parts(attrs):
        return attrs
    salida = dict(attrs)
    _openinference(attrs, salida)
    _openllmetry(attrs, salida)
    _genai_actual(salida)
    _tokens_de_cache_coherentes(salida)
    return salida


def _poner(salida: dict[str, Any], clave: str, valor: Any) -> None:
    """Rellena si falta. Nunca pisa: lo que ya venía en nuestro formato manda."""
    if valor is None or valor == "" or valor == []:
        return
    if salida.get(clave) in (None, "", []):
        salida[clave] = valor


# ---------------------------------------------------------------------------------
# OpenInference
# ---------------------------------------------------------------------------------


def _openinference(attrs: dict[str, Any], salida: dict[str, Any]) -> None:
    tipo = _TIPOS_OPENINFERENCE.get(str(attrs.get("openinference.span.kind", "")).upper())
    if tipo:
        _poner(salida, semconv.LAPLACE_SPAN_TYPE, tipo)
        if str(attrs.get("openinference.span.kind", "")).upper() == "EMBEDDING":
            _poner(salida, semconv.GEN_AI_OPERATION_NAME, semconv.OPERATION_EMBEDDINGS)

    modelo = attrs.get("llm.model_name") or attrs.get("embedding.model_name")
    _poner(salida, semconv.GEN_AI_REQUEST_MODEL, modelo)
    _poner(salida, semconv.GEN_AI_RESPONSE_MODEL, attrs.get("llm.response.model_name"))
    if attrs.get("llm.finish_reason"):
        _poner(salida, semconv.GEN_AI_RESPONSE_FINISH_REASONS, [str(attrs["llm.finish_reason"])])
    _poner(salida, semconv.GEN_AI_SYSTEM, attrs.get("llm.provider") or attrs.get("llm.system"))

    _poner(salida, semconv.GEN_AI_USAGE_INPUT_TOKENS, attrs.get("llm.token_count.prompt"))
    _poner(salida, semconv.GEN_AI_USAGE_OUTPUT_TOKENS, attrs.get("llm.token_count.completion"))
    _poner(
        salida,
        semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS,
        attrs.get("llm.token_count.prompt_details.cache_read"),
    )
    _poner(
        salida,
        semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS,
        attrs.get("llm.token_count.prompt_details.cache_write"),
    )
    _poner(
        salida,
        semconv.LAPLACE_USAGE_REASONING_TOKENS,
        attrs.get("llm.token_count.completion_details.reasoning"),
    )

    _poner(salida, semconv.GEN_AI_INPUT_MESSAGES, _mensajes(attrs, "llm.input_messages"))
    _poner(salida, semconv.GEN_AI_OUTPUT_MESSAGES, _mensajes(attrs, "llm.output_messages"))

    parametros = _json(attrs.get("llm.invocation_parameters"))
    if isinstance(parametros, dict):
        for nombre, valor in parametros.items():
            if isinstance(valor, (str, int, float, bool)):
                _poner(salida, f"gen_ai.request.{nombre}", valor)
        # OpenInference-js con Anthropic deja el `system` aquí y no entre los mensajes
        # (D-165): sin él, el paso perdía la mitad de su identidad.
        if str(salida.get(semconv.LAPLACE_SPAN_TYPE)) == semconv.SPAN_TYPE_LLM:
            _unir_sistema(salida, _texto_de_bloques(parametros.get("system")))

    if salida.get(semconv.LAPLACE_SPAN_TYPE) == semconv.SPAN_TYPE_LLM:
        _salida_de_generaciones(attrs, salida)

    herramientas = [
        _json(v)
        for k, v in sorted(attrs.items())
        if re.fullmatch(r"llm\.tools\.\d+\.tool\.json_schema", k)
    ]
    if herramientas:
        _poner(salida, "laplace.request.tools", json.dumps(herramientas, ensure_ascii=False))

    entrada, salida_valor = attrs.get("input.value"), attrs.get("output.value")
    tipo_final = salida.get(semconv.LAPLACE_SPAN_TYPE)
    if tipo_final == semconv.SPAN_TYPE_TOOL:
        _poner(salida, semconv.GEN_AI_TOOL_NAME, attrs.get("tool.name"))
        _poner(salida, semconv.GEN_AI_TOOL_DESCRIPTION, attrs.get("tool.description"))
        _poner(salida, semconv.LAPLACE_TOOL_ARGUMENTS, entrada or attrs.get("tool.parameters"))
        _poner(salida, semconv.LAPLACE_TOOL_OUTPUT, salida_valor)
    elif tipo_final == semconv.SPAN_TYPE_RETRIEVAL:
        _poner(salida, semconv.LAPLACE_RETRIEVAL_QUERY, entrada)
        documentos = _documentos(attrs)
        if documentos:
            _poner(
                salida,
                semconv.LAPLACE_RETRIEVAL_DOCUMENTS,
                json.dumps(documentos, ensure_ascii=False),
            )
    elif tipo_final != semconv.SPAN_TYPE_LLM:
        _poner(salida, semconv.LAPLACE_INPUT, entrada)
        _poner(salida, semconv.LAPLACE_OUTPUT, salida_valor)

    _poner(salida, semconv.LAPLACE_SESSION_ID, attrs.get("session.id"))
    _poner(salida, semconv.LAPLACE_USER_ID, attrs.get("user.id"))
    etiquetas = attrs.get("tag.tags")
    if isinstance(etiquetas, (list, tuple)) and etiquetas:
        _poner(salida, semconv.LAPLACE_TAGS, json.dumps([str(t) for t in etiquetas]))
    metadatos = _json(attrs.get("metadata"))
    if isinstance(metadatos, dict) and metadatos:
        _poner(salida, semconv.LAPLACE_METADATA, json.dumps(metadatos, ensure_ascii=False))
        # Con LangGraph, el nodo es el «desde dónde» del paso: sin él, dos nodos con el
        # mismo prompt de sistema se juntaban en uno (D-141).
        if tipo_final == semconv.SPAN_TYPE_LLM:
            _poner(salida, semconv.LAPLACE_STEP_PARENT, metadatos.get("langgraph_node"))


def _mensajes(attrs: dict[str, Any], prefijo: str) -> str | None:
    """`llm.input_messages.{i}.message.role/content` aplanados → lista de mensajes.

    El contenido puede venir entero en `message.content` o troceado en
    `message.contents.{j}.message_content.text`; se juntan los trozos de texto.
    """
    patron = re.compile(rf"{re.escape(prefijo)}\.(\d+)\.message\.(.+)")
    por_indice: dict[int, dict[str, Any]] = {}
    for clave, valor in attrs.items():
        encontrado = patron.fullmatch(clave)
        if not encontrado:
            continue
        indice, campo = int(encontrado.group(1)), encontrado.group(2)
        mensaje = por_indice.setdefault(indice, {})
        if campo == "role":
            mensaje["role"] = str(valor)
        elif campo == "content":
            mensaje["content"] = valor
        elif campo == "name":
            mensaje["name"] = str(valor)
        elif re.fullmatch(r"contents\.\d+\.message_content\.text", campo):
            mensaje.setdefault("_trozos", []).append((campo, str(valor)))
        elif campo.startswith("tool_calls."):
            mensaje.setdefault("_llamadas", []).append((campo, valor))
    if not por_indice:
        return None
    lista = []
    for indice in sorted(por_indice):
        mensaje = por_indice[indice]
        trozos = mensaje.pop("_trozos", None)
        if "content" not in mensaje and trozos:
            mensaje["content"] = "".join(t for _, t in sorted(trozos))
        llamadas = mensaje.pop("_llamadas", None)
        if llamadas:
            mensaje["tool_calls"] = _llamadas_a_herramientas(llamadas)
        lista.append(mensaje)
    return json.dumps(lista, ensure_ascii=False)


def _llamadas_a_herramientas(pares: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    por_indice: dict[int, dict[str, Any]] = {}
    for campo, valor in pares:
        encontrado = re.fullmatch(r"tool_calls\.(\d+)\.tool_call\.(.+)", campo)
        if not encontrado:
            continue
        llamada = por_indice.setdefault(int(encontrado.group(1)), {})
        llamada[encontrado.group(2)] = valor
    return [por_indice[i] for i in sorted(por_indice)]


def _documentos(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    patron = re.compile(r"retrieval\.documents\.(\d+)\.document\.(.+)")
    por_indice: dict[int, dict[str, Any]] = {}
    for clave, valor in attrs.items():
        encontrado = patron.fullmatch(clave)
        if encontrado:
            por_indice.setdefault(int(encontrado.group(1)), {})[encontrado.group(2)] = valor
    return [por_indice[i] for i in sorted(por_indice)]


# ---------------------------------------------------------------------------------
# OpenLLMetry
# ---------------------------------------------------------------------------------


def _openllmetry(attrs: dict[str, Any], salida: dict[str, Any]) -> None:
    tipo = _TIPOS_TRACELOOP.get(str(attrs.get("traceloop.span.kind", "")).lower())
    if tipo:
        _poner(salida, semconv.LAPLACE_SPAN_TYPE, tipo)
    if tipo == semconv.SPAN_TYPE_TOOL:
        _poner(salida, semconv.GEN_AI_TOOL_NAME, attrs.get("traceloop.entity.name"))
        _poner(salida, semconv.LAPLACE_TOOL_ARGUMENTS, attrs.get("traceloop.entity.input"))
        _poner(salida, semconv.LAPLACE_TOOL_OUTPUT, attrs.get("traceloop.entity.output"))
    elif tipo:
        _poner(salida, semconv.LAPLACE_INPUT, attrs.get("traceloop.entity.input"))
        _poner(salida, semconv.LAPLACE_OUTPUT, attrs.get("traceloop.entity.output"))

    operacion = _OPERACION_TRACELOOP.get(str(attrs.get("llm.request.type", "")).lower())
    _poner(salida, semconv.GEN_AI_OPERATION_NAME, operacion)

    _poner(salida, semconv.GEN_AI_USAGE_INPUT_TOKENS, attrs.get("gen_ai.usage.prompt_tokens"))
    _poner(
        salida, semconv.GEN_AI_USAGE_OUTPUT_TOKENS, attrs.get("gen_ai.usage.completion_tokens")
    )
    # Con guion bajo, los nombres de OpenLLMetry; con punto, los de las convenciones
    # GenAI de OpenTelemetry más recientes.
    _poner(
        salida,
        semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS,
        attrs.get("gen_ai.usage.cache_read_input_tokens")
        or attrs.get("gen_ai.usage.cache_read.input_tokens"),
    )
    _poner(
        salida,
        semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS,
        attrs.get("gen_ai.usage.cache_creation_input_tokens")
        or attrs.get("gen_ai.usage.cache_creation.input_tokens"),
    )
    _poner(salida, semconv.GEN_AI_INPUT_MESSAGES, _indexados(attrs, "gen_ai.prompt"))
    _poner(salida, semconv.GEN_AI_OUTPUT_MESSAGES, _indexados(attrs, "gen_ai.completion"))

    razones = sorted(
        (k, str(v)) for k, v in attrs.items()
        if re.fullmatch(r"gen_ai\.completion\.\d+\.finish_reason", k)
    )
    if razones:
        _poner(salida, semconv.GEN_AI_RESPONSE_FINISH_REASONS, [v for _, v in razones])

    funciones = sorted(
        (k, v) for k, v in attrs.items() if re.fullmatch(r"llm\.request\.functions\.\d+\.name", k)
    )
    if funciones:
        _poner(
            salida,
            "laplace.request.tools",
            json.dumps([{"name": str(v)} for _, v in funciones], ensure_ascii=False),
        )

    for clave, valor in attrs.items():
        if clave.startswith("traceloop.association.properties."):
            nombre = clave.rsplit(".", 1)[-1]
            if nombre in ("session_id", "conversation_id", "thread_id"):
                _poner(salida, semconv.LAPLACE_SESSION_ID, valor)
            elif nombre in ("user_id", "customer_id"):
                _poner(salida, semconv.LAPLACE_USER_ID, valor)


def _indexados(attrs: dict[str, Any], prefijo: str) -> str | None:
    """`gen_ai.prompt.{i}.role/content` → lista de mensajes."""
    patron = re.compile(rf"{re.escape(prefijo)}\.(\d+)\.(role|content|name)")
    por_indice: dict[int, dict[str, Any]] = {}
    for clave, valor in attrs.items():
        encontrado = patron.fullmatch(clave)
        if encontrado:
            por_indice.setdefault(int(encontrado.group(1)), {})[encontrado.group(2)] = valor
    if not por_indice:
        return None
    return json.dumps([por_indice[i] for i in sorted(por_indice)], ensure_ascii=False)


# ---------------------------------------------------------------------------------
# Las convenciones GenAI actuales: `parts` y `gen_ai.system_instructions`
# ---------------------------------------------------------------------------------


def _en_parts(attrs: dict[str, Any]) -> bool:
    """Si algún mensaje viene en `parts`. Barato a propósito: nuestro SDK escribe los
    mensajes con `content` y no hay que parsearlos en cada span para saberlo."""
    return any(
        isinstance(attrs.get(clave), str) and '"parts"' in attrs[clave]
        for clave in (semconv.GEN_AI_INPUT_MESSAGES, semconv.GEN_AI_OUTPUT_MESSAGES)
    )


def _genai_actual(salida: dict[str, Any]) -> None:
    for clave in (semconv.GEN_AI_INPUT_MESSAGES, semconv.GEN_AI_OUTPUT_MESSAGES):
        valor = salida.get(clave)
        if isinstance(valor, str) and '"parts"' in valor:
            mensajes = _json(valor)
            if isinstance(mensajes, list):
                salida[clave] = json.dumps(
                    [_mensaje_de_parts(m) for m in mensajes], ensure_ascii=False
                )
    instrucciones = _json(salida.get("gen_ai.system_instructions"))
    if isinstance(instrucciones, list):
        instrucciones = _texto_de_parts(instrucciones)
    if isinstance(instrucciones, str):
        _unir_sistema(salida, instrucciones)


def _texto_de_parts(parts: list[Any]) -> str | None:
    textos = [
        str(p.get("content", ""))
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    return "".join(textos) if textos else None


def _mensaje_de_parts(mensaje: Any) -> Any:
    """Un mensaje en `parts` → la forma que escribe el SDK de Python (la de OpenAI).

    Nunca pisa: si ya trae `content`, se queda como está. El texto se junta en
    `content`; las llamadas a herramientas pasan a `tool_calls`, y su respuesta a un
    mensaje `tool` con su `tool_call_id`. Lo que no es ni texto ni herramienta (una
    imagen, un fichero) no se pierde: se quedan las `parts` enteras al lado.
    """
    if not isinstance(mensaje, dict) or "content" in mensaje:
        return mensaje
    partes = mensaje.get("parts")
    if not isinstance(partes, list):
        return mensaje
    nuevo = {k: v for k, v in mensaje.items() if k != "parts"}
    llamadas, respuestas, otras = [], [], False
    for parte in partes:
        tipo = parte.get("type") if isinstance(parte, dict) else None
        if tipo == "tool_call":
            argumentos = parte.get("arguments")
            if not isinstance(argumentos, str):
                argumentos = json.dumps(argumentos, ensure_ascii=False)
            llamadas.append(
                {
                    "id": parte.get("id"),
                    "type": "function",
                    "function": {"name": parte.get("name"), "arguments": argumentos},
                }
            )
        elif tipo == "tool_call_response":
            respuestas.append(parte)
        elif tipo != "text":
            otras = True
    texto = _texto_de_parts(partes)
    if len(respuestas) == 1 and texto is None and not llamadas and not otras:
        respuesta = respuestas[0].get("response")
        nuevo["tool_call_id"] = respuestas[0].get("id")
        nuevo["content"] = (
            respuesta if isinstance(respuesta, str) else json.dumps(respuesta, ensure_ascii=False)
        )
        return nuevo
    nuevo["content"] = texto
    if llamadas:
        nuevo["tool_calls"] = llamadas
    if otras or respuestas:
        nuevo["parts"] = partes
    return nuevo


def _texto_de_bloques(valor: Any) -> str | None:
    """El `system` de Anthropic: una cadena o una lista de bloques `{type: text, text}`."""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, list):
        textos = [
            str(b.get("text", ""))
            for b in valor
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "".join(textos) if textos else None
    return None


def _unir_sistema(salida: dict[str, Any], texto: str | None) -> None:
    """Pone las instrucciones como primer mensaje, si no hay ya uno de sistema."""
    if not texto:
        return
    mensajes = _json(salida.get(semconv.GEN_AI_INPUT_MESSAGES))
    if mensajes is None:
        mensajes = []
    if not isinstance(mensajes, list):
        return
    if any(
        isinstance(m, dict) and str(m.get("role", "")).lower() in ("system", "developer")
        for m in mensajes
    ):
        return
    salida[semconv.GEN_AI_INPUT_MESSAGES] = json.dumps(
        [{"role": "system", "content": texto}, *mensajes], ensure_ascii=False
    )


def _salida_de_generaciones(attrs: dict[str, Any], salida: dict[str, Any]) -> None:
    """La respuesta de LangChain.js cuando sus mensajes llegan sin texto.

    La instrumentación de LangChain.js de OpenInference sólo manda el rol de la
    respuesta cuando el contenido es una lista (siempre, con la Responses API de
    OpenAI), pero deja en `output.value` el resultado de LangChain entero, con el texto
    en `generations[i][0].text`. Sólo se rellena lo que falta.
    """
    mensajes = _json(salida.get(semconv.GEN_AI_OUTPUT_MESSAGES))
    if not isinstance(mensajes, list) or not mensajes:
        return
    if any(isinstance(m, dict) and m.get("content") not in (None, "") for m in mensajes):
        return
    resultado = _json(attrs.get("output.value"))
    if not isinstance(resultado, dict):
        return
    generaciones = resultado.get("generations")
    if not isinstance(generaciones, list):
        return
    for mensaje, grupo in zip(mensajes, generaciones, strict=False):
        if isinstance(mensaje, dict) and isinstance(grupo, list) and grupo:
            texto = grupo[0].get("text") if isinstance(grupo[0], dict) else None
            if isinstance(texto, str) and texto:
                mensaje["content"] = texto
    salida[semconv.GEN_AI_OUTPUT_MESSAGES] = json.dumps(mensajes, ensure_ascii=False)


# ---------------------------------------------------------------------------------
# Común
# ---------------------------------------------------------------------------------


def _tokens_de_cache_coherentes(salida: dict[str, Any]) -> None:
    """Nuestro `input_tokens` es el TOTAL facturable, con la caché dentro (contrato §2).

    Las dos familias no coinciden en esto entre proveedores: con Anthropic, algunas
    versiones dan la entrada nueva por un lado y la caché por otro. Si lo cacheado más
    lo escrito supera la entrada, no hay duda de que venían separados, y se suman. Si
    no lo supera, se da por hecho que ya estaban dentro: si en realidad venían
    separados, la factura sale por debajo, que es el lado por el que equivocarse.
    """
    try:
        entrada = int(salida.get(semconv.GEN_AI_USAGE_INPUT_TOKENS) or 0)
        leidos = int(salida.get(semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS) or 0)
        escritos = int(salida.get(semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS) or 0)
    except (TypeError, ValueError):
        return
    if leidos + escritos > entrada:
        salida[semconv.GEN_AI_USAGE_INPUT_TOKENS] = entrada + leidos + escritos


def _json(valor: Any) -> Any:
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except ValueError:
            return valor
    return valor
