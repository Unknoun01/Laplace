"use client";

import Link from "next/link";
import { useState } from "react";
import { compareRuns, runJudge } from "@/lib/api";
import { duration, money, tokens } from "@/lib/format";
import { tr } from "@/lib/i18n";
import { porcentaje } from "@/lib/format";
import { t } from "@/lib/textos";
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
      setError(e instanceof Error ? e.message : t("ev.error.comparar"));
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
        titulo: t("ev.paso1"),
        cuerpo: (
          <>
            {tr("ev.paso1.texto", {
              explorador: <Link href={`/trazas${query}`}>{t("ev.explorador")}</Link>,
              guardar: t("conj.guardar"),
            })}
          </>
        ),
      },
      {
        hecho: runs.length >= 1,
        titulo: t("ev.paso2"),
        cuerpo: (
          <>
            {t("ev.paso2.texto")}
            <pre>{`import laplace
laplace.init(project="${project}")
laplace.run_dataset("${conjunto}", mi_agente, variant="actual")`}</pre>
          </>
        ),
      },
      {
        hecho: runs.length >= 2,
        titulo: t("ev.paso3"),
        cuerpo: <>{tr("ev.paso3.texto", { variant: <code>variant</code> })}</>,
      },
    ];
    const siguiente = pasos.findIndex((p) => !p.hecho);
    return (
      <section className="sec">
        <h2>{t("ev.comparar.titulo")}</h2>
        <p className="lead">{t("ev.comparar.pasos")}</p>
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
      <h2>{t("ev.comparar.titulo")}</h2>
      <p className="lead">{t("ev.comparar.lead")}</p>

      <div className="ab">
        <label>
          <small>{t("ev.a")}</small>
          <select
            className="field"
            value={a}
            onChange={(e) => {
              setA(e.target.value);
              setB("");
              setComp(null);
            }}
          >
            <option value="">{t("ev.elige")}</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {r.dataset_name || r.dataset_id} · {t("ev.n_casos", { n: r.cases })}
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>{t("ev.b")}</small>
          <select
            className="field"
            value={b}
            onChange={(e) => {
              setB(e.target.value);
              setComp(null);
            }}
            disabled={!a}
          >
            <option value="">{a ? t("ev.elige") : t("ev.elige_a")}</option>
            {candidatasB.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {t("ev.n_casos", { n: r.cases })}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn primary" onClick={comparar} disabled={!a || !b || cargando}>
          {cargando ? t("ev.comparando") : t("ev.comparar")}
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
                <strong>{t("ev.discrepan")}</strong> {t("ev.discrepan.texto")}
              </p>
            </div>
          )}

          <div className="ab-grid">
            {comp.by_source.map((c) => (
              <BloqueFuente key={c.source} comparacion={c} />
            ))}
          </div>

          <h3 className="sub">{t("ev.costo")}</h3>
          <p className="lead">
            {tr("ev.costo.lead", {
              enlace: (
                <Link href={`/prompts?project=${encodeURIComponent(project)}`}>
                  {t("ev.costo.enlace")}
                </Link>
              ),
            })}
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
                {t("ev.juzgar_dos", { modelo: juez.model })}
              </button>
              <span className="chip where">
                {t("ev.cuesta")}
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
              {t("ev.tec.conjunto")} <b>{comp.dataset_id}</b>
            </span>
            <span>
              {t("ev.tec.casos")} <b>{comp.cases}</b>
            </span>
          </div>
        </>
      )}
    </section>
  );
}

/** El veredicto que manda en el titular, para darle color al bloque. */
function principal(comp: Comparison): string {
  const conBase = comp.by_source.filter((c) => c.verdict !== "sin-base");
  if (conBase.some((c) => c.verdict === "peor")) return "peor";
  if (conBase.some((c) => c.verdict === "mejor")) return "mejor";
  return conBase.length > 0 ? "empate" : "sin-base";
}

function BloqueFuente({ comparacion }: { comparacion: SourceComparison }) {
  const etiqueta =
    comparacion.source === "human" ? t("ev.fuente.personas") : t("ev.fuente.juez");
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
function Acierto({ rate, etiqueta }: { rate: Rate; etiqueta: string }) {
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
          <b className="num">{porcentaje(rate.value)}</b>
          <span className="why">
            {t("ev.de", { a: rate.passed, b: rate.judged })}
            {rate.unjudged > 0 && t("ev.sin_anotar", { n: rate.unjudged })}
          </span>
          <span className="why pro">
            {t("ev.wilson", { bajo: porcentaje(rate.low!), alto: porcentaje(rate.high!) })}
          </span>
        </>
      )}
    </div>
  );
}

function Coste({ lado, etiqueta }: { lado: VariantSide; etiqueta: string }) {
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
      <span className="why">{t("ev.por_caso")}</span>
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
          {t("ev.tec.total")} <b>{money(lado.cost_usd)}</b>
        </span>
        <span>
          {t("ev.tec.tokens")} <b>{tokens(lado.input_tokens + lado.output_tokens)}</b>
        </span>
        {lado.duration_ms_per_case !== null && (
          <span>
            {t("ev.tec.duracion")} <b>{duration(lado.duration_ms_per_case)}</b>
          </span>
        )}
        {lado.crashed > 0 && (
          <span>
            {t("ev.tec.reventados")} <b>{lado.crashed}</b>
          </span>
        )}
      </div>
      {lado.judge_cost_usd > 0 && (
        <p className="jcost">
          {tr("ev.juez_costo", {
            coste: <b>{money(lado.judge_cost_usd)}</b>,
            desconocido: lado.judge_cost_unknown ? t("ev.juez_desconocido") : "",
          })}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Conjuntos de casos
// ---------------------------------------------------------------------------------
