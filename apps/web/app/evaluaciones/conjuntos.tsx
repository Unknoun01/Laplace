"use client";

import Link from "next/link";
import { useState } from "react";
import { createDataset, deleteDataset } from "@/lib/api";
import { usePermisos } from "@/lib/permisos";
import { money, number, timestamp } from "@/lib/format";
import type { Dataset, Rate, RunSummary } from "@/lib/types";

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
      setError(e instanceof Error ? e.message : "no se ha podido crear");
    } finally {
      setCreando(false);
    }
  }

  const { escribir } = usePermisos(project);
  return (
    <section className="sec">
      <fieldset className="sin-marco" disabled={!escribir}>
      <h2>Conjuntos de casos</h2>
      <p className="lead">
        Colecciones de ejecuciones <strong>reales</strong> de tu agente. No hay casos
        inventados aquí: cada uno sale de una traza que ocurrió, y se puede abrir para ver
        de dónde salió.
      </p>

      <div className="ab">
        <label className="grow">
          <small>Nombre</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="regresiones-checkout"
          />
        </label>
        <label>
          <small>Cuántas trazas</small>
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
          {creando ? "Creando…" : "Crear desde las más recientes"}
        </button>
      </div>
      <p className="disclaimer">
        ¿Quieres otro filtro? Fíltralo en{" "}
        <Link href={`/trazas${query}`}>el explorador</Link> y pulsa «Guardar estas trazas
        como conjunto de casos»: el conjunto guarda el filtro con el que se formó, para
        que después se pueda discutir de dónde salió.
      </p>
      {error && <p className="verr">{error}</p>}

      {datasets.length === 0 ? (
        <p className="disclaimer">Todavía no hay ninguno.</p>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Conjunto</th>
                <th className="r">Casos</th>
                <th className="pro">Filtro de origen</th>
                <th className="r hide-sm">Creado</th>
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
                      ? "sin filtro (las más recientes)"
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
                      Borrar
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
      <h2>Tiradas</h2>
      <p className="lead">
        Cada una es una pasada de un conjunto por una versión de tu agente. Las lanza el
        SDK en tu proceso; aquí sólo llega el parte y las trazas.
      </p>
      <div className="tbl-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>Versión</th>
              <th>Conjunto</th>
              <th className="r">Casos</th>
              <th className="r">Personas</th>
              <th className="r">Juez</th>
              <th className="r">Coste</th>
              <th className="r pro">Juzgar costó</th>
              <th className="r hide-sm">Cuándo</th>
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
        ¿Sin tiradas de una versión nueva? Lánzala con{" "}
        <code>laplace.run_dataset(&quot;…&quot;, mi_agente, variant=&quot;…&quot;)</code> apuntando
        a este mismo Laplace. Las trazas que genere se ven en{" "}
        <Link href={`/trazas${query}`}>el explorador</Link> como cualquier otra, y el
        proyecto es «{project}».
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
    <span title={`entre ${(rate.low! * 100).toFixed(0)} % y ${(rate.high! * 100).toFixed(0)} %`}>
      {(rate.value * 100).toFixed(0)} %
    </span>
  );
}
