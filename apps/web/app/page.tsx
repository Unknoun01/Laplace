"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BigMoney, FindingCard, GapBar, Readout } from "@/components/pieces";
import { BackendDown, Cargando, NoProject, NoTracesYet, NothingToFix } from "@/components/states";
import { getOverview, listProjects, parseDays } from "@/lib/api";
import { duration, money, number, percent, spanLabel, tokens, windowLabel } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * Inicio: el dinero primero.
 *
 * Lo primero que ve cualquiera es cuánto cuesta su agente y cuánto puede dejar de
 * costar. Todo lo demás cuelga de ahí. La capa técnica (latencia, tokens, spans) sólo
 * aparece en modo avanzado.
 */
function Contenido() {
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  const days = parseDays(params.get("days") ?? undefined);

  const estado = useApi(async () => {
    const projects = await listProjects();
    if (projects.length === 0) return { project: "", overview: null };
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    return { project, overview: await getOverview(project, days) };
  }, [pedido, days]);

  if (estado.fase === "cargando") return <Cargando />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, overview } = estado.datos;
  if (!overview) return <NoProject />;
  if (overview.spans === 0) return <NoTracesYet project={project} />;

  const query = `?project=${encodeURIComponent(project)}&days=${days}`;
  const ventana = windowLabel(overview.observed_days);

  // Proyectar o no proyectar cambia las DOS cifras a la vez, nunca una sola: gasto y
  // ahorro salen siempre de la misma base (D-058, D-073).
  const total = overview.projected ? overview.monthly_cost_usd! : overview.window_cost_usd;
  const necesario = overview.projected
    ? overview.monthly_necessary_usd!
    : overview.window_necessary_usd;
  const evitable = overview.projected
    ? overview.monthly_avoidable_usd!
    : overview.window_avoidable_usd;
  const ahorra = evitable > 0;

  return (
    <main className="reading">
      <section className="hero">
        <h1>
          Tu agente «{project}»,{" "}
          {overview.projected
            ? `al ritmo de ${ventana}`
            : `en ${spanLabel(overview.observed_days)} de datos`}
        </h1>

        <div className="pair">
          <BigMoney
            amount={total}
            currency={overview.currency}
            label={overview.projected ? "te costará este mes" : "te ha costado hasta ahora"}
          />
          {ahorra && (
            <>
              <div className="arrow" aria-hidden>
                →
              </div>
              <BigMoney
                amount={necesario}
                currency={overview.currency}
                label="si arreglas lo de abajo"
                good
              />
            </>
          )}
        </div>

        {ahorra && (
          <GapBar
            necessary={necesario}
            avoidable={evitable}
            currency={overview.currency}
          />
        )}

        <Caveats overview={overview} ventana={ventana} />

        <Readout
          items={[
            [duration(overview.p95_duration_ms), "Latencia p95"],
            [percent(overview.error_rate), "Ejecuciones con error"],
            [tokens(overview.input_tokens + overview.output_tokens), "Tokens"],
            [number(overview.traces), "Trazas"],
            [number(overview.spans), "Pasos"],
            [money(overview.cost_per_trace_usd, overview.currency), "Coste por ejecución"],
            [
              money(overview.window_cache_saving_usd, overview.currency),
              "Ya ahorrado por la caché",
            ],
            [spanLabel(overview.observed_days), "Datos observados"],
          ]}
        />
      </section>

      {overview.findings.length === 0 ? (
        <NothingToFix>
          <p style={{ marginTop: 14 }}>
            Llevas {money(overview.window_cost_usd, overview.currency)} gastados en{" "}
            {number(overview.traces)} ejecuciones.
          </p>
          <div className="actions">
            <Link href={`/trazas${query}`} className="btn">
              Ver todas las trazas
            </Link>
          </div>
        </NothingToFix>
      ) : (
        <section className="sec">
          <h2>
            {overview.findings.length === 1
              ? "Una cosa que arreglar"
              : `${overview.findings.length} cosas que arreglar`}
          </h2>
          <p className="lead">
            De la que más dinero te devuelve a la que menos.{" "}
            {overview.projected
              ? `Las cifras son la proyección a 30 días de ${ventana}.`
              : `Las cifras son dinero ya gastado en ${ventana}.`}
          </p>
          {overview.findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              href={`/problema${query}&id=${encodeURIComponent(finding.id)}`}
            />
          ))}
          <p className="disclaimer">
            {overview.projected ? (
              <>
                Los importes son una estimación a partir de {ventana} de datos, proyectada
                a 30 días. Si tu tráfico cambia, cambian.
              </>
            ) : (
              <>
                Los importes son dinero <strong>ya gastado</strong> en {ventana}, no una
                proyección: no hay datos suficientes para estimar el mes.
              </>
            )}{" "}
            El coste está en {overview.currency} porque es la moneda en la que facturan
            los proveedores.
          </p>
        </section>
      )}
    </main>
  );
}

