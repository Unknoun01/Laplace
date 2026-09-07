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
        dedup_model = llm.request_model
    elif tool is not None:
        dedup_payload = tool.arguments
        dedup_model = None
    else:
        dedup_payload = generic_input
        dedup_model = None

    consumed = {
        semconv.LAPLACE_SPAN_TYPE,
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
        events=events,
        attributes={k: v for k, v in attrs.items() if k not in consumed},
    )


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

    breakdown = prices.compute(
        response_model or request_model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        tier=tier,
        region=region,
    )

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
        cost=Cost(
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
        ),
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


def _str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
