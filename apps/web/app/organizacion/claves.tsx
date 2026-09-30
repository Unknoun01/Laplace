"use client";

import { useState } from "react";
import { type Org, createKey, revokeKey } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Aviso, useAviso } from "./aviso";
import { t } from "@/lib/textos";

export function Claves({ org, onChange }: { org: Org; onChange: () => void }) {
  const [proyecto, setProyecto] = useState(org.projects[0] ?? "");
  const [nombre, setNombre] = useState("");
  const [dias, setDias] = useState(0);
  const [nueva, setNueva] = useState<{ key: string; project_id: string } | null>(null);
  const { msg, intentar } = useAviso();
  const activas = (org.keys ?? []).filter((k) => !k.revoked_at);
  const ahora = new Date().toISOString();

  return (
    <section className="sec">
      <h3>{t("cla.titulo")}</h3>
      <p className="lead">{t("cla.lead")}</p>
      <div className="ab">
        <label className="grow">
          <small>{t("cla.proyecto")}</small>
          <input
            className="field"
            list="proyectos-org"
            value={proyecto}
            onChange={(e) => setProyecto(e.target.value)}
            placeholder="mi-agente"
          />
          <datalist id="proyectos-org">
            {org.projects.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </label>
        <label className="grow">
          <small>{t("cla.para_que")}</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder={t("cla.para_que_placeholder")}
          />
        </label>
        <label>
          <small>{t("cla.caduca")}</small>
          <select className="field" value={dias} onChange={(e) => setDias(Number(e.target.value))}>
            <option value={0}>{t("cla.nunca")}</option>
            <option value={30}>{t("cla.30")}</option>
            <option value={90}>{t("cla.90")}</option>
            <option value={365}>{t("cla.365")}</option>
          </select>
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={!/^[A-Za-z0-9._-]+$/.test(proyecto)}
          onClick={async () => {
            let r: { key: string; project_id: string } | null = null;
            const ok = await intentar(async () => {
              r = await createKey({
                org_id: org.id,
                project_id: proyecto,
                name: nombre,
                expires_days: dias,
              });
            }, "");
            if (ok) {
              setNueva(r);
              setNombre("");
              onChange();
            }
          }}
        >
          {t("cla.crear")}
        </button>
      </div>
      {nueva && (
        <div className="una-vez">
          <p>
            <strong>{t("cla.copiala")}</strong> {t("cla.copiala.texto")}
          </p>
          <Copiable texto={nueva.key} />
          <pre>{`import laplace
laplace.init(
    project="${nueva.project_id}",
    endpoint="${typeof window === "undefined" ? "" : window.location.origin}",
    api_key="${nueva.key}",
)`}</pre>
        </div>
      )}
      {activas.length > 0 && (
        <div className="tbl-scroll">
          <table className="tabla-simple ancha">
            <thead>
              <tr>
                <th>{t("cla.proyecto")}</th>
                <th>{t("cla.col.nombre")}</th>
                <th>{t("cla.col.ultimo")}</th>
                <th>{t("cla.caduca")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {activas.map((k) => (
                <tr key={k.id}>
                  <td className="num">{k.project_id}</td>
                  <td>
                    {k.name}
                    {k.created_by && <small className="muted"> · {k.created_by}</small>}
                  </td>
                  <td className="num">{k.last_used_at ? timestamp(k.last_used_at) : t("cla.nunca")}</td>
                  <td className={`num${k.expires_at && k.expires_at < ahora ? " verr" : ""}`}>
                    {k.expires_at ? timestamp(k.expires_at) : t("cla.no_caduca")}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn small danger"
                      onClick={async () => {
                        if (!window.confirm(t("cla.revocar_confirm", { nombre: k.name })))
                          return;
                        if (await intentar(() => revokeKey(org.id, k.id), t("cla.revocada")))
                          onChange();
                      }}
                    >
                      {t("cla.revocar")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="disclaimer">
        {t("cla.rotar")}
      </p>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

export function Copiable({ texto }: { texto: string }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <div className="copiable">
      <code>{texto}</code>
      <button
        type="button"
        className="btn small"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(texto);
            setCopiado(true);
          } catch {
            /* sin permiso de portapapeles: queda seleccionable a mano */
          }
        }}
      >
        {copiado ? t("cla.copiado") : t("cla.copiar")}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------
