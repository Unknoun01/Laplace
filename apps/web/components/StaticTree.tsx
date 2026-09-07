import { duration, money, tokens } from "@/lib/format";
import { barGeometry, flatten, timeWindow } from "@/lib/tree";
import type { Trace } from "@/lib/types";
import { KindDot } from "./pieces";

/**
 * El árbol de una traza, sin interacción, para incrustarlo en la ficha de un problema.
 * El árbol navegable vive en su propia pantalla; aquí sólo hace falta ver dónde cae el
 * problema dentro de la ejecución.
 */
export function StaticTree({ trace, highlight }: { trace: Trace; highlight?: string }) {
  const window = timeWindow(trace.summary.start_time, trace.summary.end_time);
  const rows = flatten(trace.roots);
  const total = trace.summary.duration_ms;

  return (
    <>
      <div className="ruler">
        <span>0 s</span>
        <span>{duration(total / 2)}</span>
        <span>{duration(total)}</span>
      </div>
      {rows.map(({ node, depth }) => {
        const span = node.span;
        const failed = span.status === "error";
        const flagged = Boolean(highlight) && span.dedup_hash === highlight;
        const { offset, width } = barGeometry(span.start_time, span.duration_ms, window);
        const cost = node.subtree.cost_usd;
        const tok = node.subtree.input_tokens + node.subtree.output_tokens;

        return (
          <div
            key={span.span_id}
            className={`node${flagged ? " flag" : ""}${failed ? " err" : ""}`}
            style={{ cursor: "default" }}
          >
            <span className="node-left">
              {Array.from({ length: depth }).map((_, i) => (
                <span key={i} className="indent" />
              ))}
              <KindDot type={span.type} failed={failed} />
              <span className="node-name">
                {span.name}
                {span.llm?.request_model && <small>{span.llm.request_model}</small>}
              </span>
              {node.repeat_count > 1 && (
                <span className="badge" title="Misma llamada, misma entrada">
                  ×{node.repeat_count}
                </span>
              )}
            </span>
            <span className="node-bar" title={duration(span.duration_ms)}>
              <i style={{ left: `${offset}%`, width: `${width}%` }} />
            </span>
            <span className="node-tok">{tok > 0 ? tokens(tok) : ""}</span>
            <span className="node-cost">{cost > 0 ? money(cost) : "—"}</span>
          </div>
        );
      })}
    </>
  );
}
