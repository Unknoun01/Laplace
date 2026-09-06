"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatCost, formatDuration, formatTokens, spanColors } from "@/lib/format";
import type { Trace, TraceTreeNode } from "@/lib/types";
import { SpanDetail } from "./SpanDetail";

interface Row {
  node: TraceTreeNode;
  depth: number;
}

/** Aplana el árbol a filas, saltándose los subárboles plegados. */
function flatten(nodes: TraceTreeNode[], collapsed: Set<string>, depth = 0): Row[] {
  const rows: Row[] = [];
  for (const node of nodes) {
    rows.push({ node, depth });
    if (node.children.length > 0 && !collapsed.has(node.span.span_id)) {
      rows.push(...flatten(node.children, collapsed, depth + 1));
    }
  }
  return rows;
}

export function TraceExplorer({ trace }: { trace: Trace }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(
    trace.roots[0]?.span.span_id ?? null,
  );
  const listRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => flatten(trace.roots, collapsed), [trace.roots, collapsed]);

  const byId = useMemo(() => {
    const map = new Map<string, TraceTreeNode>();
    const walk = (nodes: TraceTreeNode[]) => {
      for (const node of nodes) {
        map.set(node.span.span_id, node);
        walk(node.children);
      }
    };
    walk(trace.roots);
    return map;
  }, [trace.roots]);

  const selected = selectedId ? byId.get(selectedId) ?? null : null;

  // Ventana temporal de la traza: es el eje del waterfall.
  const timeWindow = useMemo(() => {
    const start = new Date(trace.summary.start_time).getTime();
    const end = new Date(trace.summary.end_time).getTime();
    return { start, span: Math.max(end - start, 1) };
  }, [trace.summary.start_time, trace.summary.end_time]);

  const toggle = useCallback((spanId: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(spanId)) next.delete(spanId);
      else next.add(spanId);
      return next;
    });
  }, []);

  // Navegación con teclado: en un árbol de cincuenta pasos, el ratón sobra.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      const index = rows.findIndex((row) => row.node.span.span_id === selectedId);
      if (event.key === "ArrowDown" || event.key === "j") {
        event.preventDefault();
        setSelectedId(rows[Math.min(index + 1, rows.length - 1)]?.node.span.span_id ?? null);
      } else if (event.key === "ArrowUp" || event.key === "k") {
        event.preventDefault();
        setSelectedId(rows[Math.max(index - 1, 0)]?.node.span.span_id ?? null);
      } else if (event.key === "ArrowRight" && selectedId) {
        setCollapsed((previous) => {
          const next = new Set(previous);
          next.delete(selectedId);
          return next;
        });
      } else if (event.key === "ArrowLeft" && selectedId) {
        const row = rows[index];
        if (row && row.node.children.length > 0 && !collapsed.has(selectedId)) {
          toggle(selectedId);
        } else {
          // Ya está plegado (o es una hoja): sube al padre.
          for (let i = index - 1; i >= 0; i -= 1) {
            if (rows[i].depth < (row?.depth ?? 0)) {
              setSelectedId(rows[i].node.span.span_id);
              break;
            }
          }
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [rows, selectedId, collapsed, toggle]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const allExpanded = collapsed.size === 0;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <section className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/50">
          <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
            <span className="font-medium uppercase tracking-wide">Árbol de ejecución</span>
            <span className="hidden sm:inline">
              {rows.length} de {trace.summary.span_count} pasos visibles
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-[11px] text-slate-400 md:inline">
              ↑↓ moverse · ←→ plegar
            </span>
            <button
              type="button"
              onClick={() =>
                setCollapsed(
                  allExpanded
                    ? new Set([...byId.values()].filter((n) => n.children.length).map((n) => n.span.span_id))
                    : new Set(),
                )
              }
              className="rounded border border-slate-200 px-2 py-1 text-xs hover:bg-white dark:border-slate-700 dark:hover:bg-slate-800"
            >
              {allExpanded ? "Plegar todo" : "Desplegar todo"}
            </button>
          </div>
        </header>

        <div ref={listRef} className="max-h-[calc(100vh-19rem)] overflow-auto">
          {rows.map((row) => (
            <TreeRow
              key={row.node.span.span_id}
              row={row}
              window={timeWindow}
              selected={row.node.span.span_id === selectedId}
              collapsed={collapsed.has(row.node.span.span_id)}
              onSelect={setSelectedId}
              onToggle={toggle}
            />
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 dark:border-slate-800">
        {selected ? (
          <SpanDetail node={selected} />
        ) : (
          <p className="p-6 text-sm text-slate-500">Selecciona un paso del árbol.</p>
        )}
      </section>
    </div>
  );
}

function TreeRow({
  row,
  window: timeWindow,
  selected,
  collapsed,
  onSelect,
  onToggle,
}: {
  row: Row;
  window: { start: number; span: number };
  selected: boolean;
  collapsed: boolean;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
}) {
  const { node, depth } = row;
  const span = node.span;
  const colors = spanColors(span.type);
  const failed = span.status === "error";

  const start = new Date(span.start_time).getTime();
  const offset = ((start - timeWindow.start) / timeWindow.span) * 100;
  const width = Math.max((span.duration_ms / timeWindow.span) * 100, 0.6);

  // El coste que se enseña en la fila es el del subárbol: en un nodo `agent` el coste
  // propio es cero y lo que interesa saber es cuánto ha costado esa rama entera.
  const cost = node.subtree.cost_usd;
  const tokens = node.subtree.input_tokens + node.subtree.output_tokens;

  return (
    <div
      data-selected={selected}
      onClick={() => onSelect(span.span_id)}
      role="button"
      tabIndex={-1}
      className={`grid cursor-pointer grid-cols-[minmax(0,1fr)_9rem_4.5rem_4.5rem] items-center gap-3 border-l-2 py-1.5 pr-3 text-sm transition-colors ${
        selected
          ? "border-l-sky-500 bg-sky-50 dark:bg-sky-500/10"
          : "border-l-transparent hover:bg-slate-50 dark:hover:bg-slate-900/60"
      }`}
    >
      <div className="flex min-w-0 items-center" style={{ paddingLeft: `${depth * 16 + 8}px` }}>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onToggle(span.span_id);
          }}
          className={`mr-1 flex h-4 w-4 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 ${
            node.children.length === 0 ? "invisible" : ""
          }`}
          aria-label={collapsed ? "desplegar" : "plegar"}
        >
          <svg viewBox="0 0 12 12" className={`h-3 w-3 transition-transform ${collapsed ? "" : "rotate-90"}`}>
            <path d="M4 2.5 8 6l-4 3.5z" fill="currentColor" />
          </svg>
        </button>

        <span className={`mr-2 h-2 w-2 shrink-0 rounded-full ${failed ? "bg-rose-500" : colors.dot}`} />
        <span className={`truncate ${failed ? "text-rose-700 dark:text-rose-300" : ""}`}>
          {span.name}
        </span>

        {node.children.length > 0 && collapsed && (
          <span className="ml-2 shrink-0 rounded bg-slate-100 px-1 text-[11px] tabular-nums text-slate-500 dark:bg-slate-800 dark:text-slate-400">
            +{node.subtree.span_count - 1}
          </span>
        )}

        {node.repeat_count > 1 && (
          <span
            className="ml-2 shrink-0 rounded bg-orange-100 px-1.5 text-[11px] font-medium text-orange-700 dark:bg-orange-500/15 dark:text-orange-300"
            title={`Esta llamada aparece ${node.repeat_count} veces en la traza con la misma entrada`}
          >
            ×{node.repeat_count}
          </span>
        )}

        <span
          className={`ml-2 shrink-0 rounded px-1.5 text-[11px] ring-1 ring-inset ${colors.chip}`}
        >
          {span.type}
        </span>
      </div>

      {/* Waterfall: dónde cae este paso dentro de la ejecución completa. */}
      <div className="relative h-4" title={formatDuration(span.duration_ms)}>
        <div className="absolute inset-y-1.5 left-0 right-0 rounded bg-slate-100 dark:bg-slate-800/60" />
        <div
          className={`absolute inset-y-1 rounded ${failed ? "bg-rose-500" : colors.bar}`}
          style={{ left: `${Math.min(offset, 99)}%`, width: `${Math.min(width, 100 - offset)}%` }}
        />
      </div>

      <span className="text-right font-mono text-xs tabular-nums text-slate-500 dark:text-slate-400">
        {tokens > 0 ? formatTokens(tokens) : ""}
      </span>
      <span
        className={`text-right font-mono text-xs tabular-nums ${
          cost > 0 ? "text-slate-700 dark:text-slate-200" : "text-slate-300 dark:text-slate-700"
        }`}
      >
        {cost > 0 ? formatCost(cost) : "—"}
      </span>
    </div>
  );
}
