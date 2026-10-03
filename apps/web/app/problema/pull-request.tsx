"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getGitHub, openPullRequest } from "@/lib/api";
import type { FindingDetail, GitHubStatus } from "@/lib/types";
import { t } from "@/lib/textos";

/**
 * El arreglo, propuesto como pull request (D-185, D-190). Sólo aparece cuando hay un
 * cambio mecánico que proponer —el de modelo, `cache_control` o un tope de vueltas— y
 * quien mira puede escribir. Si el bot no puede proponerlo con seguridad (el modelo sale
 * de una variable, el `system` no se sabe si es texto, la llamada no tiene sitio
 * anotado), lo dice con su motivo en lugar de abrir un PR a ciegas.
 */
export function PullRequest({
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
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [abriendo, setAbriendo] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getGitHub(project)
      .then(setGithub)
      .catch(() => setGithub(null));
  }, [project]);

  if (!finding.code_fix || !github) return null;
  const lead =
    finding.code_fix === "modelo" && finding.model_change
      ? t("prob.pr.lead", { de: finding.model_change.from, a: finding.model_change.to })
      : finding.code_fix === "cache"
        ? t("prob.pr.lead_cache")
        : finding.code_fix === "tope"
          ? t("prob.pr.lead_tope")
          : "";
  if (!lead) return null;

  async function abrir() {
    setAbriendo(true);
    setError("");
    try {
      const pr = await openPullRequest(project, finding.id, days);
      setUrl(pr.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("prob.pr.error"));
    } finally {
      setAbriendo(false);
    }
  }

  return (
    <section className="block prob-pr">
      <h2>{t("prob.pr.titulo")}</h2>
      <p className="muted">{lead}</p>
      {!github.configured ? (
        <p className="muted">
          <Link href={`/ajustes${query}`}>{t("prob.pr.conectar")}</Link>
        </p>
      ) : url ? (
        <p className="okline">
          <a href={url} target="_blank" rel="noreferrer">
            {t("prob.pr.abierto")}
          </a>
        </p>
      ) : (
        <button type="button" className="btn primary" onClick={abrir} disabled={abriendo}>
          {t("prob.pr.abrir", { repo: github.repo })}
        </button>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
  );
}
