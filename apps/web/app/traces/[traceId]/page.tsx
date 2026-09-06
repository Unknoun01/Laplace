import Link from "next/link";
import { notFound } from "next/navigation";
import { TraceExplorer } from "@/components/TraceExplorer";
import { getTrace } from "@/lib/api";
import { formatCost, formatDuration, formatNumber, formatTimestamp } from "@/lib/format";
import type { Diagnosis, TraceSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TracePage({ params }: { params: { traceId: string } }) {
  const trace = await getTrace(params.traceId);
  if (!trace) notFound();

  return (
    <div className="space-y-4">
      <Header summary={trace.summary} />
      {trace.diagnosis && <DiagnosisBanner diagnosis={trace.diagnosis} />}
      <TraceExplorer trace={trace} />
    </div>
  );
}

function Header({ summary }: { summary: TraceSummary }) {
  const failed = summary.status === "error";
  return (
    <div>
      <Link
        href="/"
        className="text-sm text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
      >
        ← Trazas
      </Link>

      <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <span className={`h-2.5 w-2.5 rounded-full ${failed ? "bg-rose-500" : "bg-emerald-500"}`} />
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {summary.root_name || "(sin nombre)"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
            <span className="font-mono">{summary.trace_id}</span>
            <span>·</span>
            <span>{summary.project_id}</span>
            <span>·</span>
            <span>{formatTimestamp(summary.start_time)}</span>
            {summary.session_id && (
              <>
                <span>·</span>
                <Link
                  href={`/?session=${encodeURIComponent(summary.session_id)}`}
                  className="underline decoration-dotted underline-offset-2 hover:text-slate-900 dark:hover:text-slate-100"
                >
                  sesión {summary.session_id}
                </Link>
              </>
            )}
            {summary.user_id && (
              <>
                <span>·</span>
                <span>usuario {summary.user_id}</span>
              </>
            )}
          </div>
        </div>

        <dl className="flex flex-wrap items-center gap-5 text-sm">
          <Metric label="coste" value={formatCost(summary.cost.total_usd)} emphasis />
          <Metric
            label="tokens"
            value={`${formatNumber(summary.usage.input_tokens)} / ${formatNumber(summary.usage.output_tokens)}`}
            hint="entrada / salida"
          />
          <Metric label="duración" value={formatDuration(summary.duration_ms)} />
          <Metric label="pasos" value={String(summary.span_count)} />
          <Metric label="llm" value={String(summary.llm_call_count)} />
          <Metric label="tools" value={String(summary.tool_call_count)} />
          {summary.error_count > 0 && (
            <Metric label="errores" value={String(summary.error_count)} tone="text-rose-600 dark:text-rose-400" />
          )}
        </dl>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  tone,
  emphasis,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="text-right" title={hint}>
      <dd
        className={`font-mono tabular-nums ${emphasis ? "text-lg font-semibold" : "text-base"} ${tone ?? ""}`}
      >
        {value}
      </dd>
      <dt className="text-[11px] text-slate-500 dark:text-slate-400">{hint ?? label}</dt>
    </div>
  );
}

/**
 * Diagnóstico automático (Fase 3). El hueco existe ya en el contrato y en esta pantalla;
 * hoy la API siempre devuelve `null`, así que este bloque no se pinta todavía.
 */
function DiagnosisBanner({ diagnosis }: { diagnosis: Diagnosis }) {
  return (
    <div className="rounded-lg border border-sky-200 bg-sky-50 p-4 dark:border-sky-500/30 dark:bg-sky-500/5">
      <div className="flex items-baseline gap-2">
        <h2 className="font-medium">Causa probable</h2>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          diagnosticado con {diagnosis.model}
        </span>
      </div>
      <p className="mt-1 text-sm">{diagnosis.cause}</p>
      {diagnosis.suggestion && (
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          <span className="font-medium">Sugerencia: </span>
          {diagnosis.suggestion}
        </p>
      )}
    </div>
  );
}
