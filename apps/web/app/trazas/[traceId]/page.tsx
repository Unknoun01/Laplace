import Link from "next/link";
import { TraceTree } from "@/components/TraceTree";
import { BackendDown, NotFound } from "@/components/states";
import { backendReachable, getTrace, parseDays } from "@/lib/api";
import { duration, money, number, timestamp } from "@/lib/format";
import { allNodes } from "@/lib/tree";
import type { TraceSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

interface PageProps {
  params: { traceId: string };
  searchParams: { project?: string; days?: string };
}

/**
 * Una traza, entera y navegable. Se llega desde el explorador o desde la ficha de un
 * problema, y desde aquí se vuelve al problema: observar y diagnosticar conectados en
 * los dos sentidos.
 */
export default async function TrazaPage({ params, searchParams }: PageProps) {
  if (!(await backendReachable())) {
    return <BackendDown apiUrl={process.env.LAPLACE_API_URL ?? "http://localhost:8000"} />;
  }

  const trace = await getTrace(params.traceId);
  const days = parseDays(searchParams.days);
  const project = searchParams.project ?? trace?.summary.project_id ?? "";
  const context = `project=${encodeURIComponent(project)}&days=${days}`;

  if (!trace) {
    return (
      <NotFound
        title="No encontramos esa traza"
        body="Puede que el identificador esté mal, o que la traza sea anterior a lo que se conserva."
        back={`/trazas?${context}`}
      />
    );
  }

  // Si esta ejecución repite pasos, enlazamos al problema que los explica.
  const looping = allNodes(trace.roots).find((node) => node.repeat_count >= 3);
  const findingId = looping ? `repeticion:${looping.span.dedup_hash}` : null;

  return (
    <main>
      <Link href={`/trazas?${context}`} className="back">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
          <path
            d="M8.5 3 L4.5 7 L8.5 11"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Todas las trazas
      </Link>

      <Head summary={trace.summary} />

      {(findingId || trace.diagnosis) && (
        <div
          style={{
            border: "1px solid #33407a",
            background: "#111631",
            borderRadius: "var(--r)",
            padding: "12px 16px",
            marginBottom: 16,
            fontSize: 14,
          }}
        >
          {trace.diagnosis ? (
            <>
              <strong>{trace.diagnosis.cause}</strong>
              {trace.diagnosis.suggestion && <> — {trace.diagnosis.suggestion}</>}
            </>
          ) : (
            <>
              Esta ejecución repite pasos con la misma entrada.{" "}
              <Link href={`/problemas/${encodeURIComponent(findingId!)}?${context}`}>
                Ver qué cuesta y cómo arreglarlo →
              </Link>
            </>
          )}
        </div>
      )}

      <TraceTree trace={trace} />

      <div className="actions">
        <a
          className="btn pro inline"
          href={`/api/export/${trace.summary.trace_id}`}
          download={`${trace.summary.trace_id}.json`}
        >
          Exportar traza en JSON
        </a>
      </div>
    </main>
  );
}

function Head({ summary }: { summary: TraceSummary }) {
  const failed = summary.status === "error";
  return (
    <div className="d-head" style={{ paddingBottom: 18, marginBottom: 16 }}>
      <h1 style={{ fontSize: 22, maxWidth: "none" }}>
        <i className={`dot ${failed ? "error" : "ok"}`} style={{ marginRight: 10 }} aria-hidden />
        {summary.root_name || "(sin nombre)"}
      </h1>
      <div className="d-sub" style={{ marginBottom: 12 }}>
        {summary.trace_id} · {summary.project_id} · {timestamp(summary.start_time)}
        {summary.session_id ? ` · sesión ${summary.session_id}` : ""}
        {summary.user_id ? ` · usuario ${summary.user_id}` : ""}
      </div>
      <div className="d-cost">
        <div>
          <b className="num">{money(summary.cost.total_usd, summary.cost.currency)}</b>
          <span>coste</span>
        </div>
        <div>
          <b className="num neutral">
            {number(summary.usage.input_tokens)} / {number(summary.usage.output_tokens)}
          </b>
          <span>tokens entrada / salida</span>
        </div>
        <div>
          <b className="num neutral">{duration(summary.duration_ms)}</b>
          <span>duración</span>
        </div>
        <div>
          <b className="num neutral">{summary.span_count}</b>
          <span>pasos</span>
        </div>
        <div>
          <b className="num neutral">{summary.llm_call_count}</b>
          <span>llamadas a modelo</span>
        </div>
        <div>
          <b className="num neutral">{summary.tool_call_count}</b>
          <span>herramientas</span>
        </div>
        {summary.error_count > 0 && (
          <div>
            <b className="num" style={{ color: "var(--rose)" }}>
              {summary.error_count}
            </b>
            <span>errores</span>
          </div>
        )}
      </div>
    </div>
  );
}
