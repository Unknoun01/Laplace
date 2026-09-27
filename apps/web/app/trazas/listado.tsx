"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { VerdictDots, Verdicts } from "@/components/Verdicts";
import { annotationsFor, createDataset } from "@/lib/api";
import { duration, money, relative, timestamp, tokens } from "@/lib/format";
import type { Annotation, TraceListPage, TraceSummary } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { LIVE_INTERVAL_MS, type Live, useLive } from "@/lib/useLive";
import { tr } from "@/lib/i18n";
import { t, tn } from "@/lib/textos";

/**
 * La tabla, con el modo en vivo.
 *
 * Vive aparte porque tiene estado propio y los hooks no pueden ir detrás de los
 * retornos tempranos de `Contenido`. Sólo se ofrece «en vivo» cuando el orden es
 * por recientes y no hay cursor: en cualquier otro caso, añadir filas arriba
 * contradiría el orden que el usuario ha pedido.
 */
export function Listado({
  page,
  project,
  days,
  sort,
  cursor,
  context,
  siguiente,
  recargar,
  onLive,
}: {
  page: TraceListPage;
  project: string;
  days: number;
  sort: string;
  cursor: string;
  context: string;
  /** Enlace a la página siguiente, con los mismos filtros; `null` si no hay más. */
  siguiente: string | null;
  recargar: () => Promise<TraceSummary[]>;
  onLive: () => void;
}) {
  const puedeVivir = sort === "recent" && !cursor;
  const [enVivo, setEnVivo] = useState(false);
  const live = useLive(page.traces, recargar, puedeVivir && enVivo);
  const repeats = new Set(page.with_repeats);

  // Los veredictos de la página, en una sola consulta. Se piden aparte de las trazas
  // porque son mutables y las trazas no: mezclarlos obligaría a recargar la lista
  // entera cada vez que alguien marca una fila.
  const [anotaciones, setAnotaciones] = useState<Record<string, Annotation[]>>({});
  const idsPagina = page.traces.map((t) => t.trace_id).join(",");
  useEffect(() => {
    let vigente = true;
    annotationsFor(idsPagina.split(",").filter(Boolean), project)
      .then((a) => vigente && setAnotaciones(a))
      .catch(() => undefined);
    return () => {
      vigente = false;
    };
  }, [idsPagina, project]);
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
            `${t("lista.n_trazas", { n: traces.length })}${live.recibidas > 0 ? t("lista.en_pausa") : ""}`
          )}
        </span>
        <button
          type="button"
          className={`live${activo ? " on" : ""}`}
          onClick={puedeVivir ? () => setEnVivo((v) => !v) : onLive}
          title={
            puedeVivir
              ? t("lista.cada", { n: LIVE_INTERVAL_MS / 1000 })
              : t("lista.vivo.necesita")
          }
        >
          <i aria-hidden />
          {activo ? t("lista.vivo.pausar") : t("lista.vivo.ver")}
        </button>
      </div>

      <div className="tbl-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>{t("lista.col.traza")}</th>
              <th className="pro">{t("lista.col.modelos")}</th>
              <th className="r hide-sm">{t("lista.col.pasos")}</th>
              <th className="r hide-sm simple-only">{t("lista.col.tokens")}</th>
              <th className="r pro">{t("lista.col.entrada")}</th>
              <th className="r pro">{t("lista.col.salida")}</th>
              <th className="r">{t("lista.col.coste")}</th>
              <th className="r hide-sm">{t("lista.col.duracion")}</th>
              <th className="r hide-sm">{t("lista.col.cuando")}</th>
              <th className="r">{t("lista.col.bien")}</th>
            </tr>
          </thead>
          <tbody>
            {traces.map((trace) => (
              <Row
                key={trace.trace_id}
                trace={trace}
                context={context}
                project={project}
                looping={repeats.has(trace.trace_id)}
                nueva={live.nuevas.has(trace.trace_id)}
                annotations={anotaciones[trace.trace_id] ?? []}
                onAnnotated={(nuevas) =>
                  setAnotaciones((previas) => ({ ...previas, [trace.trace_id]: nuevas }))
                }
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <span style={{ color: "var(--ink-3)" }}>
          {t("lista.n_trazas", { n: traces.length })}
          {page.next_cursor ? "" : t("lista.todas")}
        </span>
        {siguiente && (
          <Link className="btn small" href={siguiente}>
            {t("lista.antiguas")}
          </Link>
        )}
      </div>
    </>
  );
}

/** El estado del modo en vivo, dicho en una línea. */
export function Latido({ live }: { live: Live }) {
  if (live.fallando) return <>{t("lista.sin_backend")}</>;
  return (
    <>
      {live.recibidas > 0
        ? tn("lista.nuevas", live.recibidas)
        : t("lista.esperando")}
      {live.ultima && (
        <span className="pro">
          {" "}
          {t("lista.ultima", { fecha: timestamp(live.ultima.toISOString()) })}
        </span>
      )}
    </>
  );
}

export function Row({
  trace,
  context,
  project,
  looping,
  nueva,
  annotations,
  onAnnotated,
}: {
  trace: TraceSummary;
  context: string;
  project: string;
  looping: boolean;
  nueva?: boolean;
  annotations: Annotation[];
  onAnnotated: (nuevas: Annotation[]) => void;
}) {
  const failed = trace.status === "error";
  return (
    <tr className={nueva ? "nueva" : undefined}>
      <td>
        <Link href={`/traza?${context}&id=${trace.trace_id}`}>
          <span>
            <i className={`dot ${failed ? "error" : "ok"}`} aria-label={failed ? t("lista.con_error") : "ok"} />
            {trace.root_name || t("lista.sin_nombre")}
            {looping && (
              <span className="badge" style={{ marginLeft: 9 }} title={t("lista.bucle.ayuda")}>
                {t("lista.bucle")}
              </span>
            )}
            {failed && (
              <span
                className="badge"
                style={{ marginLeft: 9, background: "var(--rose-bg)", color: "var(--rose)", borderColor: "var(--rose-line)" }}
              >
                {tn("lista.errores", trace.error_count)}
              </span>
            )}
          </span>
          {/* Lo que le pidieron: es lo que distingue una fila de la de al lado (D-125). */}
          {trace.input_preview && <div className="pregunta">«{trace.input_preview}»</div>}
          <div className="meta">
            <span className="simple-only">{trace.trace_id.slice(0, 12)}</span>
            <span className="pro">{trace.trace_id}</span>
            {trace.session_id ? t("lista.sesion", { id: trace.session_id }) : ""}
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
      <td className="r money" title={trace.unknown_cost_spans > 0 ? t("lista.coste_incompleto") : undefined}>
        {money(trace.cost.total_usd, trace.cost.currency)}
        {trace.unknown_cost_spans > 0 && <span style={{ color: "var(--amber)" }}> +?</span>}
      </td>
      <td className="r hide-sm">{duration(trace.duration_ms)}</td>
      <td className="r hide-sm" title={timestamp(trace.start_time)}>
        {relative(trace.start_time)}
      </td>
      {/* Anotar desde la lista: revisar veinte ejecuciones seguidas es el caso normal
          de esta pestaña, y abrir y cerrar cada una lo haría insoportable. */}
      <td className="r">
        <VerdictDots annotations={annotations} />
        <Verdicts
          projectId={project}
          traceId={trace.trace_id}
          annotations={annotations}
          onChange={onAnnotated}
          compact
        />
      </td>
    </tr>
  );
}

/**
 * Guardar lo que se está viendo como conjunto de casos.
 *
 * Evaluaciones decía «fíltralo en el explorador y vuelve», pero al volver el filtro no
 * viajaba: el conjunto se creaba siempre con las más recientes. El filtro vive aquí,
 * así que el botón también.
 */
export function GuardarConjunto({
  project,
  context,
  filter,
}: {
  project: string;
  context: string;
  filter: Record<string, string | undefined>;
}) {
  const { escribir } = usePermisos(project);
  const [nombre, setNombre] = useState("");
  const [estado, setEstado] = useState<"" | "creando" | "hecho">("");
  const [error, setError] = useState("");

  async function guardar() {
    if (!nombre.trim()) return;
    setEstado("creando");
    setError("");
    try {
      await createDataset({ project_id: project, name: nombre.trim(), filter, limit: 50 });
      setEstado("hecho");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.crear"));
      setEstado("");
    }
  }

  if (!escribir) return null;
  if (estado === "hecho") {
    return (
      <p className="guardar-conjunto">
        {t("conj.creado", { nombre: nombre.trim() })}{" "}
        <Link href={`/evaluaciones?${context}`}>{t("conj.ir")}</Link>
      </p>
    );
  }

  return (
    <details className="guardar-conjunto">
      <summary>{t("conj.guardar")}</summary>
      <div className="ab">
        <input
          className="field grow"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder={t("conj.nombre.placeholder")}
          aria-label={t("conj.nombre.aria")}
        />
        <button
          type="button"
          className="btn"
          onClick={guardar}
          disabled={estado === "creando" || !nombre.trim()}
        >
          {estado === "creando" ? t("conj.creando") : t("conj.boton")}
        </button>
      </div>
      {error && <p className="verr">{error}</p>}
    </details>
  );
}
