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
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, finding, trace } = estado.datos;
  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  if (!finding) {
    return (
      <NotFound
        title="Ese problema ya no aparece"
        body="O lo has arreglado, o ha dejado de darse en el rango que estás mirando. Enhorabuena en cualquiera de los dos casos."
        back={`/${query}`}
      />
    );
  }

  const flaggedHash = finding.tech.find((t) => t.label === "laplace.dedup_hash")?.value;

  return (
    <main className="reading">
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
        Volver
      </Link>

      <Head finding={finding} />

      <section className="block">
        <h2>Qué está pasando</h2>
        <p>{finding.what_happens}</p>
        {finding.evidence.length > 0 && <Repetitions spans={finding.evidence} bucle={finding.kind === "bucle"} />}
      </section>

      <section className="block">
        <h2>Por qué pasa</h2>
        {finding.why.split("\n\n").map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </section>

      <section className="block pro">
        <h2>Cómo lo hemos detectado</h2>
        <Markup text={finding.detection_explanation} />
        <pre className="wrapless">{finding.detection_query}</pre>
        <p style={{ marginTop: 12 }}>
          Es la consulta que se ha ejecutado, no una versión de ejemplo. Los umbrales de
          las reglas son los mismos para todos los proyectos: en Ajustes se decide qué avisa,
          no qué se detecta.
        </p>
      </section>

      {trace && (
        <section className="block pro">
          <h2>La traza completa</h2>
          <p>
            Una ejecución real donde se ve el problema.{" "}
            <Link href={`/traza${query}&id=${trace.summary.trace_id}`}>
              Ábrela para navegarla paso a paso →
            </Link>
          </p>
          <StaticTree trace={trace} highlight={flaggedHash} />
        </section>
      )}

      {finding.evidence.length > 0 && (
        <section className="block pro">
          <h2>Atributos del paso señalado</h2>
          <SpanAttributes span={finding.evidence[0]} />
        </section>
      )}

      <section className="block">
        <h2>Cómo arreglarlo</h2>
        <ol className="fix">
          {finding.fix_steps.map((step) => (
            <li key={step.title} className={step.advanced ? "pro" : undefined}>
              <b>{step.title}.</b> {step.body}
              {step.code && <pre>{step.code}</pre>}
            </li>
          ))}
        </ol>
      </section>

      <section className="block">
        <h2>Cuánto te ahorras</h2>
        {finding.costs_money ? (
          <p>
            En {windowLabel(finding.observed_days)} ya se han ido{" "}
            <strong>{money(finding.window_waste_usd, finding.currency)}</strong> por aquí.
            {finding.monthly_saving_usd !== null ? (
              <>
                {" "}
                A ese ritmo, arreglarlo te deja de costar{" "}
                <strong>
                  {money(finding.monthly_saving_usd, finding.currency)} al mes
                </strong>
                .
              </>
            ) : (
              <>
                {" "}
                Todavía no proyectamos el mes: con {spanLabel(finding.observed_days)} de
                datos, multiplicar hasta 30 días daría una cifra que no se sostiene.
              </>
            )}
            {finding.cost_is_floor && (
              <>
                {" "}
                Y es un <strong>suelo</strong>: hay pasos aquí dentro cuyo coste no
                podemos confirmar, así que la cifra real puede ser mayor, nunca menor.
              </>
            )}
          </p>
        ) : (
          <p>
            {finding.window_waste_tokens > 0 ? (
              <>
                {/* Gasta tokens de verdad; lo que no sabemos es el precio. Decir «no te
                    cuesta dinero» aquí sería convertir «no lo sabemos» en «es gratis». */}
                Esto gasta <strong>{tokens(finding.window_waste_tokens)} tokens</strong> de
                más y <strong>{duration(finding.window_waste_ms)}</strong> de espera en el
                rango analizado. Cuánto dinero es, no lo sabemos:{" "}
                {finding.cost_unavailable || "ese modelo no tiene tarifa conocida"}.
              </>
            ) : (
              <>
                Este problema no te cuesta dinero: los pasos repetidos no consumen tokens.
                Lo que te cuesta es espera,{" "}
                <strong>{duration(finding.window_waste_ms)}</strong> en el rango analizado.
                Arreglarlo hace que tu agente responda antes.
              </>
            )}
          </p>
        )}
        {finding.savings_calculation && (
          <p className="pro">
            <span style={{ color: "var(--ink-3)" }}>Cálculo: </span>
            {finding.savings_calculation}
          </p>
        )}
        <p className="disclaimer">{finding.savings_note}</p>
      </section>

      {/* Marcar, ignorar y crear conjuntos es escribir: un lector no lo ve ofrecido
          (D-127). El backend lo rechazaría igual. */}
      {permisos.escribir && finding.kind === "modelo_caro" && (
        <ProbarAntes project={project} finding={finding} query={query} />
      )}

      {permisos.escribir ? (
        <EstadoHallazgo project={project} finding={finding} />
      ) : (
        <p className="muted solo-lectura">
          Tu rol en este proyecto ({permisos.rol}) permite verlo, no marcarlo como arreglado
          ni ignorarlo.
        </p>
      )}

      <div className="actions">
        <Link href={`/trazas${query}&${pasoParams(finding)}&sort=cost`} className="btn">
          Ver las trazas afectadas
        </Link>
        <Link href={`/trazas${query}&${pasoParams(finding)}&sort=recent`} className="btn">
          Ver las últimas ejecuciones de este paso
        </Link>
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
