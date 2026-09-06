import Link from "next/link";
import { backendReachable, listProjects, listTraces } from "@/lib/api";
import {
  formatCost,
  formatDuration,
  formatRelative,
  formatTokens,
  spanColors,
} from "@/lib/format";
import type { ProjectStats, TraceSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: {
    project?: string;
    q?: string;
    status?: string;
    session?: string;
    cursor?: string;
  };
}

export default async function TraceListPage({ searchParams }: PageProps) {
  const online = await backendReachable();
  if (!online) return <BackendDown />;

  const [projects, page] = await Promise.all([
    listProjects(),
    listTraces({
      project_id: searchParams.project,
      search: searchParams.q,
      status: searchParams.status,
      session_id: searchParams.session,
      cursor: searchParams.cursor,
    }),
  ]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Trazas</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Cada fila es una ejecución completa del agente ante una petición.
          </p>
        </div>
        <Totals traces={page.traces} projects={projects} />
      </div>

      <Filters projects={projects} searchParams={searchParams} />

      {page.traces.length === 0 ? (
        <Empty
          filtered={Boolean(
            searchParams.q || searchParams.status || searchParams.project || searchParams.cursor,
          )}
        />
      ) : (
        <>
          <TraceTable traces={page.traces} />
          <Pagination searchParams={searchParams} nextCursor={page.next_cursor} />
        </>
      )}
    </div>
  );
}

function Pagination({
  searchParams,
  nextCursor,
}: {
  searchParams: PageProps["searchParams"];
  nextCursor: string | null;
}) {
  if (!nextCursor && !searchParams.cursor) return null;

  // El cursor va en la URL para que la página sea enlazable y funcione sin JavaScript.
  const next = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    if (value && key !== "cursor") next.set(key, value);
  }
  const first = new URLSearchParams(next);
  if (nextCursor) next.set("cursor", nextCursor);

  return (
    <div className="flex items-center justify-between text-sm">
      {searchParams.cursor ? (
        <Link
          href={`/?${first.toString()}`}
          className="text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          ← Primera página
        </Link>
      ) : (
        <span />
      )}
      {nextCursor && (
        <Link
          href={`/?${next.toString()}`}
          className="rounded-md border border-slate-200 px-3 py-1.5 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-900"
        >
          Trazas más antiguas →
        </Link>
      )}
    </div>
  );
}

function Totals({ traces, projects }: { traces: TraceSummary[]; projects: ProjectStats[] }) {
  const cost = traces.reduce((sum, trace) => sum + trace.cost.total_usd, 0);
  const errors = traces.filter((trace) => trace.status === "error").length;
  const allTime = projects.reduce((sum, project) => sum + project.total_cost_usd, 0);

  return (
    <dl className="flex items-center gap-6 text-sm">
      <Stat label="en esta página" value={formatCost(cost)} />
      <Stat label="acumulado" value={formatCost(allTime)} />
      <Stat
        label="con error"
        value={String(errors)}
        tone={errors > 0 ? "text-rose-600 dark:text-rose-400" : undefined}
      />
    </dl>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="text-right">
      <dd className={`font-mono text-base font-medium tabular-nums ${tone ?? ""}`}>{value}</dd>
      <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
    </div>
  );
}

function Filters({
  projects,
  searchParams,
}: {
  projects: ProjectStats[];
  searchParams: PageProps["searchParams"];
}) {
  return (
    <form
      method="GET"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/60 p-2 dark:border-slate-800 dark:bg-slate-900/40"
    >
      <input
        type="search"
        name="q"
        defaultValue={searchParams.q ?? ""}
        placeholder="Buscar por nombre de paso o id de traza"
        className="h-9 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm outline-none placeholder:text-slate-400 focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950"
      />
      <select
        name="project"
        defaultValue={searchParams.project ?? ""}
        className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-950"
      >
        <option value="">Todos los proyectos</option>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.id}
          </option>
        ))}
      </select>
      <select
        name="status"
        defaultValue={searchParams.status ?? ""}
        className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-950"
      >
        <option value="">Todos los estados</option>
        <option value="error">Sólo con error</option>
      </select>
      <button
        type="submit"
        className="h-9 rounded-md bg-slate-900 px-3.5 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
      >
        Filtrar
      </button>
    </form>
  );
}

