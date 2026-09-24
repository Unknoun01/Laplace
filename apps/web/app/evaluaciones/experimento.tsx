"use client";

import Link from "next/link";
import { useState } from "react";
import { compareRuns, runJudge } from "@/lib/api";
import { duration, money, tokens } from "@/lib/format";
import type {
  Comparison,
  Dataset,
  JudgeStatus,
  Rate,
  RunSummary,
  SourceComparison,
  VariantSide,
} from "@/lib/types";

export function Experimento({
  project,
  runs,
  datasets,
  juez,
  query,
  onJudged,
}: {
  project: string;
  runs: RunSummary[];
  datasets: Dataset[];
  juez: JudgeStatus | null;
  query: string;
  onJudged: () => void;
}) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [comp, setComp] = useState<Comparison | null>(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  // Sólo tiene sentido comparar tiradas del mismo conjunto, así que al elegir A se
  // filtran las candidatas a B en vez de dejar que el backend rechace la pareja.
  const elegidaA = runs.find((r) => r.run_id === a);
  const candidatasB = runs.filter(
    (r) => r.run_id !== a && (!elegidaA || r.dataset_id === elegidaA.dataset_id),
  );

  async function comparar() {
    if (!a || !b) return;
    setCargando(true);
    setError("");
    try {
      setComp(await compareRuns(project, a, b));
    } catch (e) {
      setComp(null);
      setError(e instanceof Error ? e.message : "no se ha podido comparar");
    } finally {
      setCargando(false);
    }
  }

  if (runs.length < 2) {
    // Tres pasos que se marcan solos al cumplirse, en vez de tres párrafos que había que
    // leer para saber por dónde empezar (D-125).
    const conjunto = datasets[0]?.name ?? "mi-conjunto";
    const pasos = [
      {
        hecho: datasets.length > 0,
        titulo: "Guarda un conjunto de casos",
        cuerpo: (
          <>
            Ejecuciones reales de tu agente. Desde <Link href={`/trazas${query}`}>el
            explorador</Link>, filtra y pulsa «Guardar estas trazas como conjunto», o créalo
            aquí abajo con las más recientes.
          </>
        ),
      },
      {
        hecho: runs.length >= 1,
        titulo: "Lánzalo con la versión actual",
        cuerpo: (
          <>
            La tirada la lanza el SDK en tu proceso —Laplace no ejecuta tu agente—:
            <pre>{`import laplace
laplace.init(project="${project}")
laplace.run_dataset("${conjunto}", mi_agente, variant="actual")`}</pre>
          </>
        ),
      },
      {
        hecho: runs.length >= 2,
        titulo: "Y otra vez con la versión nueva",
        cuerpo: (
          <>
            Otro prompt, otro modelo: la misma línea con otro <code>variant</code>. Con las
            dos tiradas, aquí aparece la comparación de acierto y coste. El acierto sale de
            tus anotaciones o del juez automático.
          </>
        ),
      },
    ];
    const siguiente = pasos.findIndex((p) => !p.hecho);
    return (
      <section className="sec">
        <h2>Comparar dos versiones</h2>
        <p className="lead">Tres pasos, y cada uno se marca solo cuando está hecho.</p>
        <ol className="pasos">
          {pasos.map((p, i) => (
            <li
              key={p.titulo}
              className={p.hecho ? "hecho" : i === siguiente ? "siguiente" : "pendiente"}
            >
              <b>{p.titulo}</b>
              {i === siguiente && <div className="cuerpo">{p.cuerpo}</div>}
            </li>
          ))}
        </ol>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>Comparar dos versiones</h2>
      <p className="lead">
        Acierto y coste a la vez. Las dos tiradas tienen que ser del mismo conjunto:
        comparar el acierto sobre casos distintos no diría nada.
      </p>

      <div className="ab">
        <label>
          <small>A — la de referencia</small>
          <select
            className="field"
            value={a}
            onChange={(e) => {
              setA(e.target.value);
              setB("");
              setComp(null);
            }}
          >
            <option value="">Elige una tirada</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {r.dataset_name || r.dataset_id} · {r.cases} casos
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>B — la nueva</small>
          <select
            className="field"
            value={b}
            onChange={(e) => {
              setB(e.target.value);
              setComp(null);
            }}
            disabled={!a}
          >
            <option value="">{a ? "Elige una tirada" : "Elige A primero"}</option>
            {candidatasB.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {r.cases} casos
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn primary" onClick={comparar} disabled={!a || !b || cargando}>
          {cargando ? "Comparando…" : "Comparar"}
        </button>
      </div>

      {error && <p className="verr">{error}</p>}

      {comp && (
        <>
          <div className={`verdict ${principal(comp)}`}>
            <b>{comp.headline}</b>
            <p>{comp.detail}</p>
          </div>

          {comp.sources_disagree && (
            <div className="caveats">
              <p>
                <strong>Las personas y el juez no dicen lo mismo.</strong> No es un fallo
                del producto: es el dato más interesante que puede darte esta pantalla. O
                el juez está midiendo otra cosa, o quien anotó y quien escribió el prompt
                del juez no entienden igual «bien». Mira los casos antes de fiarte de
                ninguno de los dos.
              </p>
            </div>
          )}

          <div className="ab-grid">
            {comp.by_source.map((c) => (
              <BloqueFuente key={c.source} comparacion={c} />
            ))}
          </div>

          <h3 className="sub">Lo que costó cada una</h3>
          <p className="lead">
            Esto no lleva margen: no es una muestra, es la factura de lo que se ejecutó.
            Si gestionas tus prompts en Laplace, debajo de cada lado verás{" "}
            <Link href={`/prompts?project=${encodeURIComponent(project)}`}>
              con qué versión corrió
            </Link>
            , leída de las trazas y no de la etiqueta que le pusiste a la tirada.
          </p>
          <div className="ab-grid">
            <Coste lado={comp.a} etiqueta="A" />
            <Coste lado={comp.b} etiqueta="B" />
          </div>

          {juez?.enabled && (
            <div className="actions">
              <button
                type="button"
                className="btn"
                onClick={async () => {
                  await runJudge({ project_id: project, run_id: comp.a.run_id });
                  await runJudge({ project_id: project, run_id: comp.b.run_id });
                  onJudged();
                  await comparar();
                }}
              >
                Juzgar las dos tiradas con {juez.model}
              </button>
              <span className="chip where">
                Cuesta dinero: se te dirá cuánto en cuanto termine
              </span>
            </div>
          )}

          <div className="techline pro">
            <span>
              A <b>{comp.a.run_id}</b>
            </span>
            <span>
              B <b>{comp.b.run_id}</b>
            </span>
            <span>
              conjunto <b>{comp.dataset_id}</b>
            </span>
            <span>
              casos <b>{comp.cases}</b>
            </span>
          </div>
        </>
      )}
    </section>
  );
}

/** El veredicto que manda en el titular, para darle color al bloque. */
export function principal(comp: Comparison): string {
  const conBase = comp.by_source.filter((c) => c.verdict !== "sin-base");
  if (conBase.some((c) => c.verdict === "peor")) return "peor";
  if (conBase.some((c) => c.verdict === "mejor")) return "mejor";
  return conBase.length > 0 ? "empate" : "sin-base";
}

export function BloqueFuente({ comparacion }: { comparacion: SourceComparison }) {
  const etiqueta = comparacion.source === "human" ? "Personas" : "Juez (modelo)";
  return (
    <div className={`fuente ${comparacion.source} ${comparacion.verdict}`}>
      <header>
        <span className="ftag">{etiqueta}</span>
        <b>{comparacion.headline}</b>
      </header>
      <div className="frates">
        <Acierto rate={comparacion.a} etiqueta="A" />
        <Acierto rate={comparacion.b} etiqueta="B" />
      </div>
      <p>{comparacion.detail}</p>
    </div>
  );
}

/**
 * Un acierto con su guarda.
 *
 * Cuando no hay casos suficientes se enseñan los casos en bruto y el motivo, nunca un
 * porcentaje: es el mismo patrón del 468 $/mes y del 193.100 %, y ya mordió tres veces.
 */
export function Acierto({ rate, etiqueta }: { rate: Rate; etiqueta: string }) {
  return (
    <div className="acierto">
      <small>{etiqueta}</small>
      {rate.value === null ? (
        <>
          <b className="num none">
            {rate.passed}/{rate.judged || "—"}
          </b>
          <span className="why">{rate.unavailable}</span>
        </>
      ) : (
        <>
          <b className="num">{(rate.value * 100).toFixed(0)} %</b>
          <span className="why">
            {rate.passed} de {rate.judged}
            {rate.unjudged > 0 && ` · ${rate.unjudged} sin anotar`}
          </span>
          <span className="why pro">
            entre {(rate.low! * 100).toFixed(0)} % y {(rate.high! * 100).toFixed(0)} %
            (Wilson, 95 %)
          </span>
        </>
      )}
    </div>
  );
}

export function Coste({ lado, etiqueta }: { lado: VariantSide; etiqueta: string }) {
  const suelo = lado.cost_is_floor ? "≥ " : "";
  return (
    <div className="mcard">
      <small>
        {etiqueta} · {lado.variant}
      </small>
      <b className="num">
        {suelo}
        {lado.cost_per_case_usd === null ? "—" : money(lado.cost_per_case_usd)}
      </b>
      <span className="why">por caso</span>
      {/* Con qué prompt corrió este lado. Sale de las trazas y no de `variant`, que es
          lo que alguien tecleó y la mitad de las veces se queda sin actualizar (D-094). */}
      {lado.prompt_versions.length > 0 && (
        <div className="pvers">
          {lado.prompt_versions.map((v) => (
            <span key={v} className="chip where">
              {v}
            </span>
          ))}
        </div>
      )}
      <div className="techline pro" style={{ marginTop: 8 }}>
        <span>
          total <b>{money(lado.cost_usd)}</b>
        </span>
        <span>
          tokens <b>{tokens(lado.input_tokens + lado.output_tokens)}</b>
        </span>
        {lado.duration_ms_per_case !== null && (
          <span>
            duración <b>{duration(lado.duration_ms_per_case)}</b>
          </span>
        )}
        {lado.crashed > 0 && (
          <span>
            reventados <b>{lado.crashed}</b>
          </span>
        )}
      </div>
      {lado.judge_cost_usd > 0 && (
        <p className="jcost">
          Juzgar esta tirada costó <b>{money(lado.judge_cost_usd)}</b> aparte
          {lado.judge_cost_unknown && " (y hay veredictos cuyo coste no sabemos)"}. Es
          gasto nuestro, no de tu agente, y por eso va separado.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Conjuntos de casos
// ---------------------------------------------------------------------------------
