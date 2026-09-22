"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BigMoney, FindingCard, GapBar, Readout } from "@/components/pieces";
import { BackendDown, Cargando, NeedsKey, NoProject, NoTracesYet, NotYours, NothingToFix } from "@/components/states";
import { getOverview, listProjects, parseDays } from "@/lib/api";
import { duration, money, number, percent, spanLabel, tokens, windowLabel } from "@/lib/format";
import type { Coverage, Overview } from "@/lib/types";
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
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
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
  // Cuando el evitable se come casi todo el gasto, la pareja «X → Y» y la barra de
  // reparto dejan de decir lo que parecen: una barra con un lado en cero no es una
  // barra, y «puedes dejar de pagar tu agente entero» es la clase de cifra que hace
  // que alguien cierre la pestaña. Se enseña el gasto y se explica lo que pasa.
  const casiTodo = ahorra && total > 0 && evitable / total >= CASI_TODO_EVITABLE;

  const cobertura = overview.coverage;

  return (
    <main className="reading">
      {/* Si no entendemos bien este proyecto, se dice ANTES que el dinero. Leer un
          ahorro sin saber que está calculado sobre la mitad de las llamadas es peor
          que no leerlo (D-096). */}
      {cobertura?.prominent && <CoberturaBloque cobertura={cobertura} />}

      <section className="hero">
        <h1>
          Tu agente «{project}»,{" "}
          {overview.projected
            ? `al ritmo de ${ventana}`
            : `en ${spanLabel(overview.observed_days)} de datos`}
        </h1>

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

        {/* Cuando la cobertura es buena no desaparece: se queda en una línea. Que el
            usuario sepa que esto se mide —y que hoy sale bien— es la mitad de lo que
            hace creíble el aviso el día que salga mal. */}
        {cobertura && !cobertura.prominent && <CoberturaLinea cobertura={cobertura} />}

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
 * Por encima de esta parte del gasto señalada como evitable, el héroe deja de
 * enseñar la pareja «X → Y» y la barra de reparto.
 *
 * No es un umbral cosmético. Con el 93 % evitable, la barra queda con un lado
 * invisible y la segunda cifra grande dice, en letra de 54 px, que puedes dejar de
 * pagar casi todo tu agente. Nadie se lo cree, y con razón: lo que suele haber detrás
 * es un agente pequeño donde dos o tres pasos son la factura entera. El aviso de
 * `savings_needs_caution` ya salta antes (al 60 %), pero va debajo de la cifra, y aquí
 * pasa lo mismo que con el «$0» de D-073: el número se lee antes que el aviso.
 */
const CASI_TODO_EVITABLE = 0.9;

/**
 * Lo que va donde iría la barra de reparto cuando el reparto no reparte nada.
 *
 * Dice la cifra —no la esconde, está medida— y dice por qué no se la presentamos como
 * una promesa. Es la misma regla que el resto del producto: una cifra se enseña con lo
 * que haga falta para leerla bien.
 */
function CasiTodoEvitable({
  overview,
  total,
  evitable,
}: {
  overview: Overview;
  total: number;
  evitable: number;
}) {
  return (
    <div className="casitodo">
      <p>
        <strong>Casi todo lo que gastas está señalado aquí abajo.</strong> De los{" "}
        {money(total, overview.currency)}{" "}
        {overview.projected ? "que costará el mes" : "de esta ventana"}, las reglas
        consideran evitables {money(evitable, overview.currency)}: el{" "}
        {Math.round((evitable / total) * 100)} %.
      </p>
      <p>
        No te lo enseñamos como «puedes dejar de pagar tu agente», porque eso casi nunca
        es lo que significa. Un reparto en el que un lado es cero no es un reparto: lo
        que suele haber detrás es un agente pequeño o recién estrenado donde dos o tres
        pasos son prácticamente toda la factura, y arreglarlos cambia esos pasos, no el
        agente entero. Mira el desglose de cada problema antes de contar con esta cifra.
      </p>
    </div>
  );
}

/**
 * El bloque de cobertura, cuando hay algo que avisar.
 *
 * Va antes del dinero y no en Avanzado. La regla del producto es que una cifra se
 * enseña con lo que haga falta para leerla bien, y aquí lo que hace falta es saber
 * sobre cuánto del agente está calculada.
 */
function CoberturaBloque({ cobertura }: { cobertura: Coverage }) {
  return (
    <section className={`cobertura ${cobertura.level}`}>
      <h2>{cobertura.headline}</h2>
      <p>{cobertura.detail}</p>
      <Señales cobertura={cobertura} />
    </section>
  );
}

/** Una línea cuando todo va bien. Se puede desplegar para ver las cuatro señales. */
function CoberturaLinea({ cobertura }: { cobertura: Coverage }) {
  if (cobertura.level === "sin-base" && cobertura.llm_calls === 0) return null;
  return (
    <details className="cobertura-linea">
      <summary>
        <i className={`punto ${cobertura.level}`} aria-hidden /> {cobertura.headline}
      </summary>
      <p>{cobertura.detail}</p>
      <Señales cobertura={cobertura} />
    </details>
  );
}

/**
 * Las cuatro señales, cada una con su barra.
 *
 * Sin porcentaje cuando no hay llamadas suficientes: se dice por qué, igual que en
 * todas las demás proporciones del producto (D-087).
 */
function Señales({ cobertura }: { cobertura: Coverage }) {
  return (
    <ul className="senales">
      {cobertura.signals.map((s) => (
        <li key={s.key} className={s.level}>
          <div className="stop">
            <span>{s.label}</span>
            <b>
              {s.level === "no-aplica"
                ? "no lo usas"
                : s.value === null
                  ? "—"
                  : /* espacio duro: «100 %» no puede partirse en dos líneas */
                    `${(s.value * 100).toFixed(0)} %`}
            </b>
          </div>
          <div className="sbar" role="img" aria-label={`${(s.value ?? 0) * 100} por ciento`}>
            <i style={{ width: `${(s.value ?? 0) * 100}%` }} />
          </div>
          <small>
            {s.value === null
              ? s.unavailable
              : `${number(s.counted)} de ${number(s.total)} llamadas`}
          </small>
          {/* El «qué hacer» sólo cuando hace falta: si la señal va bien, es ruido. */}
          {(s.level === "malo" || s.level === "flojo") && <small className="fix">{s.fix}</small>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Avisos que acompañan a la cifra grande.
 *
 * Una cifra de ahorro que nadie se cree no vende nada: cuando el evitable es casi todo
 * el gasto, o cuando la proyección sale de unas horas de datos, se dice aquí mismo en
 * lugar de presentarlo como una promesa.
 */
function Caveats({
  overview,
  ventana,
  casiTodo,
}: {
  overview: Overview;
  ventana: string;
  casiTodo: boolean;
}) {
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
  // Cuando el evitable es casi todo, esto ya lo dice el bloque de arriba con más
  // detalle. Decirlo dos veces en la misma pantalla no lo hace más creíble.
  if (overview.savings_needs_caution && !casiTodo) {
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


/**
 * Lo que va donde iría el dinero cuando no se puede calcular.
 *
 * No es un caso raro: le pasa a cualquiera que use modelos locales —un estudiante
 * probando con Ollama— y a todo el mundo el día que sale un modelo nuevo y todavía no
 * está en la tabla de precios. Lo que se enseña es el motivo, y el detalle de abajo
 * sigue con tokens, trazas y latencia, que son datos medidos.
 */
function SinDinero({ motivo }: { motivo: string }) {
  return (
    <div className="nomoney">
      <b>No podemos calcular el dinero</b>
      <span>{motivo}</span>
    </div>
  );
}
