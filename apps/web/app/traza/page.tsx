"use client";

import Link from "next/link";
import { GrafoAgente } from "@/components/GrafoAgente";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { TraceTree } from "@/components/TraceTree";
import { Verdicts } from "@/components/Verdicts";
import { BackendDown, NeedsKey, NotFound, NotYours, TableSkeleton } from "@/components/states";
import {
  diagnoseTrace,
  getOverview,
  getTrace,
  judgePrompt,
  judgeStatus,
  parseDays,
  runJudge,
} from "@/lib/api";
import { duration, money, moneyExact, number, timestamp } from "@/lib/format";
import { allNodes } from "@/lib/tree";
import type { Annotation, Diagnosis, Finding, JudgeStatus, Trace, TraceSummary } from "@/lib/types";
import { useTitulo } from "@/lib/titulo";
import { useApi } from "@/lib/useApi";
import { t, tn } from "@/lib/textos";

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

  const estado = useApi(async (senal) => {
    const trace = await getTrace(traceId, proyecto || undefined, senal);
    // Los problemas del proyecto, para decir cuáles pasan en esta ejecución. Si fallan,
    // la traza se enseña igual: es un añadido, no lo que se ha venido a ver.
    const project = proyecto || trace?.summary.project_id || "";
    const overview = trace && project ? await getOverview(project, days, senal).catch(() => null) : null;
    return { trace, findings: [...(overview?.findings ?? []), ...(overview?.set_aside ?? [])] };
  }, [traceId, proyecto, days]);
  useTitulo(estado.fase === "listo" ? estado.datos.trace?.summary.root_name : null);
  // El span que se pide ver desde una cita del diagnóstico (D-180).
  const [foco, setFoco] = useState<{ id: string } | null>(null);

  if (estado.fase === "cargando") return <TableSkeleton />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} codigo={estado.error.code} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { trace, findings } = estado.datos;
  const project = proyecto || trace?.summary.project_id || "";
  const context = `project=${encodeURIComponent(project)}&days=${days}`;

  if (!trace) {
    return (
      <NotFound
        title={t("traza.no_encontrada")}
        body={t("traza.no_encontrada.texto")}
        back={`/trazas?${context}`}
      />
    );
  }

  const aqui = problemasDeLaTraza(trace, findings);

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
        {t("traza.todas")}
      </Link>

      <Head summary={trace.summary} />

      <Anotar project={project} trace={trace} />

      <DiagnosticoModelo project={project} trace={trace} onCita={(id) => setFoco({ id })} />

      {aqui.length > 0 && (
        <div className="en-esta-traza">
          <strong>
            {tn("traza.problemas", aqui.length)}
          </strong>
          <ul>
            {aqui.map((f) => (
              <li key={f.id}>
                <Link href={`/problema?${context}&id=${encodeURIComponent(f.id)}`}>
                  {f.title} →
                </Link>
                {f.state && <span className="muted"> · {f.state === "ignorado" ? t("traza.estado.ignorado") : f.state === "reaparecido" ? t("traza.estado.reaparecido") : t("traza.estado.arreglado")}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <GrafoAgente trace={trace} resaltados={new Set(aqui.map((f) => f.step_key))} />

      <TraceTree trace={trace} foco={foco} />

      <div className="actions">
        <ExportarJSON trace={trace} />
      </div>
    </main>
  );
}

/**
 * El diagnóstico con modelo de la ejecución (D-180). Cada afirmación lleva los spans
 * que la sostienen: al pulsar uno, el árbol lo abre. Lo que el modelo dijo sin citar
 * nada de la traza no está aquí, y se dice cuánto fue. El dinero no lo dice el modelo.
 */
function DiagnosticoModelo({
  project,
  trace,
  onCita,
}: {
  project: string;
  trace: Trace;
  onCita: (spanId: string) => void;
}) {
  const [diag, setDiag] = useState<Diagnosis | null>(trace.diagnosis);
  const [puede, setPuede] = useState(false);
  const [pidiendo, setPidiendo] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    judgeStatus()
      .then((s) => setPuede(Boolean(s.diagnosis_enabled)))
      .catch(() => setPuede(false));
  }, []);

  const nombres = new Map(
    allNodes(trace.roots).map((n) => [n.span.span_id, n.span.step_label || n.span.name]),
  );

  async function pedir() {
    setPidiendo(true);
    setError("");
    try {
      setDiag(await diagnoseTrace(project, trace.summary.trace_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("traza.diag.error"));
    } finally {
      setPidiendo(false);
    }
  }

  if (!diag && !puede) return null;
  return (
    <section className="en-esta-traza diag-modelo">
      {diag ? (
        <>
          <strong>{diag.cause}</strong>
          <ul>
            {diag.claims.map((c, i) => (
              <li key={i}>
                {c.text}{" "}
                {c.span_ids.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className="chip where cita"
                    title={id}
                    onClick={() => onCita(id)}
                  >
                    {nombres.get(id) ?? id.slice(0, 8)}
                  </button>
                ))}
              </li>
            ))}
          </ul>
          {diag.suggestion && (
            <p>
              <strong>{t("traza.diag.sugerencia")}</strong> {diag.suggestion}
            </p>
          )}
          <p className="disclaimer">
            {t("traza.diag.pie", {
              modelo: diag.model,
              coste: diag.cost_unknown ? t("traza.diag.coste_desconocido") : moneyExact(diag.cost_usd),
            })}
            {diag.discarded_claims > 0 && <> {tn("traza.diag.tiradas", diag.discarded_claims)}</>}
          </p>
        </>
      ) : (
        <p className="muted">{t("traza.diag.vacio")}</p>
      )}
      {puede && (
        <div className="actions" style={{ paddingTop: 0 }}>
          <button type="button" className="btn small" onClick={pedir} disabled={pidiendo}>
            {pidiendo ? t("traza.diag.pidiendo") : diag ? t("traza.diag.otra_vez") : t("traza.diag.pedir")}
          </button>
        </div>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

/**
 * Qué problemas del proyecto pasan en esta ejecución (D-123).
 *
 * Antes sólo se enlazaba la repetición, adivinando su identificador; un bucle o un
 * modelo caro en esta misma traza no se decía. Ahora se cruza por `step_key`, que es la
 * misma identidad con la que la ficha filtra sus trazas. Una repetición o un bucle sólo
 * cuentan si el paso sale más de una vez aquí: pasar por él una vez no es repetirlo.
 */
function problemasDeLaTraza(trace: Trace, findings: Finding[]): Finding[] {
  const veces = new Map<string, number>();
  for (const node of allNodes(trace.roots)) {
    const clave = node.span.step_key || node.span.name;
    veces.set(clave, (veces.get(clave) ?? 0) + 1);
  }
  return findings.filter((f) => {
    const n = veces.get(f.step_key) ?? 0;
    // Rehacer una respuesta cortada también es pasar dos veces por el paso (D-193).
    const dosVeces = f.kind === "repeticion" || f.kind === "bucle" || f.kind === "salida_truncada";
    return dosVeces ? n >= 2 : n >= 1;
  });
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
        <span className="alabel">{t("traza.bien")}</span>
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
                    ? t("traza.juez.sin_tarifa", { modelo: resultado.model })
                    : t("traza.juez.coste", {
                        modelo: resultado.model,
                        coste: money(resultado.cost_usd),
                      }),
                );
              } catch (e) {
                setAviso(e instanceof Error ? e.message : t("traza.juez.error"));
              } finally {
                setJuzgando(false);
              }
            }}
          >
            {juzgando ? t("traza.juez.juzgando") : t("traza.juez.opine", { modelo: juez.model })}
          </button>
          <button
            type="button"
            className="btn pro"
            onClick={async () =>
              setPrompt(prompt ? null : await judgePrompt(project, traceId))
            }
          >
            {prompt ? t("traza.juez.ocultar") : t("traza.juez.ver")}
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
      {t("traza.exportar")}
    </button>
  );
}

