"use client";

import { useState } from "react";
import { type Org, createKey, revokeKey } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Aviso, useAviso } from "./aviso";

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
      <h3>Claves de API</h3>
      <p className="lead">
        Una clave por proyecto: es con lo que tu agente manda trazas. Escribe un nombre de
        proyecto nuevo para crearlo.
      </p>
      <div className="ab">
        <label className="grow">
          <small>Proyecto</small>
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
          <small>Para qué es</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="ingesta producción"
          />
        </label>
        <label>
          <small>Caduca</small>
          <select className="field" value={dias} onChange={(e) => setDias(Number(e.target.value))}>
            <option value={0}>nunca</option>
            <option value={30}>en 30 días</option>
            <option value={90}>en 90 días</option>
            <option value={365}>en un año</option>
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
          Crear clave
        </button>
      </div>
      {nueva && (
        <div className="una-vez">
          <p>
            <strong>Cópiala ahora:</strong> sólo se guarda su huella y no se puede volver a
            ver. Si la pierdes, revócala y crea otra.
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
        <table className="tabla-simple ancha">
          <thead>
            <tr>
              <th>Proyecto</th>
              <th>Nombre</th>
              <th>Último uso</th>
              <th>Caduca</th>
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
                <td className="num">{k.last_used_at ? timestamp(k.last_used_at) : "nunca"}</td>
                <td className={`num${k.expires_at && k.expires_at < ahora ? " verr" : ""}`}>
                  {k.expires_at ? timestamp(k.expires_at) : "no caduca"}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn small danger"
                    onClick={async () => {
                      if (!window.confirm(`¿Revocar «${k.name}»? Deja de servir al momento.`))
                        return;
                      if (await intentar(() => revokeKey(org.id, k.id), "Clave revocada."))
                        onChange();
                    }}
                  >
                    Revocar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="disclaimer">
        Para rotar una clave: crea la nueva, cámbiala en tu agente y revoca la vieja.
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
        {copiado ? "Copiado" : "Copiar"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------
