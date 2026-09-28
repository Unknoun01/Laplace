"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BigMoney, FindingCard, GapBar, Readout } from "@/components/pieces";
import { BackendDown, CargandoDiagnostico, NeedsKey, NoProject, NoTracesYet, NotYours, NothingToFix } from "@/components/states";
import { getBudget, getCustomers, getOverview, listProjects, parseDays } from "@/lib/api";
import { descargarCsv } from "@/lib/csv";
import { dayHour, duration, money, number, percent, spanLabel, tokens, windowLabel } from "@/lib/format";
import type { Budget, Finding, MarginView, Overview } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { CasiTodoEvitable, CASI_TODO_EVITABLE, CoberturaBloque, CoberturaLinea, Confianza, SinDinero } from "./avisos";
import { tr, trn } from "@/lib/i18n";
import { Graficos } from "./graficos";
import { t, tn } from "@/lib/textos";

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
    if (projects.length === 0)
      return { project: "", overview: null, budget: null, clientes: null, lastSeen: null };
    const elegido = projects.find((p) => p.id === pedido) ?? projects[0];
    const project = elegido.id;
    const [overview, budget, clientes] = await Promise.all([
      getOverview(project, days, senal),
      // El presupuesto y el margen por cliente son añadidos: si fallan, el inicio sigue
      // en pie sin ellos.
      getBudget(project, senal).catch(() => null),
      getCustomers(project, days, senal).catch(() => null),
    ]);
    return { project, overview, budget, clientes, lastSeen: elegido.last_seen };
  }, [pedido, days]);

  if (estado.fase === "cargando") return <CargandoDiagnostico />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} codigo={estado.error.code} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;

  const { project, overview, budget, clientes, lastSeen } = estado.datos;
  if (!overview) return <NoProject />;
  if (overview.spans === 0)
    return <NoTracesYet project={project} lastSeen={lastSeen} days={days} />;

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
  const destacaAhorro = ahorra && !casiTodo && !overview.cost_unavailable;

  const cobertura = overview.coverage;

  return (
    <main className="diag">
      {/* Si no entendemos bien este proyecto, se dice ANTES que el dinero. Leer un
          ahorro sin saber que está calculado sobre la mitad de las llamadas es peor
          que no leerlo (D-096). */}
      {cobertura?.prominent && <CoberturaBloque cobertura={cobertura} />}

      <section className="hero">
        <div className="hero-top">
          <h1>{t("diag.titulo", { proyecto: project })}</h1>
          <Confianza
            overview={overview}
            ventana={ventana}
            casiTodo={casiTodo}
            coberturaMala={!!cobertura?.prominent}
          />
        </div>
        {!destacaAhorro && (
          <p className="hero-sub">
            {overview.projected
              ? t("diag.al_ritmo", { ventana })
              : t("diag.en_datos", { tiempo: spanLabel(overview.observed_days) })}
          </p>
        )}

        {/* Sin una sola tarifa conocida no hay cifra que enseñar. Un «$0» grande con el
            aviso debajo se lee como «no cuesta nada», que es lo contrario de lo que
            decimos: el número se lee antes que el aviso (D-073, D-107). En su lugar va
            el motivo, y debajo lo que sí está medido. */}
        {overview.cost_unavailable ? (
          <div className="pair">
            <SinDinero motivo={overview.cost_unavailable} />
          </div>
        ) : destacaAhorro ? (
          // Lo primero que se lee es lo que se puede dejar de pagar, no lo que se paga
          // (D-151). El total va debajo, en una frase, y la barra los reparte.
          <div className="puedes">
            <p className="puedes-lead">
              {overview.projected ? t("diag.puedes.proy") : t("diag.puedes.gastado")}
            </p>
            <BigMoney
              amount={evitable}
              currency={overview.currency}
              label={
                overview.projected
                  ? t("diag.puedes.al_mes")
                  : t("diag.puedes.en", { tiempo: spanLabel(overview.observed_days) })
              }
              save
            />
            <p className="hero-sub">
              {overview.projected
                ? t("diag.de_total.proy", { total: money(total, overview.currency), ventana })
                : t("diag.de_total.gastado", {
                    total: money(total, overview.currency),
                    tiempo: spanLabel(overview.observed_days),
                  })}
            </p>
          </div>
        ) : (
        <div className="pair">
          <BigMoney
            amount={total}
            currency={overview.currency}
            label={overview.projected ? t("diag.costara") : t("diag.ha_costado")}
          />
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

        {/* La otra mitad de la cifra: lo ya ahorrado, verificado después de arreglar
            (D-156). Sólo lo que el seguimiento ha medido; nada si no hay. */}
        {overview.saved_usd > 0 && (
          <p className="ahorrado">
            {trn("diag.ahorrado", overview.saved_findings, {
              n: number(overview.saved_findings),
              coste: (
                <strong>
                  {overview.saved_is_floor
                    ? t("diag.ahorrado.suelo", { coste: money(overview.saved_usd, overview.currency) })
                    : money(overview.saved_usd, overview.currency)}
                </strong>
              ),
            })}
          </p>
        )}
      </section>

      {/* El contexto que matiza la cifra, en su carril (D-132). En estrecho va debajo de
          la lista: lo primero, después de la cifra, es qué arreglar. */}
      <aside className="diag-rail" aria-label={t("diag.contexto")}>
        <ClientesQuePierden clientes={clientes} query={query} />

        <LineaPresupuesto budget={budget} query={query} />

        {/* Cuando la cobertura es buena no desaparece: se queda en una línea. Que el
            usuario sepa que esto se mide —y que hoy sale bien— es la mitad de lo que
            hace creíble el aviso el día que salga mal. */}
        {cobertura && !cobertura.prominent && (
          <div className="rail-block">
            <h2>{t("diag.cobertura")}</h2>
            <CoberturaLinea cobertura={cobertura} />
          </div>
        )}

        <div className="rail-block pro">
          <h2>{t("diag.metricas")}</h2>
          <Readout
          items={[
            [duration(overview.p95_duration_ms), t("diag.m.p95")],
            [percent(overview.error_rate), t("diag.m.errores")],
            [tokens(overview.input_tokens + overview.output_tokens), t("diag.m.tokens")],
            [number(overview.traces), t("diag.m.trazas")],
            [number(overview.spans), t("diag.m.pasos")],
            [money(overview.cost_per_trace_usd, overview.currency), t("diag.m.coste")],
            [money(overview.window_cache_saving_usd, overview.currency), t("diag.m.cache")],
            [spanLabel(overview.observed_days), t("diag.m.datos")],
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
                <strong>{cobertura.headline}</strong> {t("diag.nada.aviso")}
              </>
            ) : undefined
          }
        >
          <p style={{ marginTop: 14 }}>
            {t("diag.nada.gastado", {
              coste: money(overview.window_cost_usd, overview.currency),
              ejecuciones: number(overview.traces),
            })}
          </p>
          <div className="actions">
            <Link href={`/trazas${query}`} className="btn">
              {t("diag.ver_trazas")}
            </Link>
          </div>
        </NothingToFix>
      ) : (
        <section className="sec">
          <div className="sec-head">
            <h2>
              {tn("diag.cosas", overview.findings.length)}
            </h2>
            <button
              type="button"
              className="btn small"
              onClick={() => exportarHallazgos(project, overview)}
            >
              {t("comun.exportar_csv")}
            </button>
          </div>
          <p className="lead">
            {t("diag.orden")}{" "}
            {overview.projected
              ? t("diag.cifras_proyeccion", { ventana })
              : t("diag.cifras_gastado", { ventana })}
          </p>
          <ListaProblemas findings={overview.findings} query={query} />
          <p className="disclaimer">
            {overview.projected
              ? t("diag.nota.proyeccion", { ventana })
              : tr("diag.nota.gastado", {
                  ya: <strong>{t("diag.nota.ya_gastado")}</strong>,
                  ventana,
                })}{" "}
            {t("diag.nota.moneda", { moneda: overview.currency })}
          </p>
        </section>
      )}

      {/* Después de qué arreglar, que es lo que se ha venido a ver: el gráfico explica
          de dónde sale la cifra, no la sustituye (D-152). */}
      {overview.chart && !overview.cost_unavailable && (
        <Graficos grafico={overview.chart} currency={overview.currency} />
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
            {tn("diag.ver_mas", resto.length)}
          </summary>
          {resto.map((f, i) => tarjeta(f, VISIBLES + i + 1))}
        </details>
      )}
      {tiempo.length > 0 && (
        <details className="mas tiempo" open={dinero.length === 0}>
          <summary>
            {tn("diag.tiempo", tiempo.length)}
          </summary>
          {tiempo.map((f) => tarjeta(f))}
        </details>
      )}
    </>
  );
}