/**
 * Avisos que acompañan a la cifra grande.
 *
 * Una cifra de ahorro que nadie se cree no vende nada: cuando el evitable es casi todo
 * el gasto, o cuando la proyección sale de unas horas de datos, se dice aquí mismo en
 * lugar de presentarlo como una promesa.
 */
function Caveats({ overview, ventana }: { overview: Overview; ventana: string }) {
  const avisos: React.ReactNode[] = [];

  if (overview.unknown_cost_spans > 0) {
    avisos.push(
      <>
        <strong>Este total está incompleto.</strong> Hay {overview.unknown_cost_spans} pasos
        cuyo modelo no está en nuestra tabla de precios, así que no sabemos cuánto cuestan y
        no se suman: {overview.models_without_price.join(", ")}.
      </>,
    );
  }
  if (overview.assumed_rate_spans > 0) {
    avisos.push(
      <>
        En {overview.assumed_rate_spans}{" "}
        {overview.assumed_rate_spans === 1 ? "paso no hemos podido" : "pasos no hemos podido"}{" "}
        confirmar a qué tarifa se facturó —contexto largo, residencia de datos o modo
        rápido son metros aparte que la respuesta del proveedor no siempre revela—, así
        que <strong>hemos cobrado la estándar</strong>. Lo que ves es un suelo: el coste
        real puede ser algo mayor, nunca menor. En cada paso, en modo avanzado, se dice
        cuál es la duda.
      </>,
    );
  }
  if (overview.projected) {
    avisos.push(
      <>
        La cifra del mes se proyecta desde <strong>{ventana}</strong> de datos, que es lo
        que llevas enviando, no el rango que pide el selector. Gasto y ahorro salen de la
        misma base: si cambia una, cambia la otra.
      </>,
    );
  } else {
    avisos.push(
      <>
        <strong>Todavía no proyectamos el mes.</strong> Con {ventana} de datos, multiplicar
        para llegar a 30 días da una cifra que no se sostiene: un pico de diez minutos se
        convertiría en cientos de dólares. Lo que ves es dinero ya gastado, medido. En
        cuanto tengas {spanLabel(overview.min_days_for_projection)} de datos aparece aquí
        la previsión mensual.
      </>,
    );
  }
  if (overview.savings_needs_caution) {
    avisos.push(
      <>
        El ahorro estimado es el{" "}
        <strong>{Math.round(overview.avoidable_ratio * 100)} %</strong> de lo que
        gastas. Es mucho: suele pasar en agentes pequeños o recién estrenados, donde unos
        pocos pasos dominan la factura. Antes de darlo por bueno, mira el desglose de cada
        problema.
      </>,
    );
  }

  if (avisos.length === 0) return null;
  return (
    <div className="caveats">
      {avisos.map((aviso, index) => (
        <p key={index}>{aviso}</p>
      ))}
    </div>
  );
}


/**
 * `useSearchParams` obliga a un límite de Suspense para poder exportar la página como
 * HTML estático: sin él, Next no sabe qué pintar antes de que el navegador conozca la
 * URL. El esqueleto es el mismo que se ve mientras llegan los datos, así que no hay
 * dos saltos.
 */
export default function DiagnosticoPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
