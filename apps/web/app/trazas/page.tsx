"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BackendDown, NoProject, TableSkeleton } from "@/components/states";
import { getOverview, listProjects, listTraces, parseDays, windowStart } from "@/lib/api";
import { duration, money, relative, timestamp, tokens } from "@/lib/format";
import type { TraceListPage, TraceSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { LIVE_INTERVAL_MS, type Live, useLive } from "@/lib/useLive";

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
function Contenido() {
  const router = useRouter();
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  const days = parseDays(params.get("days") ?? undefined);
  const sort = SORTS.some((s) => s.value === params.get("sort")) ? params.get("sort")! : "cost";
  const q = params.get("q") ?? "";
  const status = params.get("status") ?? "";
  const type = params.get("type") ?? "";
  const model = params.get("model") ?? "";
  const minCostRaw = params.get("min_cost") ?? "";
  const session = params.get("session") ?? "";
  const cursor = params.get("cursor") ?? "";

  const estado = useApi(async () => {
    const projects = await listProjects();
    if (projects.length === 0) return null;
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    const minCost = Number(minCostRaw);
    const [page, overview] = await Promise.all([
      listTraces({
        project_id: project,
        since: windowStart(days),
        search: q || undefined,
        status: status || undefined,
        span_type: type || undefined,
        sort,
        cursor: cursor || undefined,
        model: model || undefined,
        min_cost_usd: Number.isFinite(minCost) && minCost > 0 ? minCost : undefined,
        session_id: session || undefined,
      }),
      // Sólo para poblar el desplegable de modelos del modo avanzado.
      getOverview(project, days).catch(() => null),
    ]);
    return { project, page, overview };
  }, [pedido, days, sort, q, status, type, model, minCostRaw, session, cursor]);

  if (estado.fase === "cargando") return <TableSkeleton />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;
  if (estado.datos === null) return <NoProject />;

  const { project, page, overview } = estado.datos;
  const context = `project=${encodeURIComponent(project)}&days=${days}`;
  // Los modelos que de verdad aparecen en el rango, no una lista inventada.
  const modelos = [...new Set(page.traces.flatMap((t) => t.models))].sort();
  if (overview) {
    for (const m of overview.models_without_price) if (!modelos.includes(m)) modelos.push(m);
  }

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
          defaultValue={q}
          placeholder="Buscar por paso, herramienta o id de traza"
          className="field grow"
          aria-label="Buscar"
        />
        <select name="status" defaultValue={status} className="field" aria-label="Estado">
          <option value="">Cualquier estado</option>
          <option value="error">Sólo con error</option>
          <option value="ok">Sólo correctas</option>
        </select>
        <select name="type" defaultValue={type} className="field" aria-label="Tipo de paso">
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
        {/* Filtros de desarrollador: en Diagnóstico la barra se queda limpia. */}
        <select name="model" defaultValue={model} className="field pro" aria-label="Modelo">
          <option value="">Cualquier modelo</option>
          {modelos.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <input
          type="text"
          inputMode="decimal"
          name="min_cost"
          defaultValue={minCostRaw}
          placeholder="Coste mínimo"
          className="field pro"
          style={{ width: 130 }}
          aria-label="Coste mínimo en USD"
        />
        <input
          type="text"
          name="session"
          defaultValue={session}
          placeholder="session.id"
          className="field pro"
          style={{ width: 150 }}
          aria-label="Identificador de sesión"
        />
        <button type="submit" className="btn">
          Filtrar
        </button>
      </form>

      {page.traces.length === 0 ? (
        <div className="state">
          <h2>Ninguna traza coincide</h2>
          <p>Prueba a quitar filtros o a ampliar el rango temporal en la barra de arriba.</p>
          <div className="actions">
            <Link href={`/trazas?${context}`} className="btn">
              Quitar filtros
            </Link>
          </div>
        </div>
      ) : (
        <Listado
          page={page}
          project={project}
          days={days}
          sort={sort}
          cursor={cursor}
          context={context}
          onLive={() => {
            // El modo en vivo sólo tiene sentido con las más recientes delante: sobre
            // un orden por coste, «lo nuevo» no va arriba, y añadir filas al principio
            // sería mentir sobre el orden. Un clic lleva al orden que sí lo permite.
            const next = new URLSearchParams(params.toString());
            next.set("sort", "recent");
            next.delete("cursor");
            router.push(`/trazas?${next.toString()}`);
          }}
          recargar={() =>
            listTraces({
              project_id: project,
              since: windowStart(days),
              search: q || undefined,
              status: status || undefined,
              span_type: type || undefined,
              sort: "recent",
              model: model || undefined,
              session_id: session || undefined,
            }).then((p) => p.traces)
          }
        />
      )}
    </main>
  );
}

/**
 * La tabla, con el modo en vivo.
 *
 * Vive aparte porque tiene estado propio y los hooks no pueden ir detrás de los
 * retornos tempranos de `Contenido`. Sólo se ofrece «en vivo» cuando el orden es
 * por recientes y no hay cursor: en cualquier otro caso, añadir filas arriba
 * contradiría el orden que el usuario ha pedido.
 */
function Listado({
  page,
  project,
  days,
  sort,
  cursor,
  context,
  recargar,
  onLive,
}: {
  page: TraceListPage;
  project: string;
  days: number;
  sort: string;
  cursor: string;
  context: string;
  recargar: () => Promise<TraceSummary[]>;
  onLive: () => void;
}) {
  const puedeVivir = sort === "recent" && !cursor;
  const [enVivo, setEnVivo] = useState(false);
  const live = useLive(page.traces, recargar, puedeVivir && enVivo);
  const repeats = new Set(page.with_repeats);
  const activo = puedeVivir && enVivo;
  // Al pausar se dejan de pedir trazas, pero las que ya han llegado **se quedan**:
  // borrarlas al pausar castigaría justo al que ha visto algo y quiere mirarlo con
  // calma. Antes de encender nada, `live.traces` es exactamente la carga inicial.
  const traces = live.traces;

  return (
    <>
      <div className="pager" style={{ marginBottom: 6 }}>
        <span style={{ color: "var(--ink-3)" }}>
          {activo ? (
            <Latido live={live} />
          ) : (
            `${traces.length} trazas${live.recibidas > 0 ? " · en pausa" : ""}`
          )}
        </span>
        <button
          type="button"
          className={`live${activo ? " on" : ""}`}
          onClick={puedeVivir ? () => setEnVivo((v) => !v) : onLive}
          title={
            puedeVivir
              ? `Se comprueba cada ${LIVE_INTERVAL_MS / 1000} segundos`
              : "El modo en vivo necesita el orden por más recientes: al pulsar se cambia"
          }
        >
          <i aria-hidden />
          {activo ? "En vivo · pausar" : "Ver en vivo"}
        </button>
      </div>

      <table className="tbl">
        <thead>
          <tr>
            <th>Traza</th>
            <th className="pro">Modelos</th>
            <th className="r hide-sm">Pasos</th>
            <th className="r hide-sm simple-only">Tokens</th>
            <th className="r pro">Entrada</th>
            <th className="r pro">Salida</th>
            <th className="r">Coste</th>
            <th className="r hide-sm">Duración</th>
            <th className="r hide-sm">Cuándo</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((trace) => (
            <Row
              key={trace.trace_id}
              trace={trace}
              context={context}
              looping={repeats.has(trace.trace_id)}
              nueva={live.nuevas.has(trace.trace_id)}
            />
          ))}
        </tbody>
      </table>

      <div className="pager">
        <span style={{ color: "var(--ink-3)" }}>
          {traces.length} trazas{page.next_cursor ? "" : " (todas las del rango)"}
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
  );
}

