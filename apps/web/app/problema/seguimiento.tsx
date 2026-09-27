"use client";

import Link from "next/link";
import { useState } from "react";
import { clearFindingState, createDataset, setFindingState } from "@/lib/api";
import { duration, money, number, timestamp, tokens } from "@/lib/format";
import type { FindingDetail } from "@/lib/types";
import { t } from "@/lib/textos";

/**
 * Lo que el usuario dice de este hallazgo, y lo que se ha medido después (D-123).
 *
 * Sustituye al «Ya lo he arreglado, vuelve a medir», que sólo recargaba. Marcarlo
 * guarda **cuándo**, y a partir de ahí se compara la ejecución media de antes con la
 * de después: si sigue saliendo igual, vuelve a la lista aunque esté marcado.
 */
export function EstadoHallazgo({ project, finding }: { project: string; finding: FindingDetail }) {
  const [nota, setNota] = useState("");
  const [ignorando, setIgnorando] = useState(false);
  const [error, setError] = useState("");

  async function marcar(status: "arreglado" | "ignorado") {
    try {
      await setFindingState({ project_id: project, finding_id: finding.id, status, note: nota });
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.guardar"));
    }
  }

  async function deshacer() {
    try {
      await clearFindingState(project, finding.id);
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.deshacer"));
    }
  }

  if (finding.state) {
    const check = finding.fix_check;
    const titulo =
      finding.state === "ignorado"
        ? t("seg.titulo.ignorado")
        : finding.state === "reaparecido"
          ? t("seg.titulo.reaparecido")
          : t("seg.titulo.arreglado");
    return (
      <section className={`block estado-hallazgo ${finding.state}`}>
        <h2>{titulo}</h2>
        {finding.state_at && (
          <p className="muted">
            {t("seg.fecha", { fecha: timestamp(finding.state_at) })}
            {finding.state_note && ` «${finding.state_note}»`}
          </p>
        )}
        {check && (
          <>
            <p>{check.headline}</p>
            {check.before_per_run !== null && check.after_per_run !== null && (
              <div className="antes-despues">
                <div>
                  <small>{t("seg.antes")}</small>
                  <b className="num">{porEjecucion(check.before_per_run, check.unit)}</b>
                  <small>{t("seg.ejecuciones", { n: number(check.runs_before) })}</small>
                </div>
                <div aria-hidden className="arrow">
                  →
                </div>
                <div>
                  <small>{t("seg.despues")}</small>
                  <b className="num">{porEjecucion(check.after_per_run, check.unit)}</b>
                  <small>{t("seg.ejecuciones", { n: number(check.runs_after) })}</small>
                </div>
              </div>
            )}
          </>
        )}
        {finding.state === "ignorado" && (
          <p>{t("seg.ignorado.nota")}</p>
        )}
        <div className="actions" style={{ paddingTop: 12 }}>
          <button type="button" className="btn" onClick={deshacer}>
            {t("seg.deshacer")}
          </button>
        </div>
        {error && <p className="verr">{error}</p>}
      </section>
    );
  }

  return (
    <section className="block estado-hallazgo">
      <h2>{t("seg.ya_arreglado")}</h2>
      <p>{t("seg.ya_arreglado.texto")}</p>
      {ignorando && (
        <input
          className="field"
          value={nota}
          onChange={(e) => setNota(e.target.value)}
          placeholder={t("seg.nota.placeholder")}
          style={{ width: "100%", marginBottom: 10 }}
        />
      )}
      <div className="actions" style={{ paddingTop: 0 }}>
        {ignorando ? (
          <>
            <button type="button" className="btn" onClick={() => marcar("ignorado")}>
              {t("seg.ignorar")}
            </button>
            <button type="button" className="btn" onClick={() => setIgnorando(false)}>
              {t("comun.cancelar")}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn primary" onClick={() => marcar("arreglado")}>
              {t("seg.arreglado.boton")}
            </button>
            <button type="button" className="btn" onClick={() => setIgnorando(true)}>
              {t("seg.no_problema")}
            </button>
          </>
        )}
      </div>
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

/**
 * Cambiar a un modelo más barato ahorra seguro; que acierte igual, no (D-123).
 *
 * La regla del modelo caro dice cuánto se ahorra y no promete la calidad, porque eso
 * exige evaluar. Este bloque lleva de una cosa a la otra: guarda las ejecuciones reales
 * de este paso como conjunto de casos y da la línea para lanzar la tirada con el modelo
 * nuevo.
 */
export function ProbarAntes({
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
      // El valor con «→» es el cambio de modelo; la etiqueta llega traducida (D-148).
      .find((item) => item.value.includes("→"))
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
      setError(e instanceof Error ? e.message : t("seg.error.crear"));
    }
  }

  return (
    <section className="block">
      <h2>{t("seg.probar.titulo")}</h2>
      <p>{t("seg.probar.texto", { modelo: alternativa || t("seg.probar.barato") })}</p>
      {creado ? (
        <>
          <pre>{`import laplace
laplace.init(project="${project}")
laplace.run_dataset("${nombre}", mi_agente, variant="${alternativa || "modelo-barato"}")`}</pre>
          <p>
            <Link href={`/evaluaciones${query}`}>{t("seg.probar.ir")}</Link>
          </p>
        </>
      ) : (
        <div className="actions" style={{ paddingTop: 0 }}>
          <button type="button" className="btn" onClick={crear}>
            {t("seg.probar.crear", { nombre })}
          </button>
        </div>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

export function porEjecucion(valor: number, unidad: "usd" | "tokens" | "ms"): string {
  if (unidad === "usd") return money(valor);
  if (unidad === "tokens") return t("seg.tokens", { n: tokens(valor) });
  return duration(valor);
}
