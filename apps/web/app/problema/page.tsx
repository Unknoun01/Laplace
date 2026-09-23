"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { StaticTree } from "@/components/StaticTree";
import { BackendDown, NeedsKey, NotFound, NotYours, TableSkeleton } from "@/components/states";
import {
  clearFindingState,
  createDataset,
  getFinding,
  getTrace,
  listProjects,
  parseDays,
  setFindingState,
} from "@/lib/api";
import {
  duration,
  money,
  number,
  oneLine,
  spanLabel,
  timestamp,
  tokens,
  windowLabel,
} from "@/lib/format";
import { Euros } from "@/lib/moneda";
import type { FindingDetail, Span } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { useTitulo } from "@/lib/titulo";
import { useApi } from "@/lib/useApi";

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

  const estado = useApi(async () => {
    const projects = await listProjects();
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0]?.id ?? "";
    const finding = await getFinding(findingId, project, days);
    const trace = finding?.sample_trace_id
      ? await getTrace(finding.sample_trace_id, project)
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
 * Lo que el usuario dice de este hallazgo, y lo que se ha medido después (D-123).
 *
 * Sustituye al «Ya lo he arreglado, vuelve a medir», que sólo recargaba. Marcarlo
 * guarda **cuándo**, y a partir de ahí se compara la ejecución media de antes con la
 * de después: si sigue saliendo igual, vuelve a la lista aunque esté marcado.
 */
function EstadoHallazgo({ project, finding }: { project: string; finding: FindingDetail }) {
  const [nota, setNota] = useState("");
  const [ignorando, setIgnorando] = useState(false);
  const [error, setError] = useState("");

  async function marcar(status: "arreglado" | "ignorado") {
    try {
      await setFindingState({ project_id: project, finding_id: finding.id, status, note: nota });
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido guardar");
    }
  }

  async function deshacer() {
    try {
      await clearFindingState(project, finding.id);
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido deshacer");
    }
  }

  if (finding.state) {
    const check = finding.fix_check;
    const titulo =
      finding.state === "ignorado"
        ? "Lo has marcado como ignorado"
        : finding.state === "reaparecido"
          ? "Lo marcaste como arreglado, y sigue saliendo"
          : "Lo has marcado como arreglado";
    return (
      <section className={`block estado-hallazgo ${finding.state}`}>
        <h2>{titulo}</h2>
        {finding.state_at && (
          <p className="muted">
            El {timestamp(finding.state_at)}.
            {finding.state_note && ` «${finding.state_note}»`}
          </p>
        )}
        {check && (
          <>
            <p>{check.headline}</p>
            {check.before_per_run !== null && check.after_per_run !== null && (
              <div className="antes-despues">
                <div>
                  <small>Antes, por ejecución</small>
                  <b className="num">{porEjecucion(check.before_per_run, check.unit)}</b>
                  <small>{number(check.runs_before)} ejecuciones</small>
                </div>
                <div aria-hidden className="arrow">
                  →
                </div>
                <div>
                  <small>Después</small>
                  <b className="num">{porEjecucion(check.after_per_run, check.unit)}</b>
                  <small>{number(check.runs_after)} ejecuciones</small>
                </div>
              </div>
            )}
          </>
        )}
        {finding.state === "ignorado" && (
          <p>No cuenta en el evitable del inicio ni manda alertas.</p>
        )}
        <div className="actions" style={{ paddingTop: 12 }}>
          <button type="button" className="btn" onClick={deshacer}>
            Deshacer y devolverlo a la lista
          </button>
        </div>
        {error && <p className="verr">{error}</p>}
      </section>
    );
  }

  return (
    <section className="block estado-hallazgo">
      <h2>¿Ya lo has arreglado?</h2>
      <p>
        Márcalo y lo comprobamos: comparamos lo que costaba cada ejecución antes con lo que
        cuesta después. Si sigue saliendo igual, vuelve a la lista.
      </p>
      {ignorando && (
        <input
          className="field"
          value={nota}
          onChange={(e) => setNota(e.target.value)}
          placeholder="Por qué no es un problema (opcional)"
          style={{ width: "100%", marginBottom: 10 }}
        />
      )}
      <div className="actions" style={{ paddingTop: 0 }}>
        {ignorando ? (
          <>
            <button type="button" className="btn" onClick={() => marcar("ignorado")}>
              Ignorar este problema
            </button>
            <button type="button" className="btn" onClick={() => setIgnorando(false)}>
              Cancelar
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn primary" onClick={() => marcar("arreglado")}>
              Lo he arreglado: compruébalo
            </button>
            <button type="button" className="btn" onClick={() => setIgnorando(true)}>
              No es un problema para mí
            </button>
          </>
        )}
      </div>
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

function porEjecucion(valor: number, unidad: "usd" | "tokens" | "ms"): string {
  if (unidad === "usd") return money(valor);
  if (unidad === "tokens") return `${tokens(valor)} tokens`;
  return duration(valor);
}

/**
 * Cambiar a un modelo más barato ahorra seguro; que acierte igual, no (D-123).
 *
 * La regla del modelo caro dice cuánto se ahorra y no promete la calidad, porque eso
 * exige evaluar. Este bloque lleva de una cosa a la otra: guarda las ejecuciones reales
 * de este paso como conjunto de casos y da la línea para lanzar la tirada con el modelo
 * nuevo.
 */
function ProbarAntes({
  project,
  finding,
  query,
}: {
  project: string;
  finding: FindingDetail;
  query: string;
}) {
  const alternativa =
    finding.tech
      .find((t) => t.label === "modelo")
      ?.value.split("→")
      .pop()
      ?.trim() ?? "";
  const nombre = `ab-${finding.step_key.slice(0, 8)}`;
  const [creado, setCreado] = useState(false);
  const [error, setError] = useState("");

  async function crear() {
    try {
      await createDataset({
        project_id: project,
        name: nombre,
        filter: { step_key: finding.step_key, sort: "recent" },
        limit: 50,
      });
      setCreado(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido crear");
    }
  }

  return (
    <section className="block">
      <h2>Antes de cambiarlo, compruébalo</h2>
      <p>
        El ahorro está medido; que {alternativa || "el modelo barato"} responda igual de
        bien, no. Guarda las ejecuciones reales de este paso como conjunto de casos y
        lánzalas con el modelo nuevo: Evaluaciones te dirá si acierta igual y cuánto cuesta
        menos.
      </p>
      {creado ? (
        <>
          <pre>{`import laplace
laplace.init(project="${project}")
laplace.run_dataset("${nombre}", mi_agente, variant="${alternativa || "modelo-barato"}")`}</pre>
          <p>
            <Link href={`/evaluaciones${query}`}>Ir a Evaluaciones →</Link>
          </p>
        </>
      ) : (
        <div className="actions" style={{ paddingTop: 0 }}>
          <button type="button" className="btn" onClick={crear}>
            Crear el conjunto «{nombre}» con estas ejecuciones
          </button>
        </div>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
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
 * La cabecera con las cifras.
 *
 * El periodo es el **observado**, no el que pide el selector: con dos horas de datos
 * en un rango de siete días, decir «ya gastados en 7 días» es falso, y era lo que
 * decía antes (D-073).
 */
function Head({ finding }: { finding: FindingDetail }) {
  const ventana = spanLabel(finding.observed_days);
  return (
    <div className="d-head">
      <h1>{finding.title}</h1>
      <div className="d-sub pro">
        {finding.tech.map((t) => `${t.label} ${t.value}`).join(" · ")}
      </div>
      <div className="d-cost">
        {finding.costs_money ? (
          <>
            {finding.monthly_saving_usd !== null && (
              <div>
                <b className="num">{money(finding.monthly_saving_usd, finding.currency)}</b>
                <span>al mes si no cambia nada</span>
              </div>
            )}
            <div>
              <b className="num">
                {finding.cost_is_floor ? "≥ " : ""}
                {money(finding.window_waste_usd, finding.currency)}
              </b>
              <span>
                ya gastados en {ventana} <Euros usd={finding.window_waste_usd} />
              </span>
            </div>
          </>
        ) : (
          <div>
            <b className="num neutral">{duration(finding.window_waste_ms)}</b>
            <span>de espera evitable en {ventana}</span>
          </div>
        )}
        {finding.scope_label && (
          <div>
            <b className="num neutral">{finding.scope_label}</b>
            <span>afectadas</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** Las ocurrencias repetidas, con las idénticas resaltadas. */
function Repetitions({ spans, bucle }: { spans: Span[]; bucle: boolean }) {
  const shown = spans.slice(0, 3);
  const rest = spans.length - shown.length;

  return (
    <div className="reps">
      {shown.map((span, index) => (
        <div key={span.span_id} className="rep same">
          <span className="n">{index + 1}</span>
          <span className="what">
            {span.name} <em>{oneLine(span.tool?.arguments ?? span.input ?? span.llm?.input_messages)}</em>
          </span>
          <span className="c">{money(span.llm?.cost.total_usd ?? 0)}</span>
        </div>
      ))}
      {rest > 0 && (
        <div className="reps-note">
          {/* En un bucle las entradas NO son idénticas —ésa es la diferencia con la
              repetición—, así que llamarlas así contradecía la frase de arriba. */}
          …{rest}{" "}
          {bucle
            ? rest === 1
              ? "vuelta más"
              : "vueltas más"
            : rest === 1
              ? "llamada idéntica más"
              : "llamadas idénticas más"}
          , todas con la misma respuesta.
        </div>
      )}
    </div>
  );
}

function SpanAttributes({ span }: { span: Span }) {
  const rows: [string, string][] = [
    ["trace_id", span.trace_id],
    ["span_id", span.span_id],
    ["laplace.span.type", span.type],
    ["laplace.dedup_hash", span.dedup_hash],
    ["laplace.step.key", span.step_key],
    ["laplace.step.label", span.step_label],
    ["duration_ms", span.duration_ms.toFixed(0)],
  ];
  if (span.tool?.name) rows.push(["gen_ai.tool.name", span.tool.name]);
  if (span.llm?.request_model) rows.push(["gen_ai.request.model", span.llm.request_model]);
  if (span.llm) {
    rows.push(["gen_ai.usage.input_tokens", String(span.llm.usage.input_tokens)]);
    rows.push(["gen_ai.usage.output_tokens", String(span.llm.usage.output_tokens)]);
    // La caché es la mitad del cálculo de coste: sin verla, la cifra no se audita.
    rows.push([
      "laplace.usage.cached_input_tokens",
      String(span.llm.usage.cached_input_tokens),
    ]);
    rows.push(["laplace.usage.cache_write_tokens", String(span.llm.usage.cache_write_tokens)]);
    if (span.llm.cost.rate) rows.push(["tarifa aplicada", span.llm.cost.rate]);
    if (span.llm.cost.rate_assumed) rows.push(["tarifa asumida", span.llm.cost.rate_note]);
  }
  if (span.session_id) rows.push(["laplace.session.id", span.session_id]);
  if (span.user_id) rows.push(["laplace.user.id", span.user_id]);

  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Negritas de markdown y `código`, que es todo lo que usan los textos de las reglas. */
function Markup({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <p>
      {parts.map((part, index) => {
        if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
        if (part.startsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
        return <span key={index}>{part}</span>;
      })}
    </p>
  );
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
