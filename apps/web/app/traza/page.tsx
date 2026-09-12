"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { TraceTree } from "@/components/TraceTree";
import { Verdicts } from "@/components/Verdicts";
import { BackendDown, NeedsKey, NotFound, NotYours, TableSkeleton } from "@/components/states";
import { getTrace, judgePrompt, judgeStatus, parseDays, runJudge } from "@/lib/api";
import { duration, money, number, timestamp } from "@/lib/format";
import { allNodes } from "@/lib/tree";
import type { Annotation, JudgeStatus, Trace, TraceSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * Una traza, entera y navegable. Se llega desde el explorador o desde la ficha de un
 * problema, y desde aquí se vuelve al problema: observar y diagnosticar conectados en
 * los dos sentidos.
 */
function Contenido() {
  const params = useSearchParams();
  const traceId = params.get("id") ?? "";
  const days = parseDays(params.get("days") ?? undefined);
  const proyecto = params.get("project") ?? "";

  const estado = useApi(() => getTrace(traceId, proyecto || undefined), [traceId, proyecto]);

  if (estado.fase === "cargando") return <TableSkeleton />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const trace = estado.datos;
  const project = proyecto || trace?.summary.project_id || "";
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
  const findingId = looping ? `repeticion:${looping.span.step_key}` : null;

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

      <Anotar project={project} trace={trace} />

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
              <Link href={`/problema?${context}&id=${encodeURIComponent(findingId!)}`}>
                Ver qué cuesta y cómo arreglarlo →
              </Link>
            </>
          )}
        </div>
      )}

      <TraceTree trace={trace} />

      <div className="actions">
        <ExportarJSON trace={trace} />
      </div>
    </main>
  );
}

/**
 * Marcar la ejecución, aquí mismo.
 *
 * Se anota donde se está mirando: quien acaba de leer el árbol y ha visto que el agente
 * se inventó la tarifa tiene el botón delante, no en otra pantalla. El veredicto del
 * juez, si lo hay, se enseña al lado pero **nunca en el mismo control**: son dos cosas
 * distintas y confundirlas es lo único que esta pestaña no se puede permitir (D-083).
 */
function Anotar({ project, trace }: { project: string; trace: Trace }) {
  const [anotaciones, setAnotaciones] = useState<Annotation[]>(trace.annotations);
  const [juez, setJuez] = useState<JudgeStatus | null>(null);
  const [prompt, setPrompt] = useState<{ system: string; user: string } | null>(null);
  const [juzgando, setJuzgando] = useState(false);
  const [aviso, setAviso] = useState("");

  useEffect(() => {
    setAnotaciones(trace.annotations);
  }, [trace.annotations]);

  useEffect(() => {
    judgeStatus()
      .then(setJuez)
      .catch(() => setJuez(null));
  }, []);

  const traceId = trace.summary.trace_id;

  return (
    <section className="anotar">
      <div className="anotar-top">
        <span className="alabel">¿Esta ejecución está bien?</span>
        <Verdicts
          projectId={project}
          traceId={traceId}
          annotations={anotaciones}
          onChange={setAnotaciones}
        />
      </div>

      {juez?.enabled && (
        <div className="actions" style={{ marginTop: 10 }}>
          <button
            type="button"
            className="btn"
            disabled={juzgando}
            onClick={async () => {
              setJuzgando(true);
              setAviso("");
              try {
                const resultado = await runJudge({
                  project_id: project,
                  trace_ids: [traceId],
                });
                const actualizada = await getTrace(traceId, project);
                setAnotaciones(actualizada?.annotations ?? anotaciones);
                setAviso(
                  resultado.cost_unknown
                    ? `Juzgada con ${resultado.model}. Su coste no lo sabemos: ese modelo no está en la tabla de precios.`
                    : `Juzgada con ${resultado.model}. Ha costado ${money(resultado.cost_usd)}.`,
                );
              } catch (e) {
                setAviso(e instanceof Error ? e.message : "el juez no ha podido opinar");
              } finally {
                setJuzgando(false);
              }
            }}
          >
            {juzgando ? "Juzgando…" : `Que opine ${juez.model}`}
          </button>
          <button
            type="button"
            className="btn pro"
            onClick={async () =>
              setPrompt(prompt ? null : await judgePrompt(project, traceId))
            }
          >
            {prompt ? "Ocultar el prompt del juez" : "Ver el prompt del juez"}
          </button>
        </div>
      )}

      {aviso && <p className="jcost">{aviso}</p>}

      {/* Modo avanzado: el texto exacto que se le manda. Un veredicto que no se puede
          auditar no se puede discutir, y el primer «este fail está mal» llega el día
          uno (D-067 aplicado al juez). */}
      {prompt && (
        <div className="pro">
          <pre className="wrapless">{prompt.system}</pre>
          <pre className="wrapless">{prompt.user}</pre>
        </div>
      )}
    </section>
  );
}

/**
 * Descarga la traza tal cual la devuelve la API.
 *
 * El JSON se arma en el navegador con lo que ya está cargado: así no hace falta una
 * ruta de servidor sólo para adjuntar una cabecera, y funciona igual servido desde
 * Python que desde Node (D-069).
 */
function ExportarJSON({ trace }: { trace: Trace }) {
  const descargar = () => {
    const blob = new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.download = `${trace.summary.trace_id}.json`;
    enlace.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button type="button" className="btn pro" onClick={descargar}>
      Exportar traza en JSON
    </button>
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
        <span className="simple-only">{summary.trace_id.slice(0, 12)}</span>
        <span className="pro">{summary.trace_id}</span> · {summary.project_id} ·{" "}
        {timestamp(summary.start_time)}
        {summary.session_id ? ` · sesión ${summary.session_id}` : ""}
        {summary.user_id ? ` · usuario ${summary.user_id}` : ""}
      </div>
      <div className="d-cost">
        <div>
          <b className="num">
            {money(summary.cost.total_usd, summary.cost.currency)}
            {summary.unknown_cost_spans > 0 && "+"}
          </b>
          <span>
            {summary.unknown_cost_spans > 0
              ? `coste (faltan ${summary.unknown_cost_spans} pasos sin tarifa)`
              : "coste"}
          </span>
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
        <div className="pro">
          <b className="num neutral">{summary.models.join(", ") || "—"}</b>
          <span>modelos</span>
        </div>
        {summary.usage.cached_input_tokens > 0 && (
          <div className="pro">
            <b className="num good">{number(summary.usage.cached_input_tokens)}</b>
            <span>tokens servidos desde caché</span>
          </div>
        )}
        {summary.cost.cache_saving_usd > 0 && (
          <div>
            <b className="num good">
              {money(summary.cost.cache_saving_usd, summary.cost.currency)}
            </b>
            <span>que te ha ahorrado la caché</span>
          </div>
        )}
        {summary.assumed_rate_spans > 0 && (
          <div className="pro">
            <b className="num neutral">{summary.assumed_rate_spans}</b>
            <span>pasos con tarifa asumida</span>
          </div>
        )}
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


/**
 * `useSearchParams` obliga a un límite de Suspense para poder exportar la página como
 * HTML estático: sin él, Next no sabe qué pintar antes de que el navegador conozca la
 * URL. El esqueleto es el mismo que se ve mientras llegan los datos, así que no hay
 * dos saltos.
 */
export default function TrazaPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <Contenido />
    </Suspense>
  );
}
