"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BackendDown, NeedsKey, NoProject, NotYours, TableSkeleton } from "@/components/states";
import { getOverview, listProjects, listTraces, parseDays, windowStart } from "@/lib/api";
import { descargarCsv } from "@/lib/csv";
import { useApi } from "@/lib/useApi";
import { Listado, GuardarConjunto } from "./listado";

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
  // El filtro por paso llega desde la ficha de un hallazgo: identidad exacta, no texto.
  const step = params.get("step") ?? "";
  const stepLabel = params.get("step_label") ?? "";
  const status = params.get("status") ?? "";
  const type = params.get("type") ?? "";
  const model = params.get("model") ?? "";
  const minCostRaw = params.get("min_cost") ?? "";
  const session = params.get("session") ?? "";
  // Llega desde el reparto por usuario del Panel (D-123).
  const user = params.get("user") ?? "";
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
        step_key: step || undefined,
        status: status || undefined,
        span_type: type || undefined,
        sort,
        cursor: cursor || undefined,
        model: model || undefined,
        min_cost_usd: Number.isFinite(minCost) && minCost > 0 ? minCost : undefined,
        session_id: session || undefined,
        user_id: user || undefined,
      }),
      // Sólo para poblar el desplegable de modelos del modo avanzado.
      getOverview(project, days).catch(() => null),
    ]);
    return { project, page, overview };
  }, [pedido, days, sort, q, step, status, type, model, minCostRaw, session, user, cursor]);

  if (estado.fase === "cargando") return <TableSkeleton />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
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
        {step && <input type="hidden" name="step" value={step} />}
        {step && <input type="hidden" name="step_label" value={stepLabel} />}
        {user && <input type="hidden" name="user" value={user} />}
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

      {step && (
        <p className="filtro-paso">
          {/* Filtra por el paso, no por la repetición: puede haber ejecuciones que pasen
              por él sin repetirlo, y decir «las del problema» sería decir de más. */}
          <span>
            Sólo las ejecuciones que pasan por el paso del problema: <strong>{stepLabel || step}</strong>
          </span>
          <Link href={`/trazas?${sinPaso(params)}`} className="btn">
            Quitar
          </Link>
        </p>
      )}

      {user && (
        <p className="filtro-paso">
          <span>
            Sólo las ejecuciones del usuario <strong>{user}</strong>
          </span>
          <Link href={`/trazas?${sinParametro(params, "user")}`} className="btn">
            Quitar
          </Link>
        </p>
      )}

      {page.traces.length > 0 && (
        <div className="explorer-tools">
          <button
            type="button"
            className="btn small"
            onClick={() =>
              exportarTrazas(project, {
                project_id: project,
                since: windowStart(days),
                search: q || undefined,
                step_key: step || undefined,
                status: status || undefined,
                span_type: type || undefined,
                model: model || undefined,
                session_id: session || undefined,
                user_id: user || undefined,
              })
            }
          >
            Exportar CSV
          </button>
        </div>
      )}

      {page.traces.length > 0 && (
        <GuardarConjunto
          project={project}
          context={context}
          filter={{
            since: windowStart(days),
            search: q || undefined,
            step_key: step || undefined,
            status: status || undefined,
            span_type: type || undefined,
            model: model || undefined,
            min_cost_usd: minCostRaw || undefined,
            session_id: session || undefined,
            user_id: user || undefined,
            sort,
          }}
        />
      )}

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
              step_key: step || undefined,
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

/** Los mismos parámetros sin uno de los filtros. */
function sinParametro(params: { toString(): string }, clave: string): string {
  const next = new URLSearchParams(params.toString());
  next.delete(clave);
  next.delete("cursor");
  return next.toString();
}

/**
 * Todas las trazas del filtro, a CSV (D-123). Se pide por orden de llegada, que es el
 * único con cursor, y con un tope: un CSV de cien mil filas no lo abre nadie, y quien
 * lo necesite tiene la API.
 */
const TOPE_CSV = 2000;

async function exportarTrazas(
  project: string,
  filtro: Parameters<typeof listTraces>[0],
): Promise<void> {
  const filas: (string | number | null)[][] = [];
  let cursor: string | undefined;
  do {
    const pagina = await listTraces({ ...filtro, sort: "recent", limit: 200, cursor });
    for (const t of pagina.traces) {
      filas.push([
        t.trace_id,
        t.root_name,
        t.start_time,
        t.status,
        t.session_id ?? "",
        t.user_id ?? "",
        t.span_count,
        t.usage.input_tokens,
        t.usage.output_tokens,
        t.cost.total_usd,
        t.unknown_cost_spans > 0 ? "sí" : "no",
        t.duration_ms,
        t.models.join(" "),
      ]);
    }
    cursor = pagina.next_cursor ?? undefined;
  } while (cursor && filas.length < TOPE_CSV);
  descargarCsv(
    `laplace-${project}-trazas`,
    [
      "Traza",
      "Agente",
      "Inicio",
      "Estado",
      "Sesión",
      "Usuario",
      "Pasos",
      "Tokens entrada",
      "Tokens salida",
      "Coste (USD)",
      "Coste incompleto",
      "Duración (ms)",
      "Modelos",
    ],
    filas.slice(0, TOPE_CSV),
  );
}

/** Los mismos parámetros sin el filtro por paso. */
function sinPaso(params: { toString(): string }): string {
  const next = new URLSearchParams(params.toString());
  next.delete("step");
  next.delete("step_label");
  next.delete("cursor");
  return next.toString();
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
