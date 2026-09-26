"""Conversión de fila a modelo del contrato, compartida por los dos almacenes.

ClickHouse y SQLite guardan los mismos campos con los mismos nombres, así que la
traducción de una fila a un `Span` o a un `TraceSummary` es la misma en los dos. Vivía
dentro del almacén de ClickHouse; sacarla aquí es lo que hace que el modo local sea el
mismo producto y no una versión paralela que se va separando sola (D-066).

Las dos funciones reciben la fila como diccionario, con las etiquetas que puso la propia
consulta. Por posición no: añadir una columna en medio y desplazar el resto en silencio
es un error demasiado barato de cometer.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from laplace.schema import (
    Cost,
    LLMAttributes,
    RetrievalAttributes,
    Span,
    SpanEvent,
    TokenUsage,
    ToolAttributes,
    TraceSummary,
)

from ..pricing import es_no_verificada, modelos_no_verificados

logger = logging.getLogger("laplace.storage")

#: Orden de columnas de la tabla `spans`. Los inserts se construyen contra esta lista,
#: y los dos almacenes la comparten para que no puedan divergir.
COLUMNS = (
    "project_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "span_type",
    "status",
    "status_message",
    "start_time",
    "end_time",
    "duration_ms",
    "gen_ai_system",
    "operation",
    "request_model",
    "response_model",
    "response_id",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "reasoning_tokens",
    "usage_estimated",
    "cost_input_usd",
    "cost_output_usd",
    "cost_total_usd",
    "cost_unknown",
    "price_rate",
    "cost_cache_read_usd",
    "cost_cache_write_usd",
    "cost_cache_saving_usd",
    "cost_rate_assumed",
    "price_note",
    "billing_tier",
    "billing_region",
    "input_messages",
    "output_messages",
    "llm_params",
    "finish_reasons",
    "tool_name",
    "tool_call_id",
    "tool_arguments",
    "tool_output",
    "retrieval_query",
    "retrieval_documents",
    "input_payload",
    "output_payload",
    "session_id",
    "user_id",
    "tags",
    "metadata",
    "dedup_hash",
    "loop_hash",
    "loop_out_hash",
    "step_key",
    "step_label",
    "step_hint",
    "step_site",
    "prompt_name",
    "prompt_version",
    "events",
    "attributes",
)


#: Un paso está «partido» cuando sus identidades distintas se acercan al número de
#: ejecuciones: una huella por llamada significa que el prompt lleva datos variables
#: dentro. Con pocas ejecuciones no se puede afirmar, así que hay mínimo.
SPLIT_RATIO = 0.8
SPLIT_MIN_TRACES = 5


def dumps(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def loads(text: Any, fallback: Any) -> Any:
    if not text:
        return fallback
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def row_to_trace_cost(r: Any) -> Any:
    """Fila a `TraceCost`. Vive aquí por el mismo motivo que `row_to_span`: la misma
    fila tiene que convertirse igual venga de ClickHouse o de SQLite, y los alias de las
    dos consultas son el contrato (D-066)."""
    from .base import TraceCost

    return TraceCost(
        trace_id=r["trace_id"],
        cost_usd=float(r["coste"] or 0.0),
        input_tokens=int(r["tok_in"] or 0),
        output_tokens=int(r["tok_out"] or 0),
        duration_ms=float(r["duracion"] or 0.0),
        spans=int(r["pasos"] or 0),
        error=bool(r["fallo"]),
        unknown_cost_spans=int(r["sin_tarifa"] or 0),
        assumed_rate_spans=int(r["asumida"] or 0),
    )


def _instante(value: Any) -> datetime | None:
    return utc(value) if value else None


def row_to_prompt_usage(f: Any) -> Any:
    """Fila a `PromptUsage`. Los alias de la consulta son el contrato entre los dos
    almacenes, igual que en `row_to_span` (D-066)."""
    from .base import PromptUsage

    return PromptUsage(
        name=f["nombre"],
        version=int(f["version"] or 0),
        traces=int(f["trazas"] or 0),
        calls=int(f["llamadas"] or 0),
        cost_usd=float(f["coste"] or 0.0),
        input_tokens=int(f["tok_in"] or 0),
        output_tokens=int(f["tok_out"] or 0),
        duration_ms=float(f["duracion"] or 0.0),
        unknown_cost_spans=int(f["sin_tarifa"] or 0),
        assumed_rate_spans=int(f["asumida"] or 0),
        first_seen=_instante(f["primero"]),
        last_seen=_instante(f["ultimo"]),
    )


def row_to_observed_prompt(f: Any) -> Any:
    """Fila a `ObservedPrompt`: un juego de instrucciones visto en las trazas."""
    from .base import ObservedPrompt

    return ObservedPrompt(
        step_key=f["clave"],
        site=f.get("sitio") or "",
        step_label=f["paso"] or "",
        hint=f["pista"] or "",
        traces=int(f["trazas"] or 0),
        calls=int(f["llamadas"] or 0),
        cost_usd=float(f["coste"] or 0.0),
        input_tokens=int(f["tok_in"] or 0),
        output_tokens=int(f["tok_out"] or 0),
        first_seen=_instante(f["primero"]),
        last_seen=_instante(f["ultimo"]),
    )


def coverage_from_rows(fila: Any, pasos: Any) -> Any:
    """Filas a `CoverageFacts`, igual para los dos almacenes (D-066).

    Un paso se considera **partido** cuando tiene casi tantas identidades distintas como
    ejecuciones: eso no son cincuenta versiones de un paso, es un prompt con una fecha
    dentro. El umbral vive en `coverage.py`, que es quien lo explica en pantalla; aquí
    sólo se aplica para no traer la lista entera de pasos a memoria.
    """
    from .base import CoverageFacts

    # Se agrupa por sitio de llamada (el camino: «atender_ticket > resumir_para_crm»)
    # pero se NOMBRA con la etiqueta, que es lo que el usuario reconoce. Enseñar el
    # camino entero en un titular sería ruido; agrupar por el nombre suelto era el fallo
    # de D-106.
    partidos = [
        (p["etiqueta"] if "etiqueta" in p.keys() and p["etiqueta"] else p["paso"])
        for p in pasos
        if int(p["trazas"] or 0) >= SPLIT_MIN_TRACES
        and int(p["identidades"] or 0) >= int(p["trazas"] or 0) * SPLIT_RATIO
        and int(p["identidades"] or 0) > 1
    ]
    return CoverageFacts(
        llm_calls=int(fila["llamadas"] or 0),
        identified_steps=int(fila["identificadas"] or 0),
        priced=int(fila["con_tarifa"] or 0),
        measured_tokens=int(fila["con_tokens"] or 0),
        with_prompt_version=int(fila["con_prompt"] or 0),
        steps=len(pasos),
        split_steps=sorted(partidos),
    )


def utc(value: Any) -> datetime:
    """Un instante en UTC, venga como `datetime` o como texto ISO."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_list(value: Any) -> list[str]:
    """Una lista de textos, venga como lista (ClickHouse) o como JSON (SQLite)."""
    if isinstance(value, list):
        return [str(v) for v in value]
    parsed = loads(value, [])
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def row_to_span(r: dict[str, Any]) -> Span:
    """Una fila de `spans` como `Span` del contrato."""
    span_type = r["span_type"]

    llm = None
    if span_type == "llm":
        llm = LLMAttributes(
            system=r["gen_ai_system"] or None,
            operation=r["operation"] or None,
            request_model=r["request_model"] or None,
            response_model=r["response_model"] or None,
            response_id=r["response_id"] or None,
            usage=TokenUsage(
                input_tokens=int(r["input_tokens"]),
                output_tokens=int(r["output_tokens"]),
                cached_input_tokens=int(r["cached_input_tokens"]),
                cache_write_tokens=int(r["cache_write_tokens"]),
                cache_write_1h_tokens=int(r["cache_write_1h_tokens"]),
                reasoning_tokens=int(r["reasoning_tokens"]),
                estimated=bool(r["usage_estimated"]),
            ),
            cost=Cost(
                input_usd=float(r["cost_input_usd"]),
                output_usd=float(r["cost_output_usd"]),
                total_usd=float(r["cost_total_usd"]),
                cache_read_usd=float(r["cost_cache_read_usd"]),
                cache_write_usd=float(r["cost_cache_write_usd"]),
                cache_saving_usd=float(r["cost_cache_saving_usd"]),
                unknown=bool(r["cost_unknown"]),
                rate_assumed=bool(r["cost_rate_assumed"]),
                rate_note=r["price_note"],
                rate=r["price_rate"],
                rate_unverified=es_no_verificada(r["price_rate"]),
            ),
            billing_tier=r["billing_tier"] or "standard",
            billing_region=r["billing_region"] or "global",
            input_messages=loads(r["input_messages"], []) or [],
            output_messages=loads(r["output_messages"], []) or [],
            params=loads(r["llm_params"], {}) or {},
            finish_reasons=as_list(r["finish_reasons"]),
        )

    tool = None
    if span_type == "tool":
        tool = ToolAttributes(
            name=r["tool_name"] or None,
            call_id=r["tool_call_id"] or None,
            arguments=loads(r["tool_arguments"], None),
            output=loads(r["tool_output"], None),
        )

    retrieval = None
    if span_type == "retrieval":
        retrieval = RetrievalAttributes(
            query=r["retrieval_query"] or None,
            documents=loads(r["retrieval_documents"], []) or [],
        )

    raw_events = loads(r["events"], []) or []
    events = [SpanEvent(**event) for event in raw_events if isinstance(event, dict)]

    return Span(
        span_id=r["span_id"],
        trace_id=r["trace_id"],
        parent_span_id=r["parent_span_id"] or None,
        project_id=r["project_id"],
        name=r["name"],
        type=span_type,
        status=r["status"],
        status_message=r["status_message"],
        start_time=utc(r["start_time"]),
        end_time=utc(r["end_time"]),
        duration_ms=float(r["duration_ms"]),
        llm=llm,
        tool=tool,
        retrieval=retrieval,
        input=loads(r["input_payload"], None),
        output=loads(r["output_payload"], None),
        session_id=r["session_id"] or None,
        user_id=r["user_id"] or None,
        tags=as_list(r["tags"]),
        metadata=loads(r["metadata"], {}) or {},
        dedup_hash=r["dedup_hash"],
        loop_hash=(r["loop_hash"] if "loop_hash" in r.keys() else ""),
        loop_out_hash=(r["loop_out_hash"] if "loop_out_hash" in r.keys() else ""),
        step_key=r["step_key"],
        step_label=r["step_label"],
        step_hint=r["step_hint"],
        # `get` y no índice: una base de antes de D-106 no tiene la columna, y la
        # interfaz no puede caerse por leer una traza vieja.
        step_site=(r["step_site"] if "step_site" in r.keys() else ""),
        prompt_name=r["prompt_name"] or "",
        prompt_version=int(r["prompt_version"] or 0),
        events=events,
        attributes=loads(r["attributes"], {}) or {},
    )


