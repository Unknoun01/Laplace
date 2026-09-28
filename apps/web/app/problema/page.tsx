"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { StaticTree } from "@/components/StaticTree";
import { BackendDown, NeedsKey, NotFound, NotYours, TableSkeleton } from "@/components/states";
import { getFinding, getTrace, listProjects, parseDays } from "@/lib/api";
import { duration, money, spanLabel, tokens, windowLabel } from "@/lib/format";
import type { FindingDetail } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { useTitulo } from "@/lib/titulo";
import { useApi } from "@/lib/useApi";
import { Head, Repetitions, SpanAttributes, Markup } from "./tecnico";
import { EstadoHallazgo, ProbarAntes } from "./seguimiento";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

/**
 * Ficha de un problema: qué pasa → por qué → (avanzado: cómo lo hemos detectado,
 * la traza y los atributos) → cómo arreglarlo → cuánto te ahorras.
 */
function Contenido() {
  const params = useSearchParams();
  const findingId = params.get("id") ?? "";
  const pedido = params.get("project") ?? "";
  const days = parseDays(params.get("days") ?? undefined);
  const permisos = usePermisos(pedido);

  const estado = useApi(async (senal) => {
    const projects = await listProjects(senal);
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0]?.id ?? "";
    const finding = await getFinding(findingId, project, days, senal);
    const trace = finding?.sample_trace_id
      ? await getTrace(finding.sample_trace_id, project, senal)
      : null;
    return { project, finding, trace };
  }, [findingId, pedido, days]);
  useTitulo(estado.fase === "listo" ? estado.datos.finding?.title : null);

  if (estado.fase === "cargando") return <TableSkeleton />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} codigo={estado.error.code} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, finding, trace } = estado.datos;
  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  if (!finding) {
    return (
      <NotFound
        title={t("prob.no_aparece.titulo")}
        body={t("prob.no_aparece.texto")}
        back={`/${query}`}
      />
    );
  }

  const flaggedHash = finding.tech.find((t) => t.label === "laplace.dedup_hash")?.value;

  return (
    <main className="reading problema">
      <Link href={`/${query}`} className="back">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
          <path
            d="M8.5 3 L4.5 7 L8.5 11"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {t("estado.volver")}
      </Link>

      <Head finding={finding} />

      <div className="prob-cols">
      <div className="prob-cuerpo">
      <section className="block">
        <h2>{t("prob.que_pasa")}</h2>
        <p>{finding.what_happens}</p>
        {finding.evidence.length > 0 && <Repetitions spans={finding.evidence} bucle={finding.kind === "bucle"} />}
      </section>

      {/* Una frase arriba (qué pasa) y el porqué plegado (D-151): son tres párrafos
          entre lo que pasa y cómo arreglarlo, y quien quiere arreglarlo ya lo sabe. */}
      <details className="block porque-bloque">
        <summary>{t("prob.por_que.plegado")}</summary>
        {finding.why.split("\n\n").map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </details>

      <section className="block pro">
        <h2>{t("prob.deteccion")}</h2>
        <Markup text={finding.detection_explanation} />
        <pre className="wrapless">{finding.detection_query}</pre>
        <p style={{ marginTop: 12 }}>{t("prob.deteccion.nota")}</p>
      </section>

      {trace && (
        <section className="block pro">
          <h2>{t("prob.traza")}</h2>
          <p>
            {t("prob.traza.texto")}{" "}
            <Link href={`/traza${query}&id=${trace.summary.trace_id}`}>
              {t("prob.traza.abrir")}
            </Link>
          </p>
          <StaticTree trace={trace} highlight={flaggedHash} />
        </section>
      )}

      {finding.evidence.length > 0 && (
        <section className="block pro">
          <h2>{t("prob.atributos")}</h2>
          <SpanAttributes span={finding.evidence[0]} />
        </section>
      )}

      <section className="block">
        <h2>{t("prob.arreglo")}</h2>
        <ol className="fix">
          {finding.fix_steps.map((step) => (
            <li key={step.title} className={step.advanced ? "pro" : undefined}>
              <b>{t("prob.paso_titulo", { titulo: step.title })}</b> {step.body}
              {step.code && <pre>{step.code}</pre>}
            </li>
          ))}
        </ol>
      </section>

      <section className="block">
        <h2>{t("prob.ahorro")}</h2>
        {finding.costs_money ? (
          <p>
            {tr("prob.ahorro.ya", {
              ventana: windowLabel(finding.observed_days),
              coste: <strong>{money(finding.window_waste_usd, finding.currency)}</strong>,
            })}{" "}
            {finding.monthly_saving_usd !== null
              ? tr("prob.ahorro.mes", {
                  coste: (
                    <strong>
                      {t("prob.ahorro.al_mes", {
                        coste: money(finding.monthly_saving_usd, finding.currency),
                      })}
                    </strong>
                  ),
                })
              : t("prob.ahorro.sin_mes", { tiempo: spanLabel(finding.observed_days) })}
            {finding.cost_is_floor && (
              <>
                {" "}
                {tr("prob.ahorro.suelo", {
                  suelo: <strong>{t("prob.ahorro.suelo.palabra")}</strong>,
                })}
              </>
            )}
            {finding.cost_unverified && (
              <>
                {" "}
                {t("prob.ahorro.sin_verificar", {
                  modelos: finding.unverified_rate_models.join(", "),
                })}
              </>
            )}
          </p>
        ) : (
          <p>
            {finding.window_waste_tokens > 0 ? (
              <>
                {/* Gasta tokens de verdad; lo que no sabemos es el precio. Decir «no te
                    cuesta dinero» aquí sería convertir «no lo sabemos» en «es gratis». */}
                {tr("prob.ahorro.tokens", {
                  tokens: (
                    <strong>
                      {t("prob.ahorro.tokens.n", { n: tokens(finding.window_waste_tokens) })}
                    </strong>
                  ),
                  espera: <strong>{duration(finding.window_waste_ms)}</strong>,
                  motivo: finding.cost_unavailable || t("prob.ahorro.sin_tarifa"),
                })}
              </>
            ) : (
              <>
                {tr("prob.ahorro.tiempo", {
                  espera: <strong>{duration(finding.window_waste_ms)}</strong>,
                })}
              </>
            )}
          </p>
        )}
        <details className="porque pregunta">
          <summary>{t("prob.ahorro.como")}</summary>
          {finding.savings_calculation && (
            <p className="pro">
              <span style={{ color: "var(--ink-3)" }}>{t("prob.calculo")} </span>
              {finding.savings_calculation}
            </p>
          )}
          <p className="disclaimer">{finding.savings_note}</p>
        </details>
      </section>
      </div>

      {/* Lo que se hace con el problema, al lado de lo que se lee (D-150). En estrecho
          cae debajo, donde estaba. */}
      <aside className="prob-rail">

      {/* Marcar, ignorar y crear conjuntos es escribir: un lector no lo ve ofrecido
          (D-127). El backend lo rechazaría igual. */}
      {permisos.escribir && finding.kind === "modelo_caro" && (
        <ProbarAntes project={project} finding={finding} query={query} />
      )}

      {permisos.escribir ? (
        <EstadoHallazgo project={project} finding={finding} />
      ) : (
        <p className="muted solo-lectura">
          {t("prob.solo_lectura", { rol: permisos.rol ?? "" })}
        </p>
      )}

      <div className="actions">
        <Link href={`/trazas${query}&${pasoParams(finding)}&sort=cost`} className="btn">
          {t("prob.ver_afectadas")}
        </Link>
        <Link href={`/trazas${query}&${pasoParams(finding)}&sort=recent`} className="btn">
          {t("prob.ver_ultimas")}
        </Link>
      </div>
      </aside>
      </div>
    </main>
  );
}

/**
 * El filtro del explorador para las trazas de este paso.
 *
 * Por identidad exacta y no por texto: el título de un hallazgo lleva el llamante o
 * una pista del prompt y no se encontraba, y el texto a secas confundía
 * «consultar_manual» con «consultar_manual_cacheado».
 */
function pasoParams(finding: FindingDetail): string {
  const next = new URLSearchParams();
  next.set("step", finding.step_key);
  next.set("step_label", finding.title);
  return next.toString();
}

/**
 * `useSearchParams` obliga a un límite de Suspense para poder exportar la página como
 * HTML estático: sin él, Next no sabe qué pintar antes de que el navegador conozca la
 * URL. El esqueleto es el mismo que se ve mientras llegan los datos, así que no hay
 * dos saltos.
 */
export default function ProblemaPage() {
  return (
    <Suspense fallback={<TableSkeleton />}>
      <Contenido />
    </Suspense>
  );
}
