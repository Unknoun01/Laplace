"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type Me,
  type Org,
  type Rol,
  type SsoStatus,
  createScimToken,
  deleteSso,
  getSso,
  putSso,
  putSsoDomains,
  revokeScimToken,
} from "@/lib/api";
import { timestamp } from "@/lib/format";
import { t } from "@/lib/textos";
import { Aviso, useAviso } from "./aviso";
import { Copiable } from "./claves";

/**
 * Inicio de sesión único y altas por SCIM de la organización (D-182).
 *
 * Los dominios los pone quien administra la instalación, no la organización: dicen de
 * quién son esos correos, y eso no se lo puede decir uno mismo. El secreto del cliente
 * no vuelve nunca del servidor: el campo vacío lo conserva.
 */
export function Sso({ org, me }: { org: Org; me: Me }) {
  const [estado, setEstado] = useState<SsoStatus | null>(null);
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secreto, setSecreto] = useState("");
  const [rol, setRol] = useState<Rol>("miembro");
  const [obligar, setObligar] = useState(false);
  const [dominios, setDominios] = useState("");
  const [tokenNuevo, setTokenNuevo] = useState("");
  const { msg, intentar } = useAviso();

  const poner = useCallback((s: SsoStatus) => {
    setEstado(s);
    setIssuer(s.issuer);
    setClientId(s.client_id);
    setSecreto("");
    setRol(s.default_role);
    setObligar(s.enforce);
    setDominios(s.domains.join(", "));
  }, []);

  useEffect(() => {
    getSso(org.id).then(poner).catch(() => setEstado(null));
  }, [org.id, poner]);

  if (!estado) return null;
  const esInstalacion = !!me.user?.is_admin;

  return (
    <section className="sec sso">
      <h3>{t("sso.titulo")}</h3>
      <p className="muted">{t("sso.explica")}</p>

      <div className="ab">
        <label className="grow">
          <small>{t("sso.dominios")}</small>
          <input
            className="field"
            value={dominios}
            onChange={(e) => setDominios(e.target.value)}
            placeholder="empresa.com"
            disabled={!esInstalacion}
          />
        </label>
        {esInstalacion && (
          <button
            type="button"
            className="btn"
            onClick={async () => {
              let s: SsoStatus | null = null;
              const lista = dominios.split(",").map((d) => d.trim()).filter(Boolean);
              if (await intentar(async () => (s = await putSsoDomains(org.id, lista)), t("sso.guardado")))
                poner(s!);
            }}
          >
            {t("sso.guardar_dominios")}
          </button>
        )}
      </div>
      {!esInstalacion && <small className="muted">{t("sso.dominios_quien")}</small>}

      <div className="ab">
        <label className="grow">
          <small>{t("sso.emisor")}</small>
          <input
            className="field"
            value={issuer}
            onChange={(e) => setIssuer(e.target.value)}
            placeholder="https://empresa.okta.com"
          />
        </label>
        <label className="grow">
          <small>{t("sso.cliente")}</small>
          <input className="field" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </label>
      </div>
      <div className="ab">
        <label className="grow">
          <small>{t("sso.secreto")}</small>
          <input
            className="field"
            type="password"
            value={secreto}
            onChange={(e) => setSecreto(e.target.value)}
            placeholder={estado.has_secret ? t("sso.secreto_guardado") : ""}
            autoComplete="off"
          />
        </label>
        <label>
          <small>{t("sso.rol")}</small>
          <select className="field" value={rol} onChange={(e) => setRol(e.target.value as Rol)}>
            <option value="lector">{t("rol.lector")}</option>
            <option value="miembro">{t("rol.miembro")}</option>
            <option value="admin">{t("rol.admin")}</option>
          </select>
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={obligar}
          disabled={estado.domains.length === 0}
          onChange={(e) => setObligar(e.target.checked)}
        />{" "}
        {t("sso.obligar")}
      </label>
      <div className="actions" style={{ paddingTop: 10 }}>
        <button
          type="button"
          className="btn primary"
          disabled={!issuer.startsWith("https://") || !clientId}
          onClick={async () => {
            let s: SsoStatus | null = null;
            const ok = await intentar(async () => {
              s = await putSso({
                org_id: org.id,
                issuer: issuer.trim(),
                client_id: clientId.trim(),
                client_secret: secreto ? secreto : null,
                default_role: rol,
                enforce: obligar,
              });
            }, t("sso.guardado"));
            if (ok) poner(s!);
          }}
        >
          {t("sso.guardar")}
        </button>
        {estado.configured && (
          <button
            type="button"
            className="btn"
            onClick={async () => {
              let s: SsoStatus | null = null;
              if (await intentar(async () => (s = await deleteSso(org.id)), t("sso.quitado")))
                poner(s!);
            }}
          >
            {t("sso.quitar")}
          </button>
        )}
      </div>
      <p className="muted">{t("sso.redireccion")}</p>
      <Copiable texto={estado.redirect_uri} />

      <h4>{t("sso.scim")}</h4>
      <p className="muted">{t("sso.scim_explica")}</p>
      <Copiable texto={estado.scim_url} />
      {estado.scim_tokens.length > 0 && (
        <ul className="lista-simple">
          {estado.scim_tokens.map((k) => (
            <li key={k.id}>
              {t("sso.scim_token", { fecha: timestamp(k.created_at) })}
              {k.last_used_at && <> · {t("sso.scim_usado", { fecha: timestamp(k.last_used_at) })}</>}{" "}
              <button
                type="button"
                className="btn small"
                onClick={async () => {
                  if (await intentar(() => revokeScimToken(org.id, k.id), t("sso.scim_revocado")))
                    getSso(org.id).then(poner);
                }}
              >
                {t("sso.scim_revocar")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="actions" style={{ paddingTop: 10 }}>
        <button
          type="button"
          className="btn"
          onClick={async () => {
            let token = "";
            if (await intentar(async () => (token = (await createScimToken(org.id)).token), "")) {
              setTokenNuevo(token);
              getSso(org.id).then(poner);
            }
          }}
        >
          {t("sso.scim_crear")}
        </button>
      </div>
      {tokenNuevo && (
        <div className="una-vez">
          <p>{t("sso.scim_una_vez")}</p>
          <Copiable texto={tokenNuevo} />
        </div>
      )}
      <Aviso {...msg} />
    </section>
  );
}