def row_to_summary(r: dict[str, Any]) -> TraceSummary:
    """Una fila agregada por traza como `TraceSummary`.

    Los alias de la consulta que la produce son parte del contrato entre almacén y
    lectura: `trace_project_id`, `trace_session_id` y `trace_user_id` se llaman así
    porque en ClickHouse un alias de agregación no puede llamarse igual que la columna
    que agrega.
    """
    sin_tarifa = int(r["unknown_cost_spans"])
    asumidos = int(r["assumed_rate_spans"])
    modelos = r["modelos"]
    if isinstance(modelos, str):
        modelos = [m for m in modelos.split(",") if m]

    return TraceSummary(
        trace_id=r["trace_id"],
        project_id=r["trace_project_id"],
        root_name=r["root_name"] or "",
        status="error" if int(r["error_count"]) else "ok",
        start_time=utc(r["started"]),
        end_time=utc(r["ended"]),
        duration_ms=float(r["duration_ms"]),
        span_count=int(r["span_count"]),
        error_count=int(r["error_count"]),
        llm_call_count=int(r["llm_call_count"]),
        tool_call_count=int(r["tool_call_count"]),
        usage=TokenUsage(
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            cached_input_tokens=int(r["cached_input_tokens"]),
            cache_write_tokens=int(r["cache_write_tokens"]),
            cache_write_1h_tokens=int(r["cache_write_1h_tokens"]),
            reasoning_tokens=int(r["reasoning_tokens"]),
        ),
        unknown_cost_spans=sin_tarifa,
        assumed_rate_spans=asumidos,
        cost=Cost(
            input_usd=float(r["cost_input_usd"]),
            output_usd=float(r["cost_output_usd"]),
            total_usd=float(r["cost_total_usd"]),
            cache_saving_usd=float(r["cache_saving_usd"]),
            unknown=sin_tarifa > 0,
            rate_assumed=asumidos > 0,
            rate_unverified=bool(modelos_no_verificados(modelos)),
        ),
        # El `sorted` es lo único que hace que los dos almacenes digan lo mismo aquí:
        # ClickHouse junta los modelos con `groupArray`, que conserva el orden de
        # inserción, y SQLite con `GROUP_CONCAT(DISTINCT …)`, que ordena por dentro. Sin
        # esto, la misma traza enseña «zzz, aaa» en la nube y «aaa, zzz» en local. Tiene
        # su prueba de paridad; no se quita (D-099).
        models=sorted(modelos or []),
        session_id=r["trace_session_id"] or None,
        user_id=r["trace_user_id"] or None,
        input_preview=vista_previa(r.get("root_input")),
    )


