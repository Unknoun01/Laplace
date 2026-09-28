"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { duration, exacto, money, number, pretty, tokens } from "@/lib/format";
import { allNodes, barGeometry, flatten, timeWindow, esEnvoltorio } from "@/lib/tree";
import type { Span, Trace, TraceTreeNode } from "@/lib/types";
import { t } from "@/lib/textos";

/**
 * El árbol de ejecución, navegable.
 *
 * Es la pantalla que justifica probar Laplace, así que tiene que aguantar una traza de
 * treinta pasos con bucles sin volverse ilegible: jerarquía plegable, barra de tiempo
 * proporcional, coste por rama y las repeticiones marcadas.
 */
export function TraceTree({ trace }: { trace: Trace }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(
    trace.roots[0]?.span.span_id ?? null,
  );
  const listRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => flatten(trace.roots, collapsed), [trace.roots, collapsed]);
  const index = useMemo(() => {
    const map = new Map<string, TraceTreeNode>();
    for (const node of allNodes(trace.roots)) map.set(node.span.span_id, node);
    return map;
  }, [trace.roots]);

  const selected = selectedId ? index.get(selectedId) ?? null : null;
  const window = useMemo(
    () => timeWindow(trace.summary.start_time, trace.summary.end_time),
    [trace.summary.start_time, trace.summary.end_time],
  );

  const toggle = useCallback((spanId: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(spanId)) next.delete(spanId);
      else next.add(spanId);
      return next;
    });
  }, []);

  // Con cincuenta pasos en pantalla, el ratón sobra.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      const at = rows.findIndex((row) => row.node.span.span_id === selectedId);
      if (event.key === "ArrowDown" || event.key === "j") {
        event.preventDefault();
        setSelectedId(rows[Math.min(at + 1, rows.length - 1)]?.node.span.span_id ?? null);
      } else if (event.key === "ArrowUp" || event.key === "k") {
        event.preventDefault();
        setSelectedId(rows[Math.max(at - 1, 0)]?.node.span.span_id ?? null);
      } else if (event.key === "ArrowRight" && selectedId) {
        setCollapsed((previous) => {
          const next = new Set(previous);
          next.delete(selectedId);
          return next;
        });
      } else if (event.key === "ArrowLeft" && selectedId) {
        const row = rows[at];
        if (row && row.node.children.length > 0 && !collapsed.has(selectedId)) {
          toggle(selectedId);
        } else {
          for (let i = at - 1; i >= 0; i -= 1) {
            if (rows[i].depth < (row?.depth ?? 0)) {
              setSelectedId(rows[i].node.span.span_id);
              break;
            }
          }
        }
      }
    };
    globalThis.addEventListener("keydown", onKeyDown);
    return () => globalThis.removeEventListener("keydown", onKeyDown);
  }, [rows, selectedId, collapsed, toggle]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const allOpen = collapsed.size === 0;
  const branches = useMemo(
    () => allNodes(trace.roots).filter((n) => n.children.length > 0),
    [trace.roots],
  );

  return (
    <div className="split">
      <section className="pane">
        <header>
          <b>{t("arbol.titulo")}</b>
          <span style={{ marginRight: "auto", marginLeft: 12 }}>
            {t("arbol.n_de", { n: rows.length, total: trace.summary.span_count })}
          </span>
          <span className="hint">{t("arbol.teclas")}</span>
          <button
            type="button"
            className="btn small"
            onClick={() =>
              setCollapsed(allOpen ? new Set(branches.map((n) => n.span.span_id)) : new Set())
            }
          >
            {allOpen ? t("arbol.plegar_todo") : t("arbol.desplegar_todo")}
          </button>
        </header>

        <div className="ruler" style={{ padding: "8px 12px 6px" }}>
          <span>0 s</span>
          <span>{duration(trace.summary.duration_ms / 2)}</span>
          <span>{duration(trace.summary.duration_ms)}</span>
        </div>

        <div className="scroll" ref={listRef}>
          {rows.map(({ node, depth }) => {
            const span = node.span;
            const failed = span.status === "error";
            const isCollapsed = collapsed.has(span.span_id);
            const { offset, width } = barGeometry(span.start_time, span.duration_ms, window);
            const cost = node.subtree.cost_usd;
            const tok = node.subtree.input_tokens + node.subtree.output_tokens;

            return (
              <div
                key={span.span_id}
                role="option"
                aria-selected={span.span_id === selectedId}
                tabIndex={-1}
                onClick={() => setSelectedId(span.span_id)}
                className={`node${node.repeat_count > 1 ? " flag" : ""}${failed ? " err" : ""}`}
              >
                <span className="node-left">
                  {Array.from({ length: depth }).map((_, i) => (
                    <span key={i} className="indent" />
                  ))}
                  <button
                    type="button"
                    className={`twist${node.children.length === 0 ? " leaf" : ""}`}
                    aria-expanded={!isCollapsed}
                    aria-label={isCollapsed ? t("arbol.desplegar") : t("arbol.plegar")}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggle(span.span_id);
                    }}
                  >
                    <svg viewBox="0 0 12 12" width="10" height="10">
                      <path d="M4 2.5 8 6l-4 3.5z" fill="currentColor" />
                    </svg>
                  </button>
                  <i className={`kind ${failed ? "err" : span.type}`} aria-hidden />
                  <span className="node-name">
                    {span.name}
                    {/* El nombre de un span de LLM ya es «chat <modelo>» por la
                        convención GenAI de OTel, así que añadirlo aparte lo escribía dos
                        veces en cada fila: «chat gpt-5.6-luna gpt-5.6-luna». Se enseña
                        sólo cuando aporta algo, que es cuando el nombre no lo lleva
                        —un span manual, o un agente que nombra sus pasos a mano. */}
                    {span.llm?.request_model && !span.name.includes(span.llm.request_model) && (
                      <small>{span.llm.request_model}</small>
                    )}
                  </span>
                  {node.children.length > 0 && isCollapsed && (
                    <span className="badge quiet">+{node.subtree.span_count - 1}</span>
                  )}
                  {node.repeat_count > 1 && (
                    <span className="badge" title={t("arbol.misma_llamada")}>
                      ×{node.repeat_count}
                    </span>
                  )}
                </span>
                <span className="node-bar" title={duration(span.duration_ms)}>
                  <i style={{ left: `${offset}%`, width: `${width}%` }} />
                </span>
                {esEnvoltorio(node) ? (
                  // Sólo envuelve a su hijo: sus cifras son las de la fila de debajo, y
                  // repetirlas se leía como el doble (D-160).
                  <>
                    <span className="node-tok igual" aria-hidden>
                      =
                    </span>
                    <span
                      className="node-cost igual"
                      title={t("arbol.envoltorio", { hijo: node.children[0].span.name })}
                    >
                      =<span className="sr">{t("arbol.envoltorio", { hijo: node.children[0].span.name })}</span>
                    </span>
                  </>
                ) : (
                  <>
                    <span className="node-tok">{tok > 0 ? tokens(tok) : ""}</span>
                    <span className="node-cost">{cost > 0 ? money(cost) : "—"}</span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="pane">
        {selected ? (
          <SpanPanel node={selected} />
        ) : (
          <div style={{ padding: 24, color: "var(--ink-3)" }}>{t("arbol.selecciona")}</div>
        )}
      </section>
    </div>
  );
}

/** Panel del paso seleccionado: siempre disponible, no escondido tras un desplegable. */
function SpanPanel({ node }: { node: TraceTreeNode }) {
  const span = node.span;

  return (
    <>
      <header>
        <b>{span.type}</b>
        <span
          style={{
            marginRight: "auto",
            marginLeft: 10,
            color: "var(--ink)",
            fontSize: 13.5,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {span.name}
        </span>
        <span className="hint pro" style={{ marginRight: 8 }}>
          {span.span_id}
        </span>
        <span className="hint">{duration(span.duration_ms)}</span>
      </header>

      <div className="scroll" style={{ padding: 14 }}>
        {span.status === "error" && span.status_message && (
          <p
            style={{
              margin: "0 0 14px",
              padding: "9px 12px",
              border: "1px solid var(--rose-line)",
              background: "var(--rose-bg)",
              borderRadius: "var(--r)",
              color: "var(--rose-ink)",
              fontSize: 14,
            }}
          >
            {span.status_message}
          </p>
        )}

        <Metrics node={node} />

        {span.llm && (
          <>
            <Block title={t("arbol.prompt", { n: span.llm.input_messages.length })}>
              <Messages messages={span.llm.input_messages} />
            </Block>
            <Block title={t("arbol.respuesta", { n: span.llm.output_messages.length })}>
              <Messages messages={span.llm.output_messages} />
            </Block>
          </>
        )}

        {span.tool && (
          <>
            <Block title={t("arbol.argumentos")}>
              <Payload value={span.tool.arguments} />
            </Block>
            <Block title={t("arbol.salida")}>
              <Payload value={span.tool.output} />
            </Block>
          </>
        )}

        {span.retrieval && (
          <>
            <Block title={t("arbol.consulta")}>
              <Payload value={span.retrieval.query} />
            </Block>
            <Block title={t("arbol.documentos", { n: span.retrieval.documents.length })}>
              <Payload value={span.retrieval.documents} />
            </Block>
          </>
        )}

        {!span.llm && !span.tool && !span.retrieval && (
          <>
            <Block title={t("arbol.entrada")}>
              <Payload value={span.input} />
            </Block>
            <Block title={t("arbol.salida")}>
              <Payload value={span.output} />
            </Block>
          </>
        )}

        {span.events.length > 0 && (
          <Block title={t("arbol.eventos", { n: span.events.length })}>
            {span.events.map((event, i) => (
              <dl className="kv" key={i}>
                <div>
                  <dt>{t("arbol.evento")}</dt>
                  <dd>{event.name}</dd>
                </div>
                {Object.entries(event.attributes).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{typeof value === "string" ? value : pretty(value)}</dd>
                  </div>
                ))}
              </dl>
            ))}
          </Block>
        )}

        <Attributes span={span} />
      </div>
    </>
  );
}

function Metrics({ node }: { node: TraceTreeNode }) {
  const llm = node.span.llm;
  if (llm) {
    return (
      <div className="metrics">
        <div>
          <b title={llm.request_model ?? ""}>{llm.request_model ?? "—"}</b>
          <small>{t("arbol.modelo")}</small>
        </div>
        <div className={llm.usage.estimated ? "warn" : undefined}>
          <b>{number(llm.usage.input_tokens)}</b>
          <small>{llm.usage.estimated ? t("arbol.tok_entrada_est") : t("arbol.tok_entrada")}</small>
        </div>
        <div className={llm.usage.estimated ? "warn" : undefined}>
          <b>{number(llm.usage.output_tokens)}</b>
          <small>{llm.usage.estimated ? t("arbol.tok_salida_est") : t("arbol.tok_salida")}</small>
        </div>
        <div
          className={
            llm.cost.unknown || llm.cost.rate_assumed || llm.cost.rate_unverified
              ? "warn"
              : undefined
          }
        >
          <b>
            {llm.cost.unknown ? "?" : money(llm.cost.total_usd, llm.cost.currency)}
            {/* Un «+» es un suelo, y sobre una tarifa sin verificar no se puede afirmar. */}
            {llm.cost.rate_assumed && !llm.cost.unknown && !llm.cost.rate_unverified && "+"}
          </b>
          <small title={llm.cost.rate_note || undefined}>
            {llm.cost.unknown
              ? t("arbol.sin_precio")
              : llm.cost.rate_unverified
                ? t("traza.coste.sin_verificar")
                : llm.cost.rate_assumed
                  ? t("arbol.coste_minimo")
                  : t("traza.coste")}
          </small>
        </div>
        {/* La caché sólo aparece cuando hay caché: sin ella, esto sería ruido. */}
        {llm.usage.cached_input_tokens > 0 && (
          <div className="pro">
            <b>{number(llm.usage.cached_input_tokens)}</b>
            <small>{t("arbol.desde_cache", { coste: money(llm.cost.cache_read_usd, llm.cost.currency) })}</small>
          </div>
        )}
        {llm.usage.cache_write_tokens + llm.usage.cache_write_1h_tokens > 0 && (
          <div className="pro">
            <b>{number(llm.usage.cache_write_tokens + llm.usage.cache_write_1h_tokens)}</b>
            <small>{t("arbol.escritos_cache", { coste: money(llm.cost.cache_write_usd, llm.cost.currency) })}</small>
          </div>
        )}
        {llm.cost.cache_saving_usd > 0 && (
          <div>
            <b>{money(llm.cost.cache_saving_usd, llm.cost.currency)}</b>
            <small>{t("arbol.ahorro_cache")}</small>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="metrics">
      <div>
        <b>{node.subtree.span_count}</b>
        <small>{t("arbol.rama.pasos")}</small>
      </div>
      <div>
        <b>{node.subtree.error_count}</b>
        <small>{t("arbol.rama.error")}</small>
      </div>
      <div>
        <b>{number(node.subtree.input_tokens + node.subtree.output_tokens)}</b>
        <small>{t("arbol.rama.tokens")}</small>
      </div>
      <div>
        <b>{money(node.subtree.cost_usd)}</b>
        <small>{t("arbol.rama.coste")}</small>
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="fold" open>
      <summary>{title}</summary>
      <div style={{ marginTop: 8 }}>{children}</div>
    </details>
  );
}

function Messages({ messages }: { messages: Record<string, unknown>[] }) {
  if (messages.length === 0) return <Empty />;
  return (
    <>
      {messages.map((message, index) => {
        const role = typeof message.role === "string" ? message.role : t("arbol.mensaje");
        const content = "content" in message ? message.content : message;
        const text = typeof content === "string" ? content : pretty(content);
        return (
          <div className="msg" key={index}>
            <header>
              <span>{role}</span>
              <Copy text={text} />
            </header>
            <pre>{text}</pre>
          </div>
        );
      })}
    </>
  );
}

function Payload({ value }: { value: unknown }) {
  const text = pretty(value);
  if (!text) return <Empty />;
  return (
    <div className="msg">
      <header>
        <span>{t("arbol.contenido")}</span>
        <Copy text={text} />
      </header>
      <pre>{text}</pre>
    </div>
  );
}

function Attributes({ span }: { span: Span }) {
  const rows: [string, string][] = [
    ["trace_id", span.trace_id],
    ["span_id", span.span_id],
    ["parent_span_id", span.parent_span_id ?? t("arbol.raiz")],
    ["laplace.span.type", span.type],
    ["laplace.dedup_hash", span.dedup_hash],
    ["laplace.step.key", span.step_key],
    ["laplace.step.label", span.step_label],
    ["start_time", span.start_time],
    ["end_time", span.end_time],
    ["duration_ms", exacto(span.duration_ms, 3)],
  ];
  if (span.llm) {
    rows.push([
      "laplace.usage.estimated",
      span.llm.usage.estimated ? t("arbol.estimados") : t("arbol.del_proveedor"),
    ]);
    if (span.llm.cost.rate) rows.push([t("tec.tarifa_aplicada"), span.llm.cost.rate]);
    if (span.llm.cost.unknown) rows.push([t("tec.tarifa_aplicada"), t("arbol.ninguna")]);
    if (span.llm.cost.rate_unverified) {
      rows.push([t("tec.tarifa_no_verificada"), span.llm.cost.rate_note]);
    } else if (span.llm.cost.rate_assumed) {
      rows.push([t("tec.tarifa_asumida"), span.llm.cost.rate_note]);
    }
    if (span.llm.usage.cached_input_tokens) {
      rows.push([
        "laplace.usage.cached_input_tokens",
        String(span.llm.usage.cached_input_tokens),
      ]);
    }
    if (span.llm.usage.cache_write_tokens) {
      rows.push(["laplace.usage.cache_write_tokens", String(span.llm.usage.cache_write_tokens)]);
    }
    if (span.llm.usage.cache_write_1h_tokens) {
      rows.push([
        "laplace.usage.cache_write_1h_tokens",
        String(span.llm.usage.cache_write_1h_tokens),
      ]);
    }
    if (span.llm.billing_tier && span.llm.billing_tier !== "standard") {
      rows.push(["laplace.billing.tier", span.llm.billing_tier]);
    }
    if (span.llm.billing_region && span.llm.billing_region !== "global") {
      rows.push(["laplace.billing.region", span.llm.billing_region]);
    }
  }
  if (span.session_id) rows.push(["laplace.session.id", span.session_id]);
  if (span.user_id) rows.push(["laplace.user.id", span.user_id]);
  if (span.llm?.system) rows.push(["gen_ai.system", span.llm.system]);
  if (span.llm?.request_model) rows.push(["gen_ai.request.model", span.llm.request_model]);
  if (span.llm?.response_model) rows.push(["gen_ai.response.model", span.llm.response_model]);
  if (span.llm?.finish_reasons.length) {
    rows.push(["gen_ai.response.finish_reasons", span.llm.finish_reasons.join(", ")]);
  }
  if (span.tool?.name) rows.push(["gen_ai.tool.name", span.tool.name]);
  for (const [key, value] of Object.entries(span.llm?.params ?? {})) {
    rows.push([`gen_ai.request.${key}`, String(value)]);
  }
  for (const [key, value] of Object.entries(span.attributes)) {
    rows.push([key, typeof value === "string" ? value : pretty(value)]);
  }

  return (
    <details className="fold pro">
      <summary>{t("arbol.atributos")}</summary>
      <dl className="kv">
        {rows.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function Empty() {
  return <p style={{ color: "var(--ink-3)", fontSize: 13, margin: 0 }}>{t("arbol.sin_contenido")}</p>;
}

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn small"
      style={{ padding: "1px 7px", fontSize: 11 }}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        });
      }}
    >
      {done ? t("arbol.copiado") : t("arbol.copiar")}
    </button>
  );
}
