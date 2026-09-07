"""Construcción del árbol de una traza y de su resumen.

La vista de árbol es la pantalla que justifica que alguien pruebe Laplace, así que lo
que se sirve ya viene resuelto: jerarquía, coste acumulado por subárbol y marca de
repeticiones. El frontend pinta, no calcula.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from laplace.schema import Cost, Rollup, Span, TokenUsage, TraceSummary, TraceTreeNode


def build_tree(spans: list[Span]) -> list[TraceTreeNode]:
    """Ordena los spans en árbol, con rollups de subárbol y conteo de repeticiones.

    Los spans huérfanos (padre no recibido, o recibido más tarde) se cuelgan de la raíz
    en vez de desaparecer: una traza incompleta se ve incompleta, no vacía.
    """
    if not spans:
        return []

    by_id = {span.span_id: span for span in spans}
    repeats = Counter(span.dedup_hash for span in spans if span.dedup_hash)

    children: dict[str | None, list[Span]] = defaultdict(list)
    for span in spans:
        parent = span.parent_span_id if span.parent_span_id in by_id else None
        children[parent].append(span)

    for bucket in children.values():
        bucket.sort(key=lambda s: (s.start_time, s.span_id))

    visited: set[str] = set()

    def node_for(span: Span) -> TraceTreeNode:
        visited.add(span.span_id)
        node = TraceTreeNode(
            span=span,
            repeat_count=repeats.get(span.dedup_hash, 1) if span.dedup_hash else 1,
        )
        for child in children.get(span.span_id, []):
            if child.span_id in visited:  # ciclo: no puede pasar, pero no colgamos por ello
                continue
            node.children.append(node_for(child))
        node.subtree = _rollup(node)
        return node

    return [node_for(span) for span in children.get(None, [])]


def _rollup(node: TraceTreeNode) -> Rollup:
    """Totales del subárbol, incluido el propio nodo."""
    cost = node.span.cost.total_usd
    usage = node.span.usage
    rollup = Rollup(
        cost_usd=cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        span_count=1,
        error_count=1 if node.span.status == "error" else 0,
    )
    for child in node.children:
        rollup.cost_usd += child.subtree.cost_usd
        rollup.input_tokens += child.subtree.input_tokens
        rollup.output_tokens += child.subtree.output_tokens
        rollup.span_count += child.subtree.span_count
        rollup.error_count += child.subtree.error_count
    return rollup


def summarize(spans: list[Span], trace_id: str, project_id: str = "") -> TraceSummary:
    """Resumen de una traza a partir de sus spans.

    Se calcula en memoria en vez de con una segunda consulta: los spans ya están aquí y
    el resultado es idéntico al de la lista.
    """
    now = datetime.now(timezone.utc)
    if not spans:
        return TraceSummary(
            trace_id=trace_id, project_id=project_id, start_time=now, end_time=now
        )

    start = min(span.start_time for span in spans)
    end = max(span.end_time for span in spans)
    roots = [span for span in spans if not span.parent_span_id]
    error_count = sum(1 for span in spans if span.status == "error")
    # Pasos cuyo modelo no está en la tabla: el total de la traza está incompleto.
    sin_tarifa = sum(1 for span in spans if span.cost.unknown)
    asumidos = sum(1 for span in spans if span.cost.rate_assumed)
    # Los modelos de la traza. La lista de trazas ya los servía; aquí faltaban, así que
    # el modo avanzado enseñaba un guión en la ficha de la traza abierta.
    modelos = sorted(
        {span.llm.request_model for span in spans if span.llm and span.llm.request_model}
    )

    return TraceSummary(
        trace_id=trace_id,
        project_id=project_id or spans[0].project_id,
        root_name=roots[0].name if roots else min(spans, key=lambda s: s.start_time).name,
        status="error" if error_count else "ok",
        start_time=start,
        end_time=end,
        duration_ms=(end - start).total_seconds() * 1000.0,
        span_count=len(spans),
        error_count=error_count,
        llm_call_count=sum(1 for span in spans if span.type == "llm"),
        tool_call_count=sum(1 for span in spans if span.type == "tool"),
        usage=TokenUsage(
            input_tokens=sum(span.usage.input_tokens for span in spans),
            output_tokens=sum(span.usage.output_tokens for span in spans),
            cached_input_tokens=sum(span.usage.cached_input_tokens for span in spans),
            cache_write_tokens=sum(span.usage.cache_write_tokens for span in spans),
            cache_write_1h_tokens=sum(span.usage.cache_write_1h_tokens for span in spans),
            reasoning_tokens=sum(span.usage.reasoning_tokens for span in spans),
        ),
        unknown_cost_spans=sin_tarifa,
        assumed_rate_spans=asumidos,
        models=modelos,
        cost=Cost(
            input_usd=sum(span.cost.input_usd for span in spans),
            output_usd=sum(span.cost.output_usd for span in spans),
            total_usd=sum(span.cost.total_usd for span in spans),
            cache_read_usd=sum(span.cost.cache_read_usd for span in spans),
            cache_write_usd=sum(span.cost.cache_write_usd for span in spans),
            cache_saving_usd=sum(span.cost.cache_saving_usd for span in spans),
            unknown=sin_tarifa > 0,
            rate_assumed=asumidos > 0,
        ),
        session_id=next((s.session_id for s in spans if s.session_id), None),
        user_id=next((s.user_id for s in spans if s.user_id), None),
    )