/** El estado del modo en vivo, dicho en una línea. */
function Latido({ live }: { live: Live }) {
  if (live.fallando) return <>El backend no responde; se sigue intentando.</>;
  return (
    <>
      {live.recibidas > 0
        ? `${live.recibidas} ${live.recibidas === 1 ? "traza nueva" : "trazas nuevas"} desde que lo encendiste.`
        : "Esperando trazas nuevas."}
      {live.ultima && (
        <span className="pro">
          {" "}
          Última comprobación: {timestamp(live.ultima.toISOString())}.
        </span>
      )}
    </>
  );
}

function Row({
  trace,
  context,
  looping,
  nueva,
}: {
  trace: TraceSummary;
  context: string;
  looping: boolean;
  nueva?: boolean;
}) {
  const failed = trace.status === "error";
  return (
    <tr className={nueva ? "nueva" : undefined}>
      <td>
        <Link href={`/traza?${context}&id=${trace.trace_id}`}>
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
            <span className="simple-only">{trace.trace_id.slice(0, 12)}</span>
            <span className="pro">{trace.trace_id}</span>
            {trace.session_id ? ` · sesión ${trace.session_id}` : ""}
          </div>
        </Link>
      </td>
      <td className="pro" style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
        {trace.models.join(", ") || "—"}
      </td>
      <td className="r hide-sm">{trace.span_count}</td>
      <td className="r hide-sm simple-only">
        {tokens(trace.usage.input_tokens + trace.usage.output_tokens)}
      </td>
      <td className="r pro">{tokens(trace.usage.input_tokens)}</td>
      <td className="r pro">{tokens(trace.usage.output_tokens)}</td>
      <td className="r money" title={trace.unknown_cost_spans > 0 ? "coste incompleto" : undefined}>
        {money(trace.cost.total_usd, trace.cost.currency)}
        {trace.unknown_cost_spans > 0 && <span style={{ color: "var(--amber)" }}> +?</span>}
      </td>
      <td className="r hide-sm">{duration(trace.duration_ms)}</td>
      <td className="r hide-sm" title={timestamp(trace.start_time)}>
        {relative(trace.start_time)}
      </td>
    </tr>
  );
}


/**
 * `useSearchParams` obliga a un límite de Suspense para poder exportar la página como
 * HTML estático: sin él, Next no sabe qué pintar antes de que el navegador conozca la
 * URL. El esqueleto es el mismo que se ve mientras llegan los datos, así que no hay
 * dos saltos.
 */
export default function TrazasPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <Contenido />
    </Suspense>
  );
}
