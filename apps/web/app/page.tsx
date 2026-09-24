"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BigMoney, FindingCard, GapBar, Readout } from "@/components/pieces";
import { BackendDown, CargandoDiagnostico, NeedsKey, NoProject, NoTracesYet, NotYours, NothingToFix } from "@/components/states";
import { getBudget, getOverview, listProjects, parseDays } from "@/lib/api";
import { descargarCsv } from "@/lib/csv";
import { duration, money, number, percent, spanLabel, tokens, windowLabel } from "@/lib/format";
import type { Budget, Finding, Overview } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { CasiTodoEvitable, CASI_TODO_EVITABLE, CoberturaBloque, CoberturaLinea, Caveats, SinDinero } from "./avisos";

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

  const estado = useApi(async (senal) => {
    const projects = await listProjects(senal);
    if (projects.length === 0) return { project: "", overview: null, budget: null };
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    const [overview, budget] = await Promise.all([
      getOverview(project, days, senal),
      // El presupuesto es un añadido: si falla, el inicio sigue en pie sin él.
      getBudget(project, senal).catch(() => null),
    ]);
    return { project, overview, budget };
  }, [pedido, days]);

  if (estado.fase === "cargando") return <CargandoDiagnostico />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, overview, budget } = estado.datos;
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
  // Cuando el evitable se come casi todo el gasto, la pareja «X → Y» y la barra de
  // reparto dejan de decir lo que parecen: una barra con un lado en cero no es una
  // barra, y «puedes dejar de pagar tu agente entero» es la clase de cifra que hace
  // que alguien cierre la pestaña. Se enseña el gasto y se explica lo que pasa.
  const casiTodo = ahorra && total > 0 && evitable / total >= CASI_TODO_EVITABLE;

  const cobertura = overview.coverage;

  return (
    <main className="diag">
      {/* Si no entendemos bien este proyecto, se dice ANTES que el dinero. Leer un
          ahorro sin saber que está calculado sobre la mitad de las llamadas es peor
          que no leerlo (D-096). */}
      {cobertura?.prominent && <CoberturaBloque cobertura={cobertura} />}

      <section className="hero">
        <h1>Tu agente «{project}»</h1>
        <p className="hero-sub">
          {overview.projected
            ? `Al ritmo de ${ventana}`
            : `En ${spanLabel(overview.observed_days)} de datos`}
        </p>

        {/* Sin una sola tarifa conocida no hay cifra que enseñar. Un «$0» grande con el
            aviso debajo se lee como «no cuesta nada», que es lo contrario de lo que
            decimos: el número se lee antes que el aviso (D-073, D-107). En su lugar va
            el motivo, y debajo lo que sí está medido. */}
        {overview.cost_unavailable ? (
          <div className="pair">
            <SinDinero motivo={overview.cost_unavailable} />
          </div>
        ) : (
        <div className="pair">
          <BigMoney
            amount={total}
            currency={overview.currency}
            label={overview.projected ? "te costará este mes" : "te ha costado hasta ahora"}
          />
          {ahorra && !casiTodo && (
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
        )}

        {ahorra && !overview.cost_unavailable && !casiTodo && (
          <GapBar
            necessary={necesario}
            avoidable={evitable}
            currency={overview.currency}
          />
        )}

        {casiTodo && !overview.cost_unavailable && (
          <CasiTodoEvitable overview={overview} total={total} evitable={evitable} />
        )}

        <Caveats overview={overview} ventana={ventana} casiTodo={casiTodo} />
      </section>

      {/* El contexto que matiza la cifra, en su carril (D-132). En estrecho cae justo
          debajo del héroe, que es donde estaba. */}
      <aside className="diag-rail" aria-label="Contexto">
        <LineaPresupuesto budget={budget} query={query} />

        {/* Cuando la cobertura es buena no desaparece: se queda en una línea. Que el
            usuario sepa que esto se mide —y que hoy sale bien— es la mitad de lo que
            hace creíble el aviso el día que salga mal. */}
        {cobertura && !cobertura.prominent && (
          <div className="rail-block">
            <h2>Cobertura</h2>
            <CoberturaLinea cobertura={cobertura} />
          </div>
        )}

        <div className="rail-block pro">
          <h2>Métricas</h2>
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
        </div>
      </aside>

      <div className="diag-lista">
      {overview.findings.length === 0 ? (
        <NothingToFix
          aviso={
            cobertura?.prominent ? (
              <>
                <strong>{cobertura.headline}</strong> Con esa cobertura, «no hay nada que
                arreglar» significa «no lo sabemos», no «está bien».
              </>
            ) : undefined
          }
        >
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
          <div className="sec-head">
            <h2>
              {overview.findings.length === 1
                ? "Una cosa que arreglar"
                : `${overview.findings.length} cosas que arreglar`}
            </h2>
            <button
              type="button"
              className="btn small"
              onClick={() => exportarHallazgos(project, overview)}
            >
              Exportar CSV
            </button>
          </div>
          <p className="lead">
            De la que más dinero te devuelve a la que menos.{" "}
            {overview.projected
              ? `Las cifras son la proyección a 30 días de ${ventana}.`
              : `Las cifras son dinero ya gastado en ${ventana}.`}
          </p>
          <ListaProblemas findings={overview.findings} query={query} />
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

      <Apartados findings={overview.set_aside} query={query} currency={overview.currency} />
      </div>
    </main>
  );
}

/**
 * Los problemas, con las tres primeras acciones a la vista (D-125).
 *
 * Primero lo que cuesta dinero —o tokens, cuando no hay tarifa—, ordenado por lo que
 * devuelve; las tres primeras se ven, el resto se despliega. Lo que sólo cuesta tiempo
 * va en su propio bloque: mezclado con el dinero, «531 ms» competía con «$1,21» por la
 * misma atención y no se comparan.
 */
const VISIBLES = 3;

function ListaProblemas({ findings, query }: { findings: Finding[]; query: string }) {
  const tiempo = findings.filter((f) => !f.costs_money && !f.window_waste_tokens);
  const dinero = findings.filter((f) => !tiempo.includes(f));
  // El peso se compara con la misma cifra que enseña la tarjeta, y sólo entre las que
  // tienen precio: tokens y dinero no van en la misma barra.
  const importe = (f: Finding) =>
    f.costs_money ? f.monthly_saving_usd ?? f.window_waste_usd : null;
  const mayor = Math.max(0, ...dinero.map((f) => importe(f) ?? 0));
  const tarjeta = (f: Finding, puesto?: number) => {
    const valor = importe(f);
    return (
      <FindingCard
        key={f.id}
        finding={f}
        href={`/problema${query}&id=${encodeURIComponent(f.id)}`}
        rank={puesto}
        share={valor !== null && mayor > 0 ? valor / mayor : undefined}
      />
    );
  };
  const resto = dinero.slice(VISIBLES);
  return (
    <>
      {dinero.slice(0, VISIBLES).map((f, i) => tarjeta(f, i + 1))}
      {resto.length > 0 && (
        <details className="mas">
          <summary>
            {resto.length === 1 ? "Ver uno más" : `Ver ${resto.length} más`}
          </summary>
          {resto.map((f, i) => tarjeta(f, VISIBLES + i + 1))}
        </details>
      )}
      {tiempo.length > 0 && (
        <details className="mas tiempo" open={dinero.length === 0}>
          <summary>
            {tiempo.length === 1
              ? "Y uno que no cuesta dinero, sólo tiempo"
              : `Y ${tiempo.length} que no cuestan dinero, sólo tiempo`}
          </summary>
          {tiempo.map((f) => tarjeta(f))}
        </details>
      )}
    </>
  );
}

/**
 * El presupuesto, en una línea, cuando hay uno puesto (D-123). Sin presupuesto, una
 * invitación discreta: es la pregunta de quien paga y no se puede hacer si no se sabe
 * que existe.
 */
function LineaPresupuesto({ budget, query }: { budget: Budget | null; query: string }) {
  if (!budget) return null;
  if (budget.monthly_usd === null) {
    return (
      <div className="rail-block">
        <h2>Presupuesto</h2>
        <p className="budget-home muted">
          <Link href={`/ajustes${query}`}>Ponle un presupuesto mensual</Link> y te avisamos
          antes de pasarte.
        </p>
      </div>
    );
  }
  const pct = Math.min(budget.ratio ?? 0, 1) * 100;
  return (
    <div className="rail-block">
      <h2>Presupuesto</h2>
      <div className={`budget-home ${budget.status}`}>
        <p>{budget.headline}</p>
        <div className="sbar budget" role="img" aria-label={`${Math.round(pct)} por ciento del presupuesto`}>
          <i style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

/**
 * Lo que el usuario ya ha resuelto: arreglado o ignorado (D-123).
 *
 * Plegado y al final. No desaparece del todo porque un ignorado es una decisión que se
 * puede querer revisar, y un arreglado lleva la cifra que demuestra que sirvió.
 */
function Apartados({
  findings,
  query,
  currency,
}: {
  findings: Finding[];
  query: string;
  currency: string;
}) {
  if (!findings || findings.length === 0) return null;
  return (
    <details className="apartados sec">
      <summary>
        {findings.length === 1
          ? "Un problema marcado como arreglado o ignorado"
          : `${findings.length} problemas marcados como arreglados o ignorados`}
      </summary>
      <ul>
        {findings.map((f) => (
          <li key={f.id}>
            <span className={`chip ${f.state === "ignorado" ? "where" : "easy"}`}>
              {f.state === "ignorado" ? "Ignorado" : "Arreglado"}
            </span>{" "}
            <Link href={`/problema${query}&id=${encodeURIComponent(f.id)}`}>{f.title}</Link>
            {f.fix_check && <small>{f.fix_check.headline}</small>}
            {f.state === "ignorado" && f.state_note && <small>«{f.state_note}»</small>}
            {!f.fix_check && f.state === "ignorado" && f.costs_money && (
              <small>{money(f.window_waste_usd, currency)} en el rango, que no cuentan como evitable.</small>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function exportarHallazgos(project: string, overview: Overview) {
  const filas = [...overview.findings, ...overview.set_aside].map((f) => [
    f.title,
    f.kind,
    f.state || "abierto",
    f.costs_money ? f.window_waste_usd : null,
    f.costs_money ? f.monthly_saving_usd : null,
    f.cost_is_floor ? "sí" : "no",
    f.window_waste_tokens || null,
    f.window_waste_ms ? f.window_waste_ms / 1000 : null,
    f.scope_label,
    f.difficulty_label,
  ]);
  descargarCsv(
    `laplace-${project}-problemas`,
    [
      "Problema",
      "Tipo",
      "Estado",
      "Ya gastado (USD)",
      "Al mes (USD)",
      "Es un suelo",
      "Tokens de más",
      "Espera evitable (s)",
      "Ejecuciones afectadas",
      "Arreglo",
    ],
    filas,
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
    <Suspense fallback={<CargandoDiagnostico />}>
      <Contenido />
    </Suspense>
  );
}
