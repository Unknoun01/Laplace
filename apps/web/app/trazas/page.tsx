import Link from "next/link";
import { BackendDown, NoProject } from "@/components/states";
import {
  backendReachable,
  listProjects,
  listTraces,
  parseDays,
  windowStart,
} from "@/lib/api";
import { duration, money, relative, timestamp, tokens } from "@/lib/format";
import type { TraceSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: {
    project?: string;
    days?: string;
    q?: string;
    status?: string;
    type?: string;
    sort?: string;
    cursor?: string;
  };
}

const SORTS = [
  { value: "cost", label: "Más caras primero" },
  { value: "recent", label: "Más recientes" },
  { value: "duration", label: "Más lentas" },
];

const TYPES = [
  { value: "", label: "Cualquier paso" },
  { value: "llm", label: "Con llamada a modelo" },
  { value: "tool", label: "Con herramienta" },
  { value: "retrieval", label: "Con búsqueda" },
];

/**
 * Explorador de trazas: la superficie de exploración libre.
 *
 * No está escondida detrás de un diagnóstico. Un desarrollador entra aquí, filtra y
 * abre la traza que quiera, tenga o no un problema detectado.
 */
export default async function TrazasPage({ searchParams }: PageProps) {
  if (!(await backendReachable())) {
    return <BackendDown apiUrl={process.env.LAPLACE_API_URL ?? "http://localhost:8000"} />;
  }

  const projects = await listProjects();
  if (projects.length === 0) return <NoProject />;

  const project = projects.find((p) => p.id === searchParams.project)?.id ?? projects[0].id;
  const days = parseDays(searchParams.days);
  const sort = SORTS.some((s) => s.value === searchParams.sort) ? searchParams.sort! : "cost";

  const page = await listTraces({
    project_id: project,
    since: windowStart(days),
    search: searchParams.q,
    status: searchParams.status,
    span_type: searchParams.type,
    sort,
    cursor: searchParams.cursor,
  });

  const repeats = new Set(page.with_repeats);
  const context = `project=${encodeURIComponent(project)}&days=${days}`;

  return (
    <main>
      <section className="sec" style={{ paddingBottom: 0 }}>
        <h2>Trazas</h2>
        <p className="lead">
          Cada fila es una ejecución completa de tu agente. Ordenadas por lo que cuestan.
        </p>
      </section>

      <form method="GET" className="toolbar">
        <input type="hidden" name="project" value={project} />
        <input type="hidden" name="days" value={days} />
        <input
          type="search"
          name="q"
          defaultValue={searchParams.q ?? ""}
          placeholder="Buscar por paso, herramienta o id de traza"
          className="field grow"
          aria-label="Buscar"
        />
        <select
          name="status"
          defaultValue={searchParams.status ?? ""}
          className="field"
          aria-label="Estado"
        >
          <option value="">Cualquier estado</option>
          <option value="error">Sólo con error</option>
          <option value="ok">Sólo correctas</option>
        </select>
        <select
          name="type"
          defaultValue={searchParams.type ?? ""}
          className="field"
          aria-label="Tipo de paso"
        >
          {TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <select name="sort" defaultValue={sort} className="field" aria-label="Orden">
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <button type="submit" className="btn">
          Filtrar
        </button>
      </form>

      {page.traces.length === 0 ? (
        <div className="state">
          <h2>Ninguna traza coincide</h2>
          <p>
            Prueba a quitar filtros o a ampliar el rango temporal en la barra de arriba.
          </p>
          <div className="actions">
            <Link href={`/trazas?${context}`} className="btn">
              Quitar filtros
            </Link>
          </div>
        </div>
      ) : (
        <>
          <table className="tbl">
            <thead>
              <tr>
                <th>Traza</th>
                <th className="r hide-sm">Pasos</th>
                <th className="r hide-sm">Tokens</th>
                <th className="r">Coste</th>
                <th className="r hide-sm">Duración</th>
                <th className="r hide-sm">Cuándo</th>
              </tr>
            </thead>
            <tbody>
              {page.traces.map((trace) => (
                <Row
                  key={trace.trace_id}
                  trace={trace}
                  context={context}
                  looping={repeats.has(trace.trace_id)}
                />
              ))}
            </tbody>
          </table>

          <div className="pager">
            <span style={{ color: "var(--ink-3)" }}>
              {page.traces.length} trazas
              {page.next_cursor ? "" : " (todas las del rango)"}
            </span>
            {page.next_cursor && (
              <Link
                className="btn small"
                href={`/trazas?${context}&sort=${sort}&cursor=${encodeURIComponent(page.next_cursor)}`}
              >
                Más antiguas →
              </Link>
            )}
          </div>
        </>
      )}
    </main>
  );
}

function Row({
  trace,
  context,
  looping,
}: {
  trace: TraceSummary;
  context: string;
  looping: boolean;
}) {
  const failed = trace.status === "error";
  return (
    <tr>
      <td>
        <Link href={`/trazas/${trace.trace_id}?${context}`}>
          <span>
            <i className={`dot ${failed ? "error" : "ok"}`} aria-label={failed ? "con error" : "ok"} />
            {trace.root_name || "(sin nombre)"}
            {looping && (
              <span className="badge" style={{ marginLeft: 9 }} title="Repite pasos con la misma entrada">
                bucle
              </span>
            )}
            {failed && (
              <span
                className="badge"
                style={{ marginLeft: 9, background: "#1d0f11", color: "var(--rose)", borderColor: "#5c2f35" }}
              >
                {trace.error_count} error{trace.error_count > 1 ? "es" : ""}
              </span>
            )}
          </span>
          <div className="meta">
            {trace.trace_id.slice(0, 12)}
            {trace.session_id ? ` · sesión ${trace.session_id}` : ""}
          </div>
        </Link>
      </td>
      <td className="r hide-sm">{trace.span_count}</td>
      <td className="r hide-sm">{tokens(trace.usage.input_tokens + trace.usage.output_tokens)}</td>
      <td className="r money">{money(trace.cost.total_usd, trace.cost.currency)}</td>
      <td className="r hide-sm">{duration(trace.duration_ms)}</td>
      <td className="r hide-sm" title={timestamp(trace.start_time)}>
        {relative(trace.start_time)}
      </td>
    </tr>
  );
}