function TraceTable({ traces }: { traces: TraceSummary[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
      <div className="grid grid-cols-[minmax(0,1fr)_repeat(5,auto)] items-center gap-x-6 border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-400">
        <span>Traza</span>
        <span className="text-right">Pasos</span>
        <span className="text-right">Tokens</span>
        <span className="text-right">Coste</span>
        <span className="text-right">Duración</span>
        <span className="text-right">Cuándo</span>
      </div>
      <ul className="divide-y divide-slate-100 dark:divide-slate-800/70">
        {traces.map((trace) => (
          <TraceRow key={trace.trace_id} trace={trace} />
        ))}
      </ul>
    </div>
  );
}

function TraceRow({ trace }: { trace: TraceSummary }) {
  const failed = trace.status === "error";
  return (
    <li>
      <Link
        href={`/traces/${trace.trace_id}`}
        className="grid grid-cols-[minmax(0,1fr)_repeat(5,auto)] items-center gap-x-6 px-4 py-3 transition-colors hover:bg-sky-50/60 dark:hover:bg-sky-500/5"
      >
        <div className="flex min-w-0 items-center gap-3">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${failed ? "bg-rose-500" : "bg-emerald-500"}`}
            title={failed ? "con error" : "ok"}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate font-medium">{trace.root_name || "(sin nombre)"}</span>
              {failed && (
                <span className="rounded bg-rose-50 px-1.5 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300">
                  {trace.error_count} error{trace.error_count > 1 ? "es" : ""}
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <span className="font-mono">{trace.trace_id.slice(0, 12)}</span>
              <span className="text-slate-300 dark:text-slate-700">·</span>
              <span>{trace.project_id}</span>
              {trace.session_id && (
                <>
                  <span className="text-slate-300 dark:text-slate-700">·</span>
                  <span>sesión {trace.session_id}</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 text-xs">
          <Count type="llm" value={trace.llm_call_count} />
          <Count type="tool" value={trace.tool_call_count} />
          <span className="ml-1 tabular-nums text-slate-500 dark:text-slate-400">
            {trace.span_count}
          </span>
        </div>

        <span className="text-right font-mono text-sm tabular-nums text-slate-600 dark:text-slate-300">
          {formatTokens(trace.usage.input_tokens + trace.usage.output_tokens)}
        </span>
        <span className="text-right font-mono text-sm tabular-nums">
          {formatCost(trace.cost.total_usd)}
        </span>
        <span className="text-right font-mono text-sm tabular-nums text-slate-600 dark:text-slate-300">
          {formatDuration(trace.duration_ms)}
        </span>
        <span
          className="w-24 text-right text-sm text-slate-500 dark:text-slate-400"
          title={trace.start_time}
        >
          {formatRelative(trace.start_time)}
        </span>
      </Link>
    </li>
  );
}

function Count({ type, value }: { type: string; value: number }) {
  if (!value) return null;
  const colors = spanColors(type);
  return (
    <span
      className={`rounded px-1.5 py-0.5 font-mono text-[11px] ring-1 ring-inset ${colors.chip}`}
      title={`${value} ${type}`}
    >
      {value} {type}
    </span>
  );
}

function Empty({ filtered }: { filtered: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-12 text-center dark:border-slate-700">
      <p className="font-medium">
        {filtered ? "Ninguna traza coincide con el filtro." : "Todavía no hay trazas."}
      </p>
      {!filtered && (
        <div className="mx-auto mt-4 max-w-lg text-left">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Instrumenta tu agente con una línea y vuelve a esta página:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-md bg-slate-900 p-4 text-xs leading-relaxed text-slate-100">
            {`import laplace\nlaplace.init(project="mi-agente")`}
          </pre>
          <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
            O lanza el agente de ejemplo:{" "}
            <code className="font-mono text-xs">python examples/agente_ejemplo.py</code>
          </p>
        </div>
      )}
    </div>
  );
}

function BackendDown() {
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-8 dark:border-amber-500/30 dark:bg-amber-500/5">
      <h1 className="font-semibold">El backend no responde</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600 dark:text-slate-300">
        No se ha podido contactar con <code className="font-mono">{process.env.LAPLACE_API_URL ?? "http://localhost:8000"}</code>.
        Levanta la infraestructura con <code className="font-mono">docker compose up</code>, o
        arranca el backend a mano con{" "}
        <code className="font-mono">uvicorn laplace_backend.main:app --port 8000</code>.
      </p>
    </div>
  );
}
