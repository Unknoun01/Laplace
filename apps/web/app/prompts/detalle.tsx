"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { addPromptVersion, deletePrompt, promptDiff } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import type { Diff, PromptCard } from "@/lib/types";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

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
      setError(e instanceof Error ? e.message : t("seg.error.guardar"));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="prompt-det">
      <h3 className="sub">{t("pr.que_cambio")}</h3>
      <div className="ab">
        <label>
          <small>{t("pr.de")}</small>
          <select className="field" value={a} onChange={(e) => setA(Number(e.target.value))}>
            {guardadas.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>{t("pr.a")}</small>
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
        <p className="disclaimer">{t("pr.elige_dos")}</p>
      )}

      {elegida && (
        <>
          <h3 className="sub">{t("pr.texto_v", { n: elegida.version })}</h3>
          <pre>{elegida.text}</pre>
        </>
      )}

      <fieldset className="sin-marco" disabled={!escribir}>
      <h3 className="sub">{t("pr.guardar_nueva")}</h3>
      <p className="lead">{t("pr.guardar_nueva.lead")}</p>
      <textarea
        className="field"
        rows={6}
        value={texto}
        placeholder={t("pr.texto.placeholder")}
        onChange={(e) => setTexto(e.target.value)}
      />
      <div className="actions">
        <button type="button" className="btn" onClick={guardar} disabled={guardando || !texto.trim()}>
          {guardando ? t("pr.guardando") : t("pr.guardar")}
        </button>
        <span className="chip where">{t("pr.guardada")}</span>
      </div>
      {error && <p className="verr">{error}</p>}
      </fieldset>

      {prompt.deploys.length > 0 && (
        <div className="pro">
          <h3 className="sub">{t("pr.historial")}</h3>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("pr.col.cuando")}</th>
                  <th>{t("pr.col.version")}</th>
                  <th>{t("pr.col.quien")}</th>
                  <th>{t("pr.col.nota")}</th>
                </tr>
              </thead>
              <tbody>
                {prompt.deploys.map((d) => (
                  <tr key={d.id}>
                    <td>{timestamp(d.at)}</td>
                    <td>
                      v{d.version}
                      {d.rollback && <span className="chip where mini">{t("pr.vuelta_atras")}</span>}
                    </td>
                    <td>{d.actor || "—"}</td>
                    <td>{d.note || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="disclaimer">
            {tr("pr.historial.nota", {
              no: <strong>{t("pr.no")}</strong>,
              panel: <Link href={`/panel${query}`}>{t("pr.panel")}</Link>,
            })}
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
          {t("pr.borrar")}
        </button>
        <span className="chip where">
          {t("pr.borrar.nota")}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Sin adoptar: la pestaña no se queda en blanco
// ---------------------------------------------------------------------------------
