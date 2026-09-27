"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BackendDown, NeedsKey, NoProject, NotYours, TableSkeleton } from "@/components/states";
import { getOverview, listProjects, listTraces, parseDays, windowStart } from "@/lib/api";
import { descargarCsv } from "@/lib/csv";
import { useApi } from "@/lib/useApi";
import { Listado, GuardarConjunto } from "./listado";
import { tr } from "@/lib/i18n";
import { t, t as txt } from "@/lib/textos";

const SORTS = [
  { value: "cost", label: "trazas.orden.coste" },
  { value: "recent", label: "trazas.orden.reciente" },
  { value: "duration", label: "trazas.orden.duracion" },
] as const;

/** Lo mismo que pide la API: con menos, casi todo casa y el índice no ayuda (D-144). */
const MINIMO_CONTENIDO = 3;

const TYPES = [
  { value: "", label: "trazas.tipo.cualquiera" },
  { value: "llm", label: "trazas.tipo.llm" },
  { value: "tool", label: "trazas.tipo.tool" },
  { value: "retrieval", label: "trazas.tipo.retrieval" },
] as const;

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
  // Dónde se busca: en nombres e ids (rápido) o dentro de prompts, respuestas y
  // herramientas (D-144). El contenido pide tres caracteres: con menos casa casi todo.
  const enContenido = params.get("en") === "contenido";
  const corta = enContenido && q.length > 0 && q.length < MINIMO_CONTENIDO;
  const texto = enContenido
    ? { content: q.length >= MINIMO_CONTENIDO ? q : undefined }
    : { search: q || undefined };
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

  const estado = useApi(async (senal) => {
    const projects = await listProjects(senal);
    if (projects.length === 0) return null;
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    const minCost = Number(minCostRaw);
    const [page, overview] = await Promise.all([
      listTraces({
        project_id: project,
        since: windowStart(days),
        ...texto,
        step_key: step || undefined,
        status: status || undefined,
        span_type: type || undefined,
        sort,
        cursor: cursor || undefined,
        model: model || undefined,
        min_cost_usd: Number.isFinite(minCost) && minCost > 0 ? minCost : undefined,
        session_id: session || undefined,
        user_id: user || undefined,
      }, senal),
      // Sólo para poblar el desplegable de modelos del modo avanzado.
      getOverview(project, days, senal).catch(() => null),
    ]);
    return { project, page, overview };
  }, [pedido, days, sort, q, enContenido, step, status, type, model, minCostRaw, session, user, cursor]);

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
        <h2>{t("trazas.titulo")}</h2>
        <p className="lead">{t("trazas.lead")}</p>
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
          placeholder={enContenido ? t("trazas.buscar.contenido") : t("trazas.buscar.nombres")}
          className="field grow"
          aria-label={t("trazas.buscar")}
        />
        <select name="en" defaultValue={enContenido ? "contenido" : ""} className="field" aria-label={t("trazas.donde")}>
          <option value="">{t("trazas.en_nombres")}</option>
          <option value="contenido">{t("trazas.en_contenido")}</option>
        </select>
        <select name="status" defaultValue={status} className="field" aria-label={t("trazas.estado")}>
          <option value="">{t("trazas.estado.cualquiera")}</option>
          <option value="error">{t("trazas.estado.error")}</option>
          <option value="ok">{t("trazas.estado.ok")}</option>
        </select>
        <select name="type" defaultValue={type} className="field" aria-label={t("trazas.tipo")}>
          {TYPES.map((tipo) => (
            <option key={tipo.value} value={tipo.value}>
              {t(tipo.label)}
            </option>
          ))}
        </select>
        <select name="sort" defaultValue={sort} className="field" aria-label={t("trazas.orden")}>
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {t(s.label)}
            </option>
          ))}
        </select>
        {/* Filtros de desarrollador: en Diagnóstico la barra se queda limpia. */}
        <select name="model" defaultValue={model} className="field pro" aria-label={t("trazas.modelo")}>
          <option value="">{t("trazas.modelo.cualquiera")}</option>
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
          placeholder={t("trazas.coste_minimo")}
          className="field pro"
          style={{ width: 130 }}
          aria-label={t("trazas.coste_minimo.aria")}
        />
        <input
          type="text"
          name="session"
          defaultValue={session}
          placeholder="session.id"
          className="field pro"
          style={{ width: 150 }}
          aria-label={t("trazas.sesion.aria")}
        />
        <button type="submit" className="btn">
          {t("trazas.filtrar")}
        </button>
      </form>

      {corta && (
        <p className="filtro-paso">
          <span>{t("trazas.corta", { n: MINIMO_CONTENIDO })}</span>
        </p>
      )}

      {step && (
        <p className="filtro-paso">
          {/* Filtra por el paso, no por la repetición: puede haber ejecuciones que pasen
              por él sin repetirlo, y decir «las del problema» sería decir de más. */}
          <span>
            {tr("trazas.filtro.paso", { paso: <strong>{stepLabel || step}</strong> })}
          </span>
          <Link href={`/trazas?${sinPaso(params)}`} className="btn">
            {t("trazas.quitar")}
          </Link>
        </p>
      )}

      {user && (
        <p className="filtro-paso">
          <span>{tr("trazas.filtro.usuario", { usuario: <strong>{user}</strong> })}</span>
          <Link href={`/trazas?${sinParametro(params, "user")}`} className="btn">
            {t("trazas.quitar")}
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
                ...texto,
                step_key: step || undefined,
                status: status || undefined,
                span_type: type || undefined,
                model: model || undefined,
                session_id: session || undefined,
                user_id: user || undefined,
              })
            }
          >
            {t("comun.exportar_csv")}
          </button>
        </div>
      )}

      {page.traces.length > 0 && (
        <GuardarConjunto
          project={project}
          context={context}
          filter={{
            since: windowStart(days),
            ...texto,
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
          <h2>{t("trazas.ninguna")}</h2>
          <p>{t("trazas.ninguna.texto")}</p>
          <div className="actions">
            <Link href={`/trazas?${context}`} className="btn">
              {t("trazas.quitar_filtros")}
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
          siguiente={page.next_cursor ? paginaSiguiente(params, project, days, sort, page.next_cursor) : null}
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
              ...texto,
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

/**
 * La página siguiente con **todos** los filtros activos. Antes el enlace sólo llevaba el
 * proyecto y los días, y al pasar de página se perdían la búsqueda, el estado y el resto.
 */
function paginaSiguiente(
  params: { toString(): string },
  project: string,
  days: number,
  sort: string,
  cursor: string,
): string {
  const next = new URLSearchParams(params.toString());
  next.set("project", project);
  next.set("days", String(days));
  next.set("sort", sort);
  next.set("cursor", cursor);
  return `/trazas?${next.toString()}`;
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
        t.unknown_cost_spans > 0 ? txt("csv.si") : txt("csv.no"),
        t.duration_ms,
        t.models.join(" "),
      ]);
    }
    cursor = pagina.next_cursor ?? undefined;
  } while (cursor && filas.length < TOPE_CSV);
  descargarCsv(
    `laplace-${project}-${txt("csv.nombre.trazas")}`,
    [
      txt("csv.h.traza"),
      txt("csv.h.agente"),
      txt("csv.h.inicio"),
      txt("csv.h.estado"),
      txt("csv.h.sesion"),
      txt("csv.h.usuario"),
      txt("csv.h.pasos"),
      txt("csv.h.tok_entrada"),
      txt("csv.h.tok_salida"),
      txt("csv.h.coste"),
      txt("csv.h.incompleto"),
      txt("csv.h.duracion"),
      txt("csv.h.modelos"),
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
