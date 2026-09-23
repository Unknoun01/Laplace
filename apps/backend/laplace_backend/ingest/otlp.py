"""Traducción de OTLP a nuestro contrato de traza.

El endpoint es OTLP estándar, así que cualquier proceso ya instrumentado con
OpenTelemetry puede exportar a Laplace sin usar nuestro SDK. Aquí se hacen tres cosas
que el estándar no hace y que el producto necesita:

1. Clasificar el span en uno de los cinco tipos del contrato.
2. Calcular el coste desglosado (D-005).
3. Calcular el `dedup_hash` que permite detectar repeticiones (D-007).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from laplace import semconv
from laplace.schema import (
    Cost,
    LLMAttributes,
    RetrievalAttributes,
    Span,
    SpanEvent,
    TokenUsage,
    ToolAttributes,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from ..pricing import get_price_table

logger = logging.getLogger("laplace.ingest")

_STATUS_BY_CODE = {0: "unset", 1: "ok", 2: "error"}


# ---------------------------------------------------------------------------------
# Decodificación del sobre OTLP
# ---------------------------------------------------------------------------------


def decode_request(
    body: bytes, content_type: str = "", content_encoding: str = ""
) -> ExportTraceServiceRequest:
    """Acepta protobuf (lo que emite el SDK) y JSON (cómodo para depurar con curl)."""
    if "gzip" in (content_encoding or "").lower():
        body = gzip.decompress(body)

    request = ExportTraceServiceRequest()
    if "json" in (content_type or "").lower():
        from google.protobuf.json_format import Parse

        Parse(body.decode("utf-8"), request)
    else:
        request.ParseFromString(body)
    return request


def _any_value(value: Any) -> Any:
    """Convierte un `AnyValue` de OTLP a un valor de Python."""
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "string_value":
        return value.string_value
    if which == "bool_value":
        return value.bool_value
    if which == "int_value":
        return value.int_value
    if which == "double_value":
        return value.double_value
    if which == "bytes_value":
        return f"<bytes len={len(value.bytes_value)}>"
    if which == "array_value":
        return [_any_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in value.kvlist_value.values}
    return None


def _attributes(pairs: Any) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in pairs}


def _ts(nanos: int) -> datetime:
    return datetime.fromtimestamp(nanos // 1_000_000_000, tz=timezone.utc).replace(
        microsecond=(nanos % 1_000_000_000) // 1000
    )


def _json_or(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _as_message_list(value: Any) -> list[dict[str, Any]]:
    """Los mensajes se guardan en crudo; lo que no sea una lista de dicts se envuelve."""
    parsed = _json_or(value, None)
    if parsed is None:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item if isinstance(item, dict) else {"content": item} for item in parsed]
    return [{"content": parsed}]


# ---------------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------------


def classify(attrs: dict[str, Any]) -> str:
    """Tipo del span. Explícito si el SDK lo puso; si no, inferido de los atributos GenAI."""
    declared = attrs.get(semconv.LAPLACE_SPAN_TYPE)
    if declared in semconv.SPAN_TYPES:
        return str(declared)

    operation = attrs.get(semconv.GEN_AI_OPERATION_NAME)
    if operation == semconv.OPERATION_EXECUTE_TOOL or attrs.get(semconv.GEN_AI_TOOL_NAME):
        return semconv.SPAN_TYPE_TOOL
    if operation == semconv.OPERATION_INVOKE_AGENT:
        return semconv.SPAN_TYPE_AGENT
    if attrs.get(semconv.GEN_AI_REQUEST_MODEL) or attrs.get(semconv.GEN_AI_RESPONSE_MODEL):
        return semconv.SPAN_TYPE_LLM
    if attrs.get(semconv.LAPLACE_RETRIEVAL_QUERY):
        return semconv.SPAN_TYPE_RETRIEVAL
    return semconv.SPAN_TYPE_CHAIN


def dedup_hash(span_type: str, name: str, model: str | None, payload: Any) -> str:
    """Hash estable de (tipo, nombre, modelo, entrada).

    Dos spans con el mismo valor dentro de una traza son la misma llamada repetida:
    es la señal que alimenta la detección de bucles (Fase 2) y el diagnóstico (Fase 3).
    """
    if isinstance(payload, (dict, list)):
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    else:
        normalized = "" if payload is None else str(payload)
    material = "|".join([span_type, name, model or "", normalized.strip()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


_SOLO_DIGITOS = re.compile(r"\d+")
_ESPACIOS = re.compile(r"\s+")


def loop_hash(span_type: str, name: str, payload: Any, *, ignorar_numeros: bool = True) -> str:
    """Hash de la llamada **ignorando los números**, para reconocer bucles.

    `dedup_hash` sólo pilla repeticiones exactas. Un bucle de verdad casi nunca repite
    exactamente: lleva un contador de intentos, un número de página, una marca de
    tiempo. `estado_almacen(pedido, intento=1..6)` son seis llamadas distintas para
    `dedup_hash` y la misma pregunta para cualquiera que las mire (D-109).

    Se normaliza a lo bruto —minúsculas, cada tirada de dígitos a `#`, espacios
    colapsados— porque lo que hace falta es agrupar, no medir parecido. Dos llamadas
    que sólo difieren en un número caen juntas; dos que difieren en una palabra, no.
    """
    if isinstance(payload, (dict, list)):
        texto = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    else:
        texto = "" if payload is None else str(payload)
    # En la ENTRADA los números son ruido: el contador de intentos no cambia la
    # pregunta. En la SALIDA son justo lo contrario, son el avance: «pedido 3
    # procesado» y «pedido 4 procesado» son dos resultados distintos, y borrarles el
    # número convertiría un bucle que trabaja en un bucle atascado. Lo destapó el
    # contraejemplo de `test_un_bucle_que_avanza_no_se_señala` (D-109).
    if ignorar_numeros:
        texto = _SOLO_DIGITOS.sub("#", texto)
    texto = _ESPACIOS.sub(" ", texto.lower()).strip()
    material = "|".join([span_type, name, texto])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


#: Cuánto de las instrucciones se guarda como pista legible para la interfaz.
STEP_HINT_CHARS = 80


def _system_text(messages: list[dict[str, Any]]) -> str:
    """El texto de los mensajes de sistema, que es la parte fija de un prompt."""
    partes: list[str] = []
    for message in messages:
        if str(message.get("role", "")).lower() != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            partes.append(content)
        elif content is not None:
            partes.append(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str))
    return "\n".join(partes)


def _tool_names(raw: Any) -> list[str]:
    """Nombres de las herramientas declaradas, en los dos formatos de la industria."""
    tools = _json_or(raw, None)
    if not isinstance(tools, list):
        return []
    nombres = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        nombre = tool.get("name") or (tool.get("function") or {}).get("name")
        if nombre:
            nombres.append(str(nombre))
    return sorted(set(nombres))


def step_identity(
    attrs: dict[str, Any], name: str, messages: list[dict[str, Any]], tools: Any
) -> tuple[str, str, str, str]:
    """Identidad estable de un paso: `(clave, etiqueta, pista)`.

    Un paso es **una llamada al modelo hecha desde el mismo sitio y con las mismas
    instrucciones**. Las dos mitades hacen falta:

    - *Desde el mismo sitio* es el span que la envuelve (la función decorada con
      `@observe`). Sin esto, el nombre del span de LLM es `chat <modelo>` para todas
      las llamadas del agente y las tres reglas mezclan pasos que no tienen nada que
      ver: basta una llamada corta para hundir el suelo de tokens de la regla del
      contexto fijo y para que la del modelo caro compare medias sin sentido.
    - *Con las mismas instrucciones* es la huella del prompt de sistema y de las
      herramientas declaradas. Hace falta porque quien acaba de instalar Laplace no
      decora nada: sus llamadas cuelgan todas del mismo span raíz, o de ninguno.

    Si no hay ni lo uno ni lo otro —payloads desactivados y sin decorar— se cae al
    nombre del span, que es lo que había antes. No se inventa una identidad que no se
    puede sostener (D-060).
    """
    # El sitio es el CAMINO de pasos, no sólo el padre: `atender_ticket >
    # resumir_para_crm`. Con el padre a secas, dos agentes que llamen igual a una
    # función caían en el mismo sitio y sus poblaciones se mezclaban (D-106). Se cae al
    # padre para las trazas que llegaron antes de que el SDK mandara el camino.
    sitio = str(
        attrs.get(semconv.LAPLACE_STEP_SITE) or attrs.get(semconv.LAPLACE_STEP_PARENT) or ""
    ).strip()
    padre = str(attrs.get(semconv.LAPLACE_STEP_PARENT) or "").strip()
    instrucciones = _system_text(messages)
    herramientas = _tool_names(tools)

    huella = ""
    if instrucciones or herramientas:
        material = instrucciones + "\x00" + "\x00".join(herramientas)
        huella = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]

    if not sitio and not huella:
        # Sin ninguna de las dos señales, el nombre es todo lo que hay.
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16], name, "", ""

    clave = hashlib.sha256(f"{sitio}\x00{huella}".encode()).hexdigest()[:16]
    pista = " ".join(instrucciones.split())[:STEP_HINT_CHARS]
    # La etiqueta sigue siendo el nombre a secas: es lo que se lee en pantalla, y
    # «atender_ticket > resumir_para_crm» en un titular sería ruido. El camino va
    # aparte, para agrupar.
    etiqueta = padre or sitio or pista or name
    return clave, etiqueta, pista, sitio


# ---------------------------------------------------------------------------------
# OTLP -> Span
# ---------------------------------------------------------------------------------


def parse_spans(request: ExportTraceServiceRequest) -> list[Span]:
    """Aplana la petición OTLP en spans del contrato, ya con coste calculado."""
    prices = get_price_table()
    spans: list[Span] = []

    for resource_spans in request.resource_spans:
        resource = _attributes(resource_spans.resource.attributes)
        project_id = str(
            resource.get(semconv.LAPLACE_PROJECT_ID)
            or resource.get(semconv.SERVICE_NAME)
            or "default"
        )
        for scope_spans in resource_spans.scope_spans:
            for proto_span in scope_spans.spans:
                try:
                    spans.append(_build_span(proto_span, project_id, resource, prices))
                except Exception:  # noqa: BLE001 - un span roto no invalida el lote
                    logger.exception("no se pudo procesar un span; se descarta")
    return spans


def _build_span(proto_span: Any, project_id: str, resource: dict[str, Any], prices: Any) -> Span:
    attrs = _attributes(proto_span.attributes)
    span_type = classify(attrs)
    name = proto_span.name

    start = _ts(proto_span.start_time_unix_nano)
    end = _ts(proto_span.end_time_unix_nano)
    duration_ms = (proto_span.end_time_unix_nano - proto_span.start_time_unix_nano) / 1_000_000.0

    events = [
        SpanEvent(
            name=event.name,
            timestamp=_ts(event.time_unix_nano),
            attributes=_attributes(event.attributes),
        )
        for event in proto_span.events
    ]

    status = _STATUS_BY_CODE.get(int(proto_span.status.code), "unset")
    status_message = proto_span.status.message or ""
    if status != "error" and any(e.name == semconv.EVENT_EXCEPTION for e in events):
        # Una excepción registrada es un fallo aunque el instrumentador olvidara el estado.
        status = "error"

    llm = _build_llm(attrs, prices) if span_type == semconv.SPAN_TYPE_LLM else None
    tool = _build_tool(attrs) if span_type == semconv.SPAN_TYPE_TOOL else None
    retrieval = _build_retrieval(attrs) if span_type == semconv.SPAN_TYPE_RETRIEVAL else None

    generic_input = _json_or(attrs.get(semconv.LAPLACE_INPUT), None)
    generic_output = _json_or(attrs.get(semconv.LAPLACE_OUTPUT), None)

    if llm is not None:
        dedup_payload: Any = llm.input_messages
        salida_payload: Any = llm.output_messages
        dedup_model = llm.request_model
    elif tool is not None:
        dedup_payload = tool.arguments
        salida_payload = tool.output
        dedup_model = None
    else:
        dedup_payload = generic_input
        salida_payload = generic_output
        dedup_model = None

    if llm is not None:
        step_key, step_label, step_hint, step_site = step_identity(
            attrs, name, llm.input_messages, attrs.get("laplace.request.tools")
        )
    else:
        step_key = step_label = step_hint = step_site = ""

    consumed = {
        semconv.LAPLACE_SPAN_TYPE,
        semconv.LAPLACE_STEP_PARENT,
        semconv.LAPLACE_SESSION_ID,
        semconv.LAPLACE_USER_ID,
        semconv.LAPLACE_TAGS,
        semconv.LAPLACE_METADATA,
        semconv.LAPLACE_INPUT,
        semconv.LAPLACE_OUTPUT,
        semconv.GEN_AI_INPUT_MESSAGES,
        semconv.GEN_AI_OUTPUT_MESSAGES,
        semconv.LAPLACE_TOOL_ARGUMENTS,
        semconv.LAPLACE_TOOL_OUTPUT,
        semconv.LAPLACE_RETRIEVAL_DOCUMENTS,
        semconv.LAPLACE_BILLING_TIER,
        semconv.LAPLACE_BILLING_REGION,
        semconv.LAPLACE_PROMPT_NAME,
        semconv.LAPLACE_PROMPT_VERSION,
    }

    return Span(
        span_id=proto_span.span_id.hex(),
        trace_id=proto_span.trace_id.hex(),
        parent_span_id=proto_span.parent_span_id.hex() or None,
        project_id=project_id,
        name=name,
        type=span_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        status_message=status_message,
        start_time=start,
        end_time=end,
        duration_ms=duration_ms,
        llm=llm,
        tool=tool,
        retrieval=retrieval,
        input=generic_input,
        output=generic_output,
        session_id=_str_or_none(attrs.get(semconv.LAPLACE_SESSION_ID)),
        user_id=_str_or_none(attrs.get(semconv.LAPLACE_USER_ID)),
        tags=[str(t) for t in (_json_or(attrs.get(semconv.LAPLACE_TAGS), []) or [])],
        metadata=_json_or(attrs.get(semconv.LAPLACE_METADATA), {}) or {},
        dedup_hash=dedup_hash(span_type, name, dedup_model, dedup_payload),
        loop_hash=loop_hash(span_type, name, dedup_payload),
        # El de la salida sirve para la otra mitad de la pregunta: un bucle que da
        # vueltas sin avanzar produce siempre lo mismo (D-109).
        loop_out_hash=loop_hash(span_type, name, salida_payload, ignorar_numeros=False),
        step_key=step_key,
        step_site=step_site,
        step_label=step_label,
        step_hint=step_hint,
        # El SDK sólo escribe esto cuando ha comprobado que el texto de esa versión iba
        # de verdad en los mensajes. Aquí se copia tal cual: la ingesta no deduce una
        # versión que el emisor no haya afirmado (D-090).
        prompt_name=str(attrs.get(semconv.LAPLACE_PROMPT_NAME) or ""),
        prompt_version=_entero(attrs.get(semconv.LAPLACE_PROMPT_VERSION)),
        events=events,
        attributes={k: v for k, v in attrs.items() if k not in consumed},
    )


def _coste(prices: Any, model: str | None, usage: TokenUsage, tier: str, region: str) -> Cost:
    """El coste de una llamada. Lo usan la ingesta y el recálculo, y es el mismo."""
    breakdown = prices.compute(
        model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        tier=tier,
        region=region,
    )
    return Cost(
        input_usd=breakdown.input_usd,
        output_usd=breakdown.output_usd,
        total_usd=breakdown.total_usd,
        cache_read_usd=breakdown.cache_read_usd,
        cache_write_usd=breakdown.cache_write_usd,
        cache_saving_usd=breakdown.cache_saving_usd,
        unknown=breakdown.unknown,
        rate_assumed=breakdown.assumed,
        rate_note=breakdown.note,
        rate=breakdown.rate,
    )


def recalcular_coste(span: Span, prices: Any) -> Span:
    """El mismo span con el coste calculado otra vez contra la tabla en vigor.

    Existe para las tarifas propias (D-123): el coste se calcula en la ingesta, así que
    un precio añadido después no alcanzaba a lo ya guardado y el modelo seguía saliendo
    como «no lo sabemos» en todo el histórico.
    """
    if span.llm is None:
        return span
    llm = span.llm
    coste = _coste(
        prices,
        llm.response_model or llm.request_model,
        llm.usage,
        llm.billing_tier,
        llm.billing_region,
    )
    return span.model_copy(update={"llm": llm.model_copy(update={"cost": coste})})


def _build_llm(attrs: dict[str, Any], prices: Any) -> LLMAttributes:
    usage = TokenUsage(
        input_tokens=int(attrs.get(semconv.GEN_AI_USAGE_INPUT_TOKENS) or 0),
        output_tokens=int(attrs.get(semconv.GEN_AI_USAGE_OUTPUT_TOKENS) or 0),
        cached_input_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS) or 0),
        cache_write_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS) or 0),
        cache_write_1h_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_1H_TOKENS) or 0),
        reasoning_tokens=int(attrs.get(semconv.LAPLACE_USAGE_REASONING_TOKENS) or 0),
        estimated=bool(attrs.get(semconv.LAPLACE_USAGE_ESTIMATED) or False),
    )
    request_model = _str_or_none(attrs.get(semconv.GEN_AI_REQUEST_MODEL))
    response_model = _str_or_none(attrs.get(semconv.GEN_AI_RESPONSE_MODEL))
    # Sin atributo, el metro es el estándar: es lo que los proveedores facturan por
    # defecto, no una suposición que haya que marcar como tal.
    tier = str(attrs.get(semconv.LAPLACE_BILLING_TIER) or "standard")
    region = str(attrs.get(semconv.LAPLACE_BILLING_REGION) or "global")

    coste = _coste(prices, response_model or request_model, usage, tier, region)

    params = {
        key.rsplit(".", 1)[-1]: value
        for key, value in attrs.items()
        if key.startswith("gen_ai.request.") and key != semconv.GEN_AI_REQUEST_MODEL
    }

    finish = attrs.get(semconv.GEN_AI_RESPONSE_FINISH_REASONS) or []
    if isinstance(finish, str):
        finish = [finish]

    return LLMAttributes(
        system=_str_or_none(attrs.get(semconv.GEN_AI_SYSTEM)),
        request_model=request_model,
        response_model=response_model,
        response_id=_str_or_none(attrs.get(semconv.GEN_AI_RESPONSE_ID)),
        operation=_str_or_none(attrs.get(semconv.GEN_AI_OPERATION_NAME)),
        billing_tier=tier,
        billing_region=region,
        usage=usage,
        cost=coste,
        input_messages=_as_message_list(attrs.get(semconv.GEN_AI_INPUT_MESSAGES)),
        output_messages=_as_message_list(attrs.get(semconv.GEN_AI_OUTPUT_MESSAGES)),
        params=params,
        finish_reasons=[str(r) for r in finish],
    )


def _build_tool(attrs: dict[str, Any]) -> ToolAttributes:
    return ToolAttributes(
        name=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_NAME)),
        call_id=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_CALL_ID)),
        description=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_DESCRIPTION)),
        arguments=_json_or(attrs.get(semconv.LAPLACE_TOOL_ARGUMENTS), None),
        output=_json_or(attrs.get(semconv.LAPLACE_TOOL_OUTPUT), None),
    )


def _build_retrieval(attrs: dict[str, Any]) -> RetrievalAttributes:
    documents = _json_or(attrs.get(semconv.LAPLACE_RETRIEVAL_DOCUMENTS), []) or []
    if not isinstance(documents, list):
        documents = [{"content": documents}]
    return RetrievalAttributes(
        query=_str_or_none(attrs.get(semconv.LAPLACE_RETRIEVAL_QUERY)),
        top_k=int(attrs[semconv.LAPLACE_RETRIEVAL_TOP_K])
        if attrs.get(semconv.LAPLACE_RETRIEVAL_TOP_K) is not None
        else None,
        documents=[d if isinstance(d, dict) else {"content": d} for d in documents],
    )


def _entero(value: Any) -> int:
    """Un entero, o cero. Una versión ilegible no puede tumbar la ingesta de un span."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