/**
 * El aviso del margen por cliente (D-161), en el carril y sólo cuando hay algo que
 * avisar: un cliente que te hace perder dinero va por encima del presupuesto, porque es
 * dinero que se pierde, no que se gasta.
 */
function ClientesQuePierden({ clientes, query }: { clientes: MarginView | null; query: string }) {
  if (!clientes || clientes.losing === 0) return null;
  return (
    <div className="rail-block clientes-aviso">
      <h2>{t("diag.clientes")}</h2>
      <p>{tn("diag.clientes.pierden", clientes.losing)}</p>
      <Link href={`/clientes${query}`}>{t("diag.clientes.ver")}</Link>
    </div>
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
        <h2>{t("diag.presupuesto")}</h2>
        <p className="budget-home muted">
          {tr("diag.presupuesto.invitar", {
            enlace: <Link href={`/ajustes${query}`}>{t("diag.presupuesto.enlace")}</Link>,
          })}
        </p>
      </div>
    );
  }
  const pct = Math.min(budget.ratio ?? 0, 1) * 100;
  return (
    <div className="rail-block">
      <h2>{t("diag.presupuesto")}</h2>
      <div className={`budget-home ${budget.status}`}>
        <p>{budget.headline}</p>
        <div className="sbar budget" role="img" aria-label={t("diag.presupuesto.aria", { n: Math.round(pct) })}>
          <i style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

/**
 * Lo que ya no está pendiente: arreglado o ignorado por el usuario (D-123), o que dejó
 * de ocurrir dentro del rango sin que nadie lo marcara (D-135).
 *
 * Plegado y al final. No desaparece del todo porque un ignorado es una decisión que se
 * puede querer revisar, y un arreglado lleva la cifra que demuestra que sirvió.
 */
const ETIQUETA_ESTADO = {
  arreglado: "estado.arreglado",
  ignorado: "estado.ignorado",
  desaparecido: "estado.desaparecido",
} as const;

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
        {tn("diag.apartados", findings.length)}
      </summary>
      <ul>
        {findings.map((f) => (
          <li key={f.id}>
            <span className={`chip ${f.state === "arreglado" ? "easy" : "where"}`}>
              {t(ETIQUETA_ESTADO[f.state as keyof typeof ETIQUETA_ESTADO] ?? "estado.arreglado")}
            </span>{" "}
            <Link href={`/problema${query}&id=${encodeURIComponent(f.id)}`}>{f.title}</Link>
            {f.state === "desaparecido" && f.last_seen && (
              <small>{t("diag.desaparecido", { fecha: dayHour(f.last_seen) })}</small>
            )}
            {f.fix_check && <small>{f.fix_check.headline}</small>}
            {f.state === "ignorado" && f.state_note && <small>«{f.state_note}»</small>}
            {!f.fix_check && f.state === "ignorado" && f.costs_money && (
              <small>{t("diag.ignorado_coste", { coste: money(f.window_waste_usd, currency) })}</small>
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
    f.state || t("csv.abierto"),
    f.costs_money ? f.window_waste_usd : null,
    f.costs_money ? f.monthly_saving_usd : null,
    f.cost_is_floor ? t("csv.si") : t("csv.no"),
    f.window_waste_tokens || null,
    f.window_waste_ms ? f.window_waste_ms / 1000 : null,
    f.scope_label,
    f.difficulty_label,
  ]);
  descargarCsv(
    `laplace-${project}-${t("csv.nombre.problemas")}`,
    [
      t("csv.h.problema"),
      t("csv.h.tipo"),
      t("csv.h.estado"),
      t("csv.h.gastado"),
      t("csv.h.al_mes"),
      t("csv.h.suelo"),
      t("csv.h.tokens"),
      t("csv.h.espera"),
      t("csv.h.ejecuciones"),
      t("csv.h.arreglo"),
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
