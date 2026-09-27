"use client";

import Link from "next/link";
import { useState } from "react";
import { createDataset, deleteDataset } from "@/lib/api";
import { usePermisos } from "@/lib/permisos";
import { money, number, timestamp } from "@/lib/format";
import type { Dataset, Rate, RunSummary } from "@/lib/types";
import { tr } from "@/lib/i18n";
import { porcentaje } from "@/lib/format";
import { t } from "@/lib/textos";

export function Conjuntos({
  project,
  datasets,
  query,
  onChange,
}: {
  project: string;
  datasets: Dataset[];
  query: string;
  onChange: () => void;
}) {
  const [nombre, setNombre] = useState("");
  const [limite, setLimite] = useState(50);
  const [error, setError] = useState("");
  const [creando, setCreando] = useState(false);

  async function crear() {
    if (!nombre.trim()) return;
    setCreando(true);
    setError("");
    try {
      await createDataset({
        project_id: project,
        name: nombre.trim(),
        filter: { sort: "recent" },
        limit: limite,
      });
      setNombre("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.crear"));
    } finally {
      setCreando(false);
    }
  }

  const { escribir } = usePermisos(project);
  return (
    <section className="sec">
      <fieldset className="sin-marco" disabled={!escribir}>
      <h2>{t("ev.conj.titulo")}</h2>
      <p className="lead">
        {tr("ev.conj.lead", { reales: <strong>{t("ev.conj.reales")}</strong> })}
      </p>

      <div className="ab">
        <label className="grow">
          <small>{t("ev.conj.nombre")}</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="regresiones-checkout"
          />
        </label>
        <label>
          <small>{t("ev.conj.cuantas")}</small>
          <input
            className="field"
            type="number"
            min={1}
            max={500}
            value={limite}
            onChange={(e) => setLimite(Number(e.target.value) || 50)}
            style={{ width: 110 }}
          />
        </label>
        <button type="button" className="btn" onClick={crear} disabled={creando || !nombre.trim()}>
          {creando ? t("conj.creando") : t("ev.conj.crear")}
        </button>
      </div>
      <p className="disclaimer">
        {tr("ev.conj.otro_filtro", {
          explorador: <Link href={`/trazas${query}`}>{t("ev.explorador")}</Link>,
          guardar: t("conj.guardar"),
        })}
      </p>
      {error && <p className="verr">{error}</p>}

      {datasets.length === 0 ? (
        <p className="disclaimer">{t("ev.conj.ninguno")}</p>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("ev.conj.col.conjunto")}</th>
                <th className="r">{t("ev.conj.col.casos")}</th>
                <th className="pro">{t("ev.conj.col.filtro")}</th>
                <th className="r hide-sm">{t("ev.conj.col.creado")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.id}>
                  <td>
                    {d.name}
                    <div className="meta pro">{d.id}</div>
                  </td>
                  <td className="r">{number(d.item_count)}</td>
                  <td className="pro" style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                    {Object.entries(d.source_filter).length === 0
                      ? t("ev.conj.sin_filtro")
                      : Object.entries(d.source_filter)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" · ")}
                  </td>
                  <td className="r hide-sm">{timestamp(d.created_at)}</td>
                  <td className="r">
                    <button
                      type="button"
                      className="vbtn ghost"
                      onClick={async () => {
                        await deleteDataset(d.id, project);
                        onChange();
                      }}
                    >
                      {t("ev.borrar")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </fieldset>
    </section>
  );
}

// ---------------------------------------------------------------------------------
// Tiradas
// ---------------------------------------------------------------------------------

export function Tiradas({
  runs,
  query,
  project,
}: {
  runs: RunSummary[];
  query: string;
  project: string;
}) {
  if (runs.length === 0) return null;
  return (
    <section className="sec">
      <h2>{t("ev.tiradas")}</h2>
      <p className="lead">{t("ev.tiradas.lead")}</p>
      <div className="tbl-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>{t("ev.col.version")}</th>
              <th>{t("ev.col.conjunto")}</th>
              <th className="r">{t("ev.col.casos")}</th>
              <th className="r">{t("ev.col.personas")}</th>
              <th className="r">{t("ev.col.juez")}</th>
              <th className="r">{t("ev.col.coste")}</th>
              <th className="r pro">{t("ev.col.juzgar")}</th>
              <th className="r hide-sm">{t("ev.col.cuando")}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td>
                  {r.variant}
                  {/* La versión de prompt con la que corrió de verdad, por si `variant`
                      se quedó viejo, que es lo que pasa la mitad de las veces. */}
                  {r.prompt_versions.length > 0 && (
                    <div className="meta">{r.prompt_versions.join(" · ")}</div>
                  )}
                  <div className="meta pro">{r.run_id}</div>
                </td>
                <td>{r.dataset_name || r.dataset_id}</td>
                <td className="r">{r.cases}</td>
                <td className="r">
                  <Celda rate={r.rates.find((x) => x.source === "human")} />
                </td>
                <td className="r">
                  <Celda rate={r.rates.find((x) => x.source === "llm_judge")} />
                </td>
                <td className="r money">{money(r.cost_usd)}</td>
                <td className="r money pro">
                  {r.judge_cost_usd > 0 ? money(r.judge_cost_usd) : "—"}
                </td>
                <td className="r hide-sm">{timestamp(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="disclaimer">
        {tr("ev.sin_tiradas", {
          codigo: <code>laplace.run_dataset(&quot;…&quot;, mi_agente, variant=&quot;…&quot;)</code>,
          explorador: <Link href={`/trazas${query}`}>{t("ev.explorador")}</Link>,
          proyecto: project,
        })}
      </p>
    </section>
  );
}

/** Una celda de acierto: porcentaje si lo hay, casos en bruto si no. */
export function Celda({ rate }: { rate?: Rate }) {
  if (!rate || rate.judged === 0) return <span style={{ color: "var(--ink-3)" }}>—</span>;
  if (rate.value === null)
    return (
      <span title={rate.unavailable}>
        {rate.passed}/{rate.judged}
      </span>
    );
  return (
    <span title={t("ev.entre", { bajo: porcentaje(rate.low!), alto: porcentaje(rate.high!) })}>
      {porcentaje(rate.value)}
    </span>
  );
}
