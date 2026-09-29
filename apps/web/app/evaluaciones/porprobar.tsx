"use client";

import Link from "next/link";
import { useState } from "react";
import { createDataset } from "@/lib/api";
import { money } from "@/lib/format";
import type { Dataset, Finding, RunSummary } from "@/lib/types";
import { t, tn } from "@/lib/textos";
import { lineaDeReplay, pruebasDelPaso } from "../problema/ciclo";

/** Cuántos problemas se listan: los que más devuelven. El resto sigue en el Diagnóstico. */
const MAX = 6;

/**
 * Arreglos por probar (D-156). Probar es el segundo paso del ciclo, y hasta aquí la
 * pantalla empezaba por las tiradas, como si el usuario ya supiera qué probar. Ahora
 * empieza por lo que el Diagnóstico ha encontrado: cada problema abierto de un paso,
 * con lo que devuelve y si ya tiene su prueba —un conjunto con las llamadas reales de
 * ese paso, y sus tiradas—. Lo que no es de un solo paso no se lista: no hay llamadas
 * que guardar para él.
 */
export function PorProbar({
  project,
  findings,
  datasets,
  runs,
  escribir,
  query,
  onChange,
}: {
  project: string;
  findings: Finding[];
  datasets: Dataset[];
  runs: RunSummary[];
  escribir: boolean;
  query: string;
  onChange: () => void;
}) {
  const abiertos = findings.filter((f) => f.step_key && f.costs_money).slice(0, MAX);
  if (abiertos.length === 0) return null;
  return (
    <section className="sec">
      <h2>{t("ev.por_probar.titulo")}</h2>
      <p className="lead">{t("ev.por_probar.lead")}</p>
      <ul className="por-probar">
        {abiertos.map((f) => (
          <Fila
            key={f.id}
            project={project}
            finding={f}
            pruebas={pruebasDelPaso(f.step_key, datasets, runs)}
            escribir={escribir}
            query={query}
            onChange={onChange}
          />
        ))}
      </ul>
    </section>
  );
}

function Fila({
  project,
  finding,
  pruebas,
  escribir,
  query,
  onChange,
}: {
  project: string;
  finding: Finding;
  pruebas: ReturnType<typeof pruebasDelPaso>;
  escribir: boolean;
  query: string;
  onChange: () => void;
}) {
  const [guardado, setGuardado] = useState("");
  const [error, setError] = useState("");
  const nombre = `ab-${finding.step_key.slice(0, 8)}`;
  const importe = finding.monthly_saving_usd ?? finding.window_waste_usd;

  async function guardar() {
    try {
      await createDataset({
        project_id: project,
        name: nombre,
        filter: { step_key: finding.step_key, sort: "recent" },
        limit: 50,
      });
      setGuardado(nombre);
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("ev.por_probar.error"));
    }
  }

  const estado = pruebas.conjunto
    ? pruebas.tiradas.length > 0
      ? tn("ev.por_probar.probado", pruebas.tiradas.length)
      : t("ev.por_probar.guardado")
    : t("ev.por_probar.sin_probar");

  return (
    <li>
      <div className="pp-titulo">
        <Link href={`/problema${query}&id=${encodeURIComponent(finding.id)}`}>{finding.title}</Link>
      </div>
      <div className="pp-dinero">
        {finding.cost_is_floor ? "≥ " : ""}
        {money(importe, finding.currency)}
      </div>
      <div className="pp-estado">
        <span className={`chip ${pruebas.tiradas.length > 0 ? "easy" : "where"}`}>{estado}</span>
        {pruebas.conjunto && <code>{pruebas.conjunto.name}</code>}
        {escribir && !pruebas.conjunto && !guardado && (
          <button type="button" className="btn small" onClick={guardar}>
            {t("ev.por_probar.guardar")}
          </button>
        )}
      </div>
      {guardado && (
        <>
          <p className="pp-estado">{t("ev.por_probar.guardado_como", { nombre: guardado })}</p>
          <pre>
            {lineaDeReplay(project, guardado, finding) ||
              `laplace.run_dataset("${guardado}", mi_agente, variant="${t("seg.probar.arreglado")}")`}
          </pre>
        </>
      )}
      {error && <p className="verr">{error}</p>}
    </li>
  );
}
