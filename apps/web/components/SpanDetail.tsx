"use client";

import { useState } from "react";
import { formatCost, formatDuration, formatNumber, pretty, spanColors } from "@/lib/format";
import type { Span, TraceTreeNode } from "@/lib/types";

export function SpanDetail({ node }: { node: TraceTreeNode }) {
  const span = node.span;
  const colors = spanColors(span.type);

  return (
    <div className="flex max-h-[calc(100vh-19rem)] flex-col">
      <header className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded px-1.5 py-0.5 text-[11px] ring-1 ring-inset ${colors.chip}`}>
            {span.type}
          </span>
          <h2 className="truncate font-medium">{span.name}</h2>
          {span.status === "error" && (
            <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 ring-1 ring-inset ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300">
              error
            </span>
          )}
          {node.repeat_count > 1 && (
            <span className="rounded bg-orange-100 px-1.5 py-0.5 text-[11px] font-medium text-orange-700 dark:bg-orange-500/15 dark:text-orange-300">
              se repite {node.repeat_count} veces
            </span>
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-slate-500 dark:text-slate-400">
          <span>{span.span_id}</span>
          <span>·</span>
          <span>{new Date(span.start_time).toLocaleTimeString("es-ES")}</span>
          <span>·</span>
          <span>{formatDuration(span.duration_ms)}</span>
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-auto p-4">
        {span.status === "error" && span.status_message && (
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
            {span.status_message}
          </div>
        )}

        <Metrics node={node} />

        {span.llm && (
          <>
            <Section title="Prompt" count={span.llm.input_messages.length}>
              <Messages messages={span.llm.input_messages} />
            </Section>
            <Section title="Respuesta" count={span.llm.output_messages.length}>
              <Messages messages={span.llm.output_messages} />
            </Section>
            {Object.keys(span.llm.params).length > 0 && (
              <Section title="Parámetros">
                <KeyValues data={span.llm.params} />
              </Section>
            )}
          </>
        )}

        {span.tool && (
          <>
            <Section title="Argumentos">
              <Payload value={span.tool.arguments} />
            </Section>
            <Section title="Salida">
              <Payload value={span.tool.output} />
            </Section>
          </>
        )}

        {span.retrieval && (
          <>
            <Section title="Consulta">
              <Payload value={span.retrieval.query} />
            </Section>
            <Section title="Documentos" count={span.retrieval.documents.length}>
              <Payload value={span.retrieval.documents} />
            </Section>
          </>
        )}

        {!span.llm && !span.tool && !span.retrieval && (
          <>
            <Section title="Entrada">
              <Payload value={span.input} />
            </Section>
            <Section title="Salida">
              <Payload value={span.output} />
            </Section>
          </>
        )}

        {span.events.length > 0 && (
          <Section title="Eventos" count={span.events.length} defaultOpen>
            <div className="space-y-2">
              {span.events.map((event, index) => (
                <div
                  key={`${event.name}-${index}`}
                  className="rounded-md border border-slate-200 p-2 dark:border-slate-800"
                >
                  <div className="font-mono text-xs font-medium">{event.name}</div>
                  <KeyValues data={event.attributes} />
                </div>
              ))}
            </div>
          </Section>
        )}

        <Meta span={span} />
      </div>
    </div>
  );
}

function Metrics({ node }: { node: TraceTreeNode }) {
  const span = node.span;
  const llm = span.llm;

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {llm ? (
        <>
          <Metric label="modelo" value={llm.request_model ?? "—"} mono />
          <Metric label="tokens entrada" value={formatNumber(llm.usage.input_tokens)} />
          <Metric label="tokens salida" value={formatNumber(llm.usage.output_tokens)} />
          <Metric
            label={llm.cost.estimated ? "coste (sin tarifa)" : "coste"}
            value={formatCost(llm.cost.total_usd)}
            hint={
              llm.cost.estimated
                ? "El modelo no está en la tabla de precios: no se puede calcular."
                : `entrada ${formatCost(llm.cost.input_usd)} · salida ${formatCost(llm.cost.output_usd)}`
            }
            warn={llm.cost.estimated}
          />
        </>
      ) : (
        <>
          <Metric label="pasos del subárbol" value={String(node.subtree.span_count)} />
          <Metric label="llamadas con error" value={String(node.subtree.error_count)} />
          <Metric
            label="tokens del subárbol"
            value={formatNumber(node.subtree.input_tokens + node.subtree.output_tokens)}
          />
          <Metric label="coste del subárbol" value={formatCost(node.subtree.cost_usd)} />
        </>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  mono,
  warn,
}: {
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div
      title={hint}
      className={`rounded-md border px-2.5 py-1.5 ${
        warn
          ? "border-amber-300 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10"
          : "border-slate-200 bg-slate-50/60 dark:border-slate-800 dark:bg-slate-900/40"
      }`}
    >
      <div className={`truncate text-sm tabular-nums ${mono ? "font-mono text-xs" : "font-medium"}`}>
        {value}
      </div>
      <div className="mt-0.5 truncate text-[11px] text-slate-500 dark:text-slate-400">{label}</div>
    </div>
  );
}

function Section({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <svg viewBox="0 0 12 12" className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`}>
          <path d="M4 2.5 8 6l-4 3.5z" fill="currentColor" />
        </svg>
        {title}
        {count !== undefined && <span className="font-normal normal-case">({count})</span>}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

function Messages({ messages }: { messages: Record<string, unknown>[] }) {
  if (messages.length === 0) return <Nothing />;
  return (
    <div className="space-y-2">
      {messages.map((message, index) => {
        const role = typeof message.role === "string" ? message.role : "mensaje";
        const content = "content" in message ? message.content : message;
        return (
          <div
            key={index}
            className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-800"
          >
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-2 py-1 dark:border-slate-800 dark:bg-slate-900/50">
              <span className="font-mono text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {role}
              </span>
              <CopyButton text={typeof content === "string" ? content : pretty(content)} />
            </div>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-mono text-xs leading-relaxed">
              {typeof content === "string" ? content : pretty(content)}
            </pre>
          </div>
        );
      })}
    </div>
  );
}

function Payload({ value }: { value: unknown }) {
  const text = pretty(value);
  if (!text) return <Nothing />;
  return (
    <div className="relative overflow-hidden rounded-md border border-slate-200 dark:border-slate-800">
      <div className="absolute right-1.5 top-1.5">
        <CopyButton text={text} />
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 pr-16 font-mono text-xs leading-relaxed">
        {text}
      </pre>
    </div>
  );
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <Nothing />;
  return (
    <dl className="divide-y divide-slate-100 rounded-md border border-slate-200 text-xs dark:divide-slate-800 dark:border-slate-800">
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[10rem_minmax(0,1fr)] gap-2 px-2.5 py-1.5">
          <dt className="truncate font-mono text-slate-500 dark:text-slate-400">{key}</dt>
          <dd className="break-words font-mono">
            {typeof value === "string" ? value : pretty(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Meta({ span }: { span: Span }) {
  const meta: Record<string, unknown> = {
    trace_id: span.trace_id,
    span_id: span.span_id,
    parent_span_id: span.parent_span_id ?? "(raíz)",
    proyecto: span.project_id,
    inicio: span.start_time,
    fin: span.end_time,
    ...(span.session_id ? { sesión: span.session_id } : {}),
    ...(span.user_id ? { usuario: span.user_id } : {}),
    ...(span.llm?.system ? { proveedor: span.llm.system } : {}),
    ...(span.llm?.response_model ? { modelo_respuesta: span.llm.response_model } : {}),
    ...(span.llm?.finish_reasons.length ? { finish_reason: span.llm.finish_reasons.join(", ") } : {}),
    // Sirve para reconocer llamadas repetidas: mismo hash = misma llamada.
    dedup_hash: span.dedup_hash,
    ...span.metadata,
    ...span.attributes,
  };
  return (
    <Section title="Detalles" defaultOpen={false}>
      <KeyValues data={meta} />
    </Section>
  );
}

function Nothing() {
  return <p className="text-xs italic text-slate-400 dark:text-slate-600">Sin contenido.</p>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className="rounded border border-slate-200 bg-white/70 px-1.5 py-0.5 text-[11px] text-slate-500 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-900/70 dark:hover:text-slate-100"
    >
      {copied ? "copiado" : "copiar"}
    </button>
  );
}
