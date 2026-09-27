"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NoTracesYet, NotYours } from "@/components/states";
import { getBreakdown, getPanel, listProjects, parseDays } from "@/lib/api";
import { dayHour, decimal, duration, money, number, porcentaje, spanLabel, tokens } from "@/lib/format";
import type { Breakdown, Metric, Panel, Spike } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { tr } from "@/lib/i18n";
import { t, tn } from "@/lib/textos";
import { ETIQUETAS, idiomaActual } from "@/lib/idioma";

/**
 * Panel: coste por unidad de trabajo y atribución de picos.
 *
 * Un panel de líneas con tokens y latencia no aporta nada frente a Grafana, así que
 * esta pantalla está construida alrededor de dos cosas y sólo dos:
 *
 * 1. **La lectura, en palabras y arriba del todo.** Si el gasto sube porque hay más
 *    trabajo, lo dice; si sube con el mismo trabajo, dice que hay que revisarlo. Las
 *    métricas por ejecución van debajo respaldándola, y los totales en letra pequeña.
 * 2. **Los picos con su causa.** Cada uno dice cuándo, qué cambió alrededor y a qué
 *    trazas ir. Cuando no se puede atribuir, lo dice; no se insinúa nada.
 */
function Contenido() {
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  const days = parseDays(params.get("days") ?? undefined);

  const estado = useApi(async (senal) => {
    const projects = await listProjects(senal);
    if (projects.length === 0) return { project: "", panel: null };
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    return { project, panel: await getPanel(project, days, senal) };
  }, [pedido, days]);

  if (estado.fase === "cargando") return <Cargando />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, panel } = estado.datos;
  if (!panel) return <NoProject />;
  if (panel.observed_days === 0 && panel.totals[1]?.value === 0)
    return <NoTracesYet project={project} />;

  return (
    <main className="reading">
      <section className="hero">
        <h1>{t("panel.titulo", { proyecto: project })}</h1>

        <div className={`verdict ${panel.reading.verdict}`}>
          <b>{panel.reading.headline}</b>
          <p>{panel.reading.detail}</p>
        </div>

        <h2 className="sub">{t("panel.por_ejecucion")}</h2>
        <p className="lead">{t("panel.por_ejecucion.lead")}</p>
        <div className="mgrid seis">
          {panel.per_execution.map((m) => (
            <MetricCard key={m.label} metric={m} currency={panel.currency} destacada />
          ))}
        </div>

        <Chart panel={panel} />

        <h2 className="sub">{t("panel.totales")}</h2>
        <p className="lead">{t("panel.totales.lead")}</p>
        <div className="mgrid small">
          {panel.totals.map((m) => (
            <MetricCard key={m.label} metric={m} currency={panel.currency} />
          ))}
        </div>
      </section>

      <QuienGasta project={project} days={days} currency={panel.currency} />

      <Spikes panel={panel} />
    </main>
  );
}

/**
 * Quién gasta más: por usuario o por sesión (D-123).
 *
 * Las trazas ya traían `user_id` y `session_id` y ninguna pantalla los usaba. «¿Qué
 * cliente me cuesta más?» es la pregunta que se entiende sin saber qué es un span. Las
 * ejecuciones que no dicen de quién son se cuentan aparte, con su cifra: si no, los
 * grupos sumarían menos que el total sin explicarlo.
 */