function Head({ summary }: { summary: TraceSummary }) {
  const failed = summary.status === "error";
  return (
    <div className="d-head" style={{ paddingBottom: 18, marginBottom: 16 }}>
      <h1 style={{ fontSize: 22, maxWidth: "none" }}>
        <i className={`dot ${failed ? "error" : "ok"}`} style={{ marginRight: 10 }} aria-hidden />
        {summary.root_name || t("lista.sin_nombre")}
      </h1>
      <div className="d-sub" style={{ marginBottom: 12 }}>
        <span className="simple-only">{summary.trace_id.slice(0, 12)}</span>
        <span className="pro">{summary.trace_id}</span> · {summary.project_id} ·{" "}
        {timestamp(summary.start_time)}
        {summary.session_id ? t("lista.sesion", { id: summary.session_id }) : ""}
        {summary.user_id ? t("traza.usuario", { id: summary.user_id }) : ""}
      </div>
      <div className="d-cost">
        <div>
          <b className="num">
            {money(summary.cost.total_usd, summary.cost.currency)}
            {summary.unknown_cost_spans > 0 && "+"}
          </b>
          <span>
            {summary.unknown_cost_spans > 0
              ? t("traza.coste.faltan", { n: summary.unknown_cost_spans })
              : summary.cost.rate_unverified
                ? t("traza.coste.sin_verificar")
                : t("traza.coste")}
          </span>
        </div>
        <div>
          <b className="num neutral">
            {number(summary.usage.input_tokens)} / {number(summary.usage.output_tokens)}
          </b>
          <span>{t("traza.tokens")}</span>
        </div>
        <div>
          <b className="num neutral">{duration(summary.duration_ms)}</b>
          <span>{t("traza.duracion")}</span>
        </div>
        <div>
          <b className="num neutral">{summary.span_count}</b>
          <span>{t("traza.pasos")}</span>
        </div>
        <div>
          <b className="num neutral">{summary.llm_call_count}</b>
          <span>{t("traza.llamadas")}</span>
        </div>
        <div>
          <b className="num neutral">{summary.tool_call_count}</b>
          <span>{t("traza.herramientas")}</span>
        </div>
        <div className="pro">
          <b className="num neutral">{summary.models.join(", ") || "—"}</b>
          <span>{t("traza.modelos")}</span>
        </div>
        {summary.usage.cached_input_tokens > 0 && (
          <div className="pro">
            <b className="num good">{number(summary.usage.cached_input_tokens)}</b>
            <span>{t("traza.cache_tokens")}</span>
          </div>
        )}
        {summary.cost.cache_saving_usd > 0 && (
          <div>
            <b className="num good">
              {money(summary.cost.cache_saving_usd, summary.cost.currency)}
            </b>
            <span>{t("traza.cache_ahorro")}</span>
          </div>
        )}
        {summary.assumed_rate_spans > 0 && (
          <div className="pro">
            <b className="num neutral">{summary.assumed_rate_spans}</b>
            <span>{t("traza.asumida")}</span>
          </div>
        )}
        {summary.error_count > 0 && (
          <div>
            <b className="num" style={{ color: "var(--rose)" }}>
              {summary.error_count}
            </b>
            <span>{t("traza.errores")}</span>
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
