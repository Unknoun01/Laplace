"use client";

import Link from "next/link";
import { useState } from "react";
import { clearFindingState, createDataset, setFindingState } from "@/lib/api";
import { duration, money, number, timestamp, tokens } from "@/lib/format";
import type { FindingDetail } from "@/lib/types";

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

export function porEjecucion(valor: number, unidad: "usd" | "tokens" | "ms"): string {
  if (unidad === "usd") return money(valor);
  if (unidad === "tokens") return `${tokens(valor)} tokens`;
  return duration(valor);
}