function QuienGasta({ project, days, currency }: { project: string; days: number; currency: string }) {
  const [by, setBy] = useState<"user" | "session">("user");
  const [datos, setDatos] = useState<Breakdown | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let vigente = true;
    setDatos(null);
    getBreakdown(project, days, by)
      .then((d) => vigente && setDatos(d))
      .catch((e) => vigente && setError(e instanceof Error ? e.message : t("panel.error.cargar")));
    return () => {
      vigente = false;
    };
  }, [project, days, by]);

  const context = `project=${encodeURIComponent(project)}&days=${days}`;
  return (
    <section className="sec">
      <div className="sec-head">
        <h2>{t("panel.quien")}</h2>
        <div className="seg" role="group" aria-label={t("panel.agrupar")}>
          <button type="button" aria-pressed={by === "user"} onClick={() => setBy("user")}>
            {t("panel.usuarios")}
          </button>
          <button type="button" aria-pressed={by === "session"} onClick={() => setBy("session")}>
            {t("panel.sesiones")}
          </button>
        </div>
      </div>
      {error && <p className="verr">{error}</p>}
      {datos && datos.groups.length === 0 && (
        <p className="lead">
          {tr(by === "user" ? "panel.sin_usuario" : "panel.sin_sesion", {
            codigo: <code>laplace.set_context({by === "user" ? "user_id" : "session_id"}=…)</code>,
          })}
        </p>
      )}
      {datos && datos.groups.length > 0 && (
        <>
          <table className="tabla-simple ancha">
            <thead>
              <tr>
                <th>{by === "user" ? t("panel.col.usuario") : t("panel.col.sesion")}</th>
                <th>{t("panel.col.ejecuciones")}</th>
                <th>{t("panel.col.por_ejecucion")}</th>
                <th>{t("panel.col.total")}</th>
                <th>{t("panel.col.del_gasto")}</th>
              </tr>
            </thead>
            <tbody>
              {datos.groups.map((g) => (
                <tr key={g.key}>
                  <td>
                    <Link
                      href={`/trazas?${context}&${by === "user" ? "user" : "session"}=${encodeURIComponent(g.key)}`}
                    >
                      {g.key}
                    </Link>
                  </td>
                  <td className="num">{number(g.traces)}</td>
                  <td className="num">{money(g.cost_per_trace_usd, currency)}</td>
                  <td className="num">
                    {g.unknown_cost_spans > 0 ? "≥ " : ""}
                    {money(g.cost_usd, currency)}
                  </td>
                  <td className="num">
                    {datos.total_cost_usd > 0
                      ? porcentajeDelGasto(g.cost_usd / datos.total_cost_usd)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {datos.untagged_traces > 0 && (
            <p className="disclaimer">
              {t(by === "user" ? "panel.sin_etiqueta_usuario" : "panel.sin_etiqueta_sesion", {
                n: number(datos.untagged_traces),
                coste: money(datos.untagged_cost_usd, currency),
              })}
            </p>
          )}
        </>
      )}
    </section>
  );
}

/**
 * «0 %» para algo que sí ocurre se lee como que no ocurre nada: un usuario que gasta, o
 * un agente que falla de vez en cuando.
 */
function porcentajeDelGasto(ratio: number): string {
  if (ratio > 0 && ratio < 0.001) return t("panel.menos_de", { x: porcentaje(0.001, 1) });
  return porcentaje(ratio, 1);
}

/**
 * Una métrica con su variación.
 *
 * Cuando no hay cifra se enseña «—» y el motivo, nunca un cero: un cero se lee como
 * «cuesta cero», que es una afirmación distinta y casi siempre falsa (D-073).
 */
function MetricCard({
  metric,
  currency,
  destacada,
}: {
  metric: Metric;
  currency: string;
  destacada?: boolean;
}) {
  const cambio = metric.change_ratio;
  // El signo del cambio no es bueno ni malo por sí mismo, pero en un panel de coste
  // subir es la dirección mala y bajar la buena, en todas las métricas que hay aquí.
  const clase = cambio === null ? "" : cambio > 0.1 ? " up" : cambio < -0.1 ? " down" : "";

  return (
    <div className={`mcard${destacada ? " lead" : ""}`}>
      <small>{metric.label}</small>
      {metric.value === null ? (
        <>
          <b className="num none">—</b>
          <span className="why">{metric.unavailable}</span>
        </>
      ) : (
        <>
          <b className="num">{formatear(metric.value, metric.unit, currency)}</b>
          {cambio === null ? (
            <span className="why">{t("panel.sin_comparacion")}</span>
          ) : (
            <span className={`delta${clase}`}>
              {t("panel.vs_anterior", { cambio: `${cambio > 0 ? "+" : ""}${porcentaje(cambio)}` })}
            </span>
          )}
        </>
      )}
    </div>
  );
}

function formatear(value: number, unit: string, currency: string): string {
  if (unit === "money") return money(value, currency);
  if (unit === "tokens") return tokens(Math.round(value));
  if (unit === "duration") return duration(value);
  // Ejecuciones con error: un 0,05 % no es «0 %», que se leería como que no falla nada.
  if (unit === "ratio") return porcentajeDelGasto(value);
  return number(Math.round(value * 10) / 10);
}

/**
 * La serie de coste por ejecución, dibujada a mano en SVG.
 *
 * Sin librería de gráficas: son barras y una línea, y una dependencia de 90 kB para
 * esto sólo añade superficie. Los tramos sin ejecuciones se dejan **en blanco**, no a
 * cero: pintar un cero dibujaría una caída del coste que nadie ha tenido.
 */
function Chart({ panel }: { panel: Panel }) {
  const alto = 130;
  const ancho = 720;
  const conDatos = panel.buckets.filter((b) => b.cost_per_trace_usd !== null);
  if (conDatos.length < 2) return null;

  // El eje empieza un tramo antes del primer dato, no al principio del rango: con once
  // horas de datos en un rango de siete días, las barras quedaban aplastadas contra el
  // borde derecho y el resto era un vacío que no decía nada (D-125). Los vacíos del
  // final se quedan: «no ha llegado nada desde entonces» sí es información.
  const primero = panel.buckets.findIndex((b) => b.traces > 0);
  const tramos = panel.buckets.slice(Math.max(primero - 1, 0));
  const recortado = tramos.length < panel.buckets.length;

  const tope = Math.max(...conDatos.map((b) => b.cost_per_trace_usd ?? 0));
  const topeTrazas = Math.max(...tramos.map((b) => b.traces), 1);
  const paso = ancho / tramos.length;

  return (
    <div className="chart">
      <div className="chart-head">
        <span>{t("panel.grafica.titulo", { tramo: spanLabel(panel.bucket_minutes / 1440) })}</span>
        <span className="legend">
          <i className="bar" /> {t("panel.grafica.coste")} <i className="vol" />{" "}
          {t("panel.grafica.ejecuciones")}
        </span>
      </div>
      <svg viewBox={`0 0 ${ancho} ${alto}`} role="img" aria-label={t("panel.grafica.aria")}>
        {tramos.map((b, i) => {
          const x = i * paso;
          const volumen = (b.traces / topeTrazas) * alto;
          const unitario = b.cost_per_trace_usd;
          const altura = unitario === null ? 0 : (unitario / tope) * (alto - 8);
          return (
            <g key={b.start}>
              {/* El volumen va detrás y en gris: es contexto, no el protagonista. */}
              <rect
                x={x + paso * 0.1}
                y={alto - volumen}
                width={paso * 0.8}
                height={volumen}
                className="vol"
              />
              {unitario !== null && (
                <rect
                  x={x + paso * 0.25}
                  y={alto - altura}
                  width={paso * 0.5}
                  height={Math.max(altura, 1)}
                  className={b.is_spike ? "bar spike" : "bar"}
                >
                  <title>
                    {t("panel.grafica.punto", {
                      fecha: dayHour(b.start),
                      coste: money(unitario),
                      n: b.traces,
                    })}
                  </title>
                </rect>
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-foot">
        <span>
          {dayHour(tramos[0].start)}
          {recortado && t("panel.grafica.desde")}
        </span>
        <span>{dayHour(tramos[tramos.length - 1].start)}</span>
      </div>
      <p className="disclaimer">{t("panel.grafica.nota")}</p>
    </div>
  );
}

/** Los picos, con su atribución o con su «no lo sabemos». */
function Spikes({ panel }: { panel: Panel }) {
  if (panel.spikes.length === 0) {
    return (
      <section className="sec">
        <h2>{t("panel.picos")}</h2>
        <p className="lead">{panel.spikes_unavailable || t("panel.picos.ninguno")}</p>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>
        {tn("panel.picos.n", panel.spikes.length)}
      </h2>
      <p className="lead">{t("panel.picos.lead")}</p>
      {panel.spikes.map((spike) => (
        <SpikeCard key={spike.start} spike={spike} panel={panel} />
      ))}
    </section>
  );
}

function SpikeCard({ spike, panel }: { spike: Spike; panel: Panel }) {
  const filtro = new URLSearchParams({
    ...spike.traces_query,
    project: spike.traces_query.project_id ?? "",
    days: String(panel.days),
  });
  filtro.delete("project_id");

  return (
    <article className="card static">
      <div className="card-top">
        <h3>
          {t("panel.pico.cuando", {
            inicio: dayHour(spike.start),
            fin: new Date(spike.end).toLocaleTimeString(ETIQUETAS[idiomaActual()], {
              hour: "2-digit",
              minute: "2-digit",
            }),
          })}
        </h3>
        <div className="price">
          {money(spike.excess_usd, panel.currency)}
          <small>{t("panel.pico.de_mas")}</small>
        </div>
      </div>
      <p>
        {tr(spike.traces === 1 ? "panel.pico.texto_one" : "panel.pico.texto_other", {
          coste: <strong>{money(spike.cost_per_trace_usd, panel.currency)}</strong>,
          base: money(spike.baseline_cost_per_trace_usd, panel.currency),
          veces: <strong>{t("panel.pico.veces", { n: decimal(spike.times_baseline) })}</strong>,
          n: number(spike.traces),
        })}
      </p>

      {spike.causes.length > 0 ? (
        <ul className="causes">
          {spike.causes.map((c) => (
            <li key={c.text}>
              {c.text}
              {/* La versión de prompt es la única causa con pantalla propia: desde aquí
                  se va a ver el diff y, si hace falta, a volver atrás. */}
              {c.kind === "version_prompt" && (
                <>
                  {" "}
                  <Link href={`/prompts?project=${encodeURIComponent(spike.traces_query.project_id ?? "")}&days=${panel.days}`}>
                    {t("panel.pico.ver_prompt")}
                  </Link>
                </>
              )}
              {c.evidence && <span className="pro"> {c.evidence}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="unattributed">{spike.unattributed}</p>
      )}

      <footer>
        <Link href={`/trazas?${filtro.toString()}`} className="btn">
          {t("panel.pico.ver_trazas")}
        </Link>
        <span className="chip where">
          {t("panel.pico.orden")}
        </span>
      </footer>
      <div className="techline pro">
        <span>
          {t("panel.pico.tramo")} <b>{spike.start}</b>
        </span>
        <span>
          {t("panel.pico.base")} <b>{t("panel.pico.mediana")}</b>
        </span>
      </div>
    </article>
  );
}

export default function PanelPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
