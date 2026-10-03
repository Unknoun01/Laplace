"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getGitHub, openPullRequest } from "@/lib/api";
import type { FindingDetail, GitHubStatus } from "@/lib/types";
import { t } from "@/lib/textos";

/**
 * El arreglo, propuesto como pull request (D-185). Sólo aparece cuando hay un cambio
 * mecánico que proponer —hoy, el de modelo— y quien mira puede escribir. Si el bot no
 * puede proponerlo con seguridad (el modelo sale de una variable, la llamada no tiene
 * sitio anotado), lo dice con su motivo en lugar de abrir un PR a ciegas.
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

  if (!finding.model_change || !github) return null;

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
      <p className="muted">
        {t("prob.pr.lead", { de: finding.model_change.from, a: finding.model_change.to })}
      </p>
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
