"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { aprobarArregloTexto, getArregloTexto, proponerArregloTexto, type ArregloTexto } from "@/lib/api";
import { money } from "@/lib/format";
import type { FindingDetail } from "@/lib/types";
import { t } from "@/lib/textos";
import { BotonReplay } from "./boton-replay";

/** Los hallazgos con arreglo de texto (D-221); el backend tiene la misma lista. */
const CON_ARREGLO = ["contexto_fijo", "json_roto"];

/**
 * El arreglo de texto (D-221): lo escribe un modelo, se prueba con un replay y lo aprueba
 * una persona aquí, con el diff delante. Sin un replay de esta misma propuesta, el botón
 * de aprobar no se ofrece: se dice que no está probada y cómo probarla.
 */
export function ArregloTexto({
  project,
  finding,
  days,
  query,
}: {
  project: string;
  finding: FindingDetail;
  days: number;
  query: string;
}) {
  const [vista, setVista] = useState<ArregloTexto | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const [hecho, setHecho] = useState<{ url?: string; version?: number } | null>(null);
  const conArreglo = CON_ARREGLO.includes(finding.kind);

  const mirar = useCallback(() => {
    if (!conArreglo) return;
    getArregloTexto(project, finding.id)
      .then(setVista)
      .catch(() => setVista(null));
  }, [project, finding.id, conArreglo]);
  useEffect(mirar, [mirar]);

  if (!conArreglo || !vista || (!vista.disponible && !vista.propuesta)) return null;
  const p = vista.propuesta;

  async function hacer(accion: () => Promise<void>) {
    setOcupado(true);
    setError("");
    try {
      await accion();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  }
  const proponer = () => hacer(async () => setVista(await proponerArregloTexto(project, finding.id, days)));
  const aprobar = () =>
    hacer(async () => {
      if (p) setHecho(await aprobarArregloTexto(project, finding.id, p.sha));
    });

  const donde = !p
    ? ""
    : p.donde.tipo === "prompt"
      ? t("prob.texto.donde_prompt", { nombre: p.donde.nombre ?? "", version: p.donde.version ?? 0 })
      : t("prob.texto.donde_codigo", { ruta: p.donde.ruta ?? "", linea: p.donde.linea ?? 0 });
  const orden =
    p && vista.conjunto
      ? `laplace replay "${vista.conjunto}" --modelo ${p.modelo} --propuesta "${finding.id}" --tope 1`
      : "";

  return (
    <section className="block prob-texto" data-arreglo-texto>
      <h2>{t("prob.texto.titulo")}</h2>
      <p className="muted">{t(vista.objetivo === "json" ? "prob.texto.lead_json" : "prob.texto.lead")}</p>

      {p && (
        <>
          <p className="muted">{donde}</p>
          {p.explicacion && <p>{p.explicacion}</p>}
          {vista.diff && (
            <div className="diff">
              {vista.diff.lines.map((linea, i) => (
                <div key={i} className={`dl ${linea.op === "+" ? "add" : linea.op === "-" ? "del" : ""}`}>
                  <span className="dn pro">{linea.left ?? ""}</span>
                  <span className="dn pro">{linea.right ?? ""}</span>
                  <span className="dop">{linea.op === "=" ? " " : linea.op}</span>
                  <span className="dt">{linea.text || " "}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <p className="disclaimer">
        {t("prob.texto.gasto", {
          n: vista.gasto.calls,
          usd: money(vista.gasto.usd),
          minimo: vista.gasto.unknown ? t("prob.texto.al_menos") : "",
        })}
      </p>

      {p &&
        (vista.probada ? (
          <p className="okline">
            {t("prob.texto.probada", { n: vista.probada.casos, fallidos: vista.probada.fallidos })}{" "}
            <Link href={`/evaluaciones${query}`}>{t("prob.texto.comparar")}</Link>
          </p>
        ) : (
          <div>
            <p className="verr">{t("prob.texto.sin_probar")}</p>
            {orden ? (
              <>
                <pre>{orden}</pre>
                <BotonReplay project={project} conjunto={vista.conjunto} modelo={p.modelo} propuesta={finding.id} />
              </>
            ) : (
              <p className="muted">{t("prob.texto.sin_conjunto")}</p>
            )}
          </div>
        ))}

      {hecho?.url ? (
        <p className="okline">
          <a href={hecho.url} target="_blank" rel="noreferrer">
            {t("prob.pr.abierto")}
          </a>
        </p>
      ) : hecho?.version ? (
        <p className="okline">
          <Link href={`/prompts${query}`}>{t("prob.texto.version_creada", { n: hecho.version })}</Link>
        </p>
      ) : (
        <p>
          {p && (
            <button type="button" className="btn primary" onClick={aprobar} disabled={ocupado || !vista.probada}>
              {p.donde.tipo === "prompt"
                ? t("prob.texto.aprobar_version", { nombre: p.donde.nombre ?? "" })
                : t("prob.texto.aprobar_pr")}
            </button>
          )}{" "}
          {vista.disponible && (
            <button type="button" className="btn" onClick={proponer} disabled={ocupado}>
              {t(p ? "prob.texto.otra" : "prob.texto.proponer")}
            </button>
          )}
        </p>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
  );
}
