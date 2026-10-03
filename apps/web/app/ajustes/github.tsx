"use client";

import { useEffect, useState } from "react";
import { getGitHub, setGitHub } from "@/lib/api";
import type { GitHubStatus } from "@/lib/types";
import { t } from "@/lib/textos";

/**
 * Lo que hay que poner en GitHub para que avise al fusionar un PR de Laplace (D-191). Con
 * la GitHub App de la instalación los avisos llegan solos; con un token, se pone a mano
 * en el repositorio con el secreto de este proyecto.
 */
function Webhook({ estado }: { estado: GitHubStatus }) {
  if (estado.auth === "app" && estado.app_webhook) {
    return <p className="muted">{t("aj.gh.webhook.app")}</p>;
  }
  const url =
    typeof window === "undefined"
      ? "/api/github/webhook"
      : `${window.location.origin}/api/github/webhook`;
  return (
    <div className="gh-webhook">
      <h4>{t("aj.gh.webhook.titulo")}</h4>
      <p className="muted">{t("aj.gh.webhook.texto")}</p>
      <dl>
        <dt>{t("aj.gh.webhook.url")}</dt>
        <dd>
          <code>{url}</code>
        </dd>
        <dt>{t("aj.gh.webhook.secreto")}</dt>
        <dd>
          <code>{estado.webhook_secret}</code>
        </dd>
      </dl>
    </div>
  );
}

/**
 * El repositorio donde el bot propone los arreglos (D-185). Con la GitHub App de Laplace
 * basta el número de la instalación; sin ella, un token del usuario con permiso de
 * escribir contenido y abrir pull requests en ese repositorio. El token se escribe una
 * vez y no vuelve: la API sólo devuelve sus cuatro últimos caracteres.
 */
export function GitHub({ project }: { project: string }) {
  const [estado, setEstado] = useState<GitHubStatus | null>(null);
  const [repo, setRepo] = useState("");
  const [rama, setRama] = useState("");
  const [token, setToken] = useState("");
  const [instalacion, setInstalacion] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  useEffect(() => {
    getGitHub(project)
      .then((e) => {
        setEstado(e);
        setRepo(e.repo);
        setRama(e.base_branch);
      })
      .catch(() => setEstado(null));
  }, [project]);

  async function guardar(quitar = false) {
    try {
      const nuevo = await setGitHub(
        project,
        quitar
          ? null
          : {
              repo: repo.trim(),
              base_branch: rama.trim(),
              token: token.trim() || null,
              installation_id: instalacion.trim() ? Number(instalacion.trim()) : null,
            },
      );
      setEstado(nuevo);
      setToken("");
      setInstalacion("");
      if (quitar) {
        setRepo("");
        setRama("");
      }
      setMsg({ ok: true, texto: quitar ? t("aj.gh.quitado") : t("aj.gh.guardado") });
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("aj.gh.error") });
    }
  }

  if (!estado) return null;
  return (
    <section className="sec">
      <h2>{t("aj.gh.titulo")}</h2>
      <p className="lead">{t("aj.gh.lead")}</p>
      {estado.configured && (
        <p className="muted">
          {estado.auth === "app"
            ? t("aj.gh.puesto_app", { repo: estado.repo, n: String(estado.installation_id ?? "") })
            : t("aj.gh.puesto_token", { repo: estado.repo, pista: estado.token_hint })}
        </p>
      )}
      <div className="ab">
        <label>
          <small>{t("aj.gh.repo")}</small>
          <input
            className="field"
            placeholder="acme/agentes"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          />
        </label>
        <label>
          <small>{t("aj.gh.rama")}</small>
          <input
            className="field"
            placeholder="main"
            value={rama}
            onChange={(e) => setRama(e.target.value)}
          />
        </label>
        {estado.app_available ? (
          <label>
            <small>{t("aj.gh.instalacion")}</small>
            <input
              className="field"
              inputMode="numeric"
              value={instalacion}
              onChange={(e) => setInstalacion(e.target.value)}
            />
          </label>
        ) : (
          <label>
            <small>{t("aj.gh.token")}</small>
            <input
              className="field"
              type="password"
              autoComplete="off"
              placeholder="github_pat_…"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
        )}
        <button type="button" className="btn" onClick={() => guardar()}>
          {t("aj.gh.guardar")}
        </button>
        {estado.configured && (
          <button type="button" className="btn" onClick={() => guardar(true)}>
            {t("aj.gh.quitar")}
          </button>
        )}
      </div>
      {msg.texto && <p className={msg.ok ? "okline" : "verr"}>{msg.texto}</p>}
      {estado.configured && <Webhook estado={estado} />}
    </section>
  );
}
