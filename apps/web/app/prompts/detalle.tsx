"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { addPromptVersion, deletePrompt, promptDiff } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import type { Diff, PromptCard } from "@/lib/types";

export function Detalle({
  prompt,
  project,
  query,
  onChange,
}: {
  prompt: PromptCard;
  project: string;
  query: string;
  onChange: () => void;
}) {
  const { escribir } = usePermisos(project);
  const guardadas = prompt.versions.filter((v) => v.version > 0);
  const [b, setB] = useState(guardadas[0]?.version ?? 0);
  const [a, setA] = useState(guardadas[1]?.version ?? guardadas[0]?.version ?? 0);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [texto, setTexto] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let vigente = true;
    if (!a || !b || a === b) {
      setDiff(null);
      return;
    }
    promptDiff(prompt.id, a, b)
      .then((d) => vigente && setDiff(d))
      .catch(() => vigente && setDiff(null));
    return () => {
      vigente = false;
    };
  }, [prompt.id, a, b]);

  const elegida = guardadas.find((v) => v.version === b);

  async function guardar() {
    if (!texto.trim()) return;
    setGuardando(true);
    setError("");
    try {
      await addPromptVersion(prompt.id, { project_id: project, text: texto });
      setTexto("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido guardar");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="prompt-det">
      <h3 className="sub">Qué cambió</h3>
      <div className="ab">
        <label>
          <small>De</small>
          <select className="field" value={a} onChange={(e) => setA(Number(e.target.value))}>
            {guardadas.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>A</small>
          <select className="field" value={b} onChange={(e) => setB(Number(e.target.value))}>
            {guardadas.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </label>
        {diff && <span className="chip where">{diff.summary}</span>}
      </div>

      {diff ? (
        <div className="diff">
          {diff.lines.map((linea, i) => (
            <div key={i} className={`dl ${linea.op === "+" ? "add" : linea.op === "-" ? "del" : ""}`}>
              <span className="dn pro">{linea.left ?? ""}</span>
              <span className="dn pro">{linea.right ?? ""}</span>
              <span className="dop">{linea.op === "=" ? " " : linea.op}</span>
              <span className="dt">{linea.text || " "}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="disclaimer">
          Elige dos versiones distintas para ver el diff. Con una sola no hay nada que
          comparar.
        </p>
      )}

      {elegida && (
        <>
          <h3 className="sub">El texto de la v{elegida.version}</h3>
          <pre>{elegida.text}</pre>
        </>
      )}

      <fieldset className="sin-marco" disabled={!escribir}>
      <h3 className="sub">Guardar una versión nueva</h3>
      <p className="lead">
        Guardar no despliega. Son dos gestos distintos a propósito: es lo que te deja
        preparar una versión con calma y lo que hace que exista el botón de volver atrás.
      </p>
      <textarea
        className="field"
        rows={6}
        value={texto}
        placeholder="El texto del prompt. Usa {{variable}} para lo que cambie en cada llamada."
        onChange={(e) => setTexto(e.target.value)}
      />
      <div className="actions">
        <button type="button" className="btn" onClick={guardar} disabled={guardando || !texto.trim()}>
          {guardando ? "Guardando…" : "Guardar versión"}
        </button>
        <span className="chip where">Queda guardada, no servida</span>
      </div>
      {error && <p className="verr">{error}</p>}
      </fieldset>

      {prompt.deploys.length > 0 && (
        <div className="pro">
          <h3 className="sub">Historial de despliegues</h3>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Cuándo</th>
                  <th>Versión</th>
                  <th>Quién</th>
                  <th>Nota</th>
                </tr>
              </thead>
              <tbody>
                {prompt.deploys.map((d) => (
                  <tr key={d.id}>
                    <td>{timestamp(d.at)}</td>
                    <td>
                      v{d.version}
                      {d.rollback && <span className="chip where mini">vuelta atrás</span>}
                    </td>
                    <td>{d.actor || "—"}</td>
                    <td>{d.note || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="disclaimer">
            Este historial cuenta la historia; <strong>no</strong> atribuye picos. Para eso
            el <Link href={`/panel${query}`}>panel</Link> usa la versión que aparece en las
            trazas del tramo, que es un hecho medido y no una coincidencia de horarios.
          </p>
        </div>
      )}

      <div className="actions">
        <button
          type="button"
          className="vbtn ghost"
          disabled={!escribir}
          onClick={async () => {
            await deletePrompt(prompt.id, project);
            onChange();
          }}
        >
          Borrar este prompt
        </button>
        <span className="chip where">
          Las trazas no se tocan: seguirán diciendo con qué versión corrieron
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Sin adoptar: la pestaña no se queda en blanco
// ---------------------------------------------------------------------------------