def vista_previa(payload: Any, largo: int = 140) -> str:
    """La entrada de una traza en una línea legible.

    La entrada es lo que el usuario pasó a su función decorada, serializado: un dict de
    argumentos, una lista de mensajes o un texto. Se busca la primera cadena que diga
    algo —el argumento de texto, el último mensaje del usuario— en vez de enseñar el
    JSON entero con sus llaves.
    """
    if not payload:
        return ""
    texto = str(payload)
    try:
        datos = json.loads(texto)
    except (ValueError, TypeError):
        datos = texto

    def primera(valor: Any) -> str:
        if isinstance(valor, str):
            return valor
        if isinstance(valor, dict):
            if isinstance(valor.get("content"), str):
                return valor["content"]
            for v in valor.values():
                encontrado = primera(v)
                if encontrado:
                    return encontrado
        if isinstance(valor, list):
            usuarios = [m for m in valor if isinstance(m, dict) and m.get("role") == "user"]
            for v in reversed(usuarios or valor):
                encontrado = primera(v)
                if encontrado:
                    return encontrado
        return ""

    linea = " ".join(primera(datos).split())
    return linea if len(linea) <= largo else linea[: largo - 1].rstrip() + "…"


def span_to_row(span: Span) -> list[Any]:
    """Un `Span` como fila, en el orden de `COLUMNS`."""
    llm = span.llm
    tool = span.tool
    retrieval = span.retrieval
    usage = llm.usage if llm else TokenUsage()
    cost = llm.cost if llm else Cost()

    return [
        span.project_id,
        span.trace_id,
        span.span_id,
        span.parent_span_id or "",
        span.name,
        span.type,
        span.status,
        span.status_message,
        span.start_time,
        span.end_time,
        float(span.duration_ms),
        (llm.system if llm else None) or "",
        (llm.operation if llm else None) or "",
        (llm.request_model if llm else None) or "",
        (llm.response_model if llm else None) or "",
        (llm.response_id if llm else None) or "",
        usage.input_tokens,
        usage.output_tokens,
        usage.cached_input_tokens,
        usage.cache_write_tokens,
        usage.cache_write_1h_tokens,
        usage.reasoning_tokens,
        1 if usage.estimated else 0,
        cost.input_usd,
        cost.output_usd,
        cost.total_usd,
        1 if cost.unknown else 0,
        cost.rate,
        cost.cache_read_usd,
        cost.cache_write_usd,
        cost.cache_saving_usd,
        1 if cost.rate_assumed else 0,
        cost.rate_note,
        (llm.billing_tier if llm else None) or "standard",
        (llm.billing_region if llm else None) or "global",
        dumps(llm.input_messages) if llm else "",
        dumps(llm.output_messages) if llm else "",
        dumps(llm.params) if llm else "",
        list(llm.finish_reasons) if llm else [],
        (tool.name if tool else None) or "",
        (tool.call_id if tool else None) or "",
        dumps(tool.arguments) if tool else "",
        dumps(tool.output) if tool else "",
        (retrieval.query if retrieval else None) or "",
        dumps(retrieval.documents) if retrieval else "",
        dumps(span.input),
        dumps(span.output),
        span.session_id or "",
        span.user_id or "",
        list(span.tags),
        dumps(span.metadata),
        span.dedup_hash,
        span.loop_hash,
        span.loop_out_hash,
        span.step_key,
        span.step_label,
        span.step_hint,
        span.step_site,
        span.prompt_name,
        int(span.prompt_version or 0),
        dumps([event.model_dump(mode="json") for event in span.events]),
        dumps(span.attributes),
    ]
