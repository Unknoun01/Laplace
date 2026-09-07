import type { TraceTreeNode } from "./types";

export interface Row {
  node: TraceTreeNode;
  depth: number;
}

/** Aplana el árbol a filas, saltándose los subárboles plegados. */
export function flatten(
  nodes: TraceTreeNode[],
  collapsed: Set<string> = new Set(),
  depth = 0,
): Row[] {
  const rows: Row[] = [];
  for (const node of nodes) {
    rows.push({ node, depth });
    if (node.children.length > 0 && !collapsed.has(node.span.span_id)) {
      rows.push(...flatten(node.children, collapsed, depth + 1));
    }
  }
  return rows;
}

/** Ventana temporal de la traza: es el eje del waterfall. */
export function timeWindow(startIso: string, endIso: string) {
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  return { start, span: Math.max(end - start, 1) };
}

/** Posición y anchura de la barra de un span dentro de esa ventana, en %. */
export function barGeometry(
  startIso: string,
  durationMs: number,
  window: { start: number; span: number },
) {
  const offset = Math.min(
    Math.max(((new Date(startIso).getTime() - window.start) / window.span) * 100, 0),
    99,
  );
  const width = Math.max(Math.min((durationMs / window.span) * 100, 100 - offset), 0.6);
  return { offset, width };
}

export function allNodes(nodes: TraceTreeNode[]): TraceTreeNode[] {
  return nodes.flatMap((node) => [node, ...allNodes(node.children)]);
}
