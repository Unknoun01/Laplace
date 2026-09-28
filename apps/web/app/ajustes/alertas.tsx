"use client";

import { useState } from "react";
import { setAlertSettings, testAlert } from "@/lib/api";
import type { AlertSettings } from "@/lib/types";
import { Aviso } from "./aviso";
import { t } from "@/lib/textos";

export function Alertas({ project, inicial }: { project: string; inicial: AlertSettings }) {
  const [a, setA] = useState(inicial);
  const [slack, setSlack] = useState("");
  const [webhook, setWebhook] = useState("");
  const [correo, setCorreo] = useState(inicial.email_to);
  const [umbral, setUmbral] = useState(String(inicial.min_usd));
  const [calma, setCalma] = useState(String(inicial.quiet_hours));
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  async function guardar(cambios: Parameters<typeof setAlertSettings>[1], texto: string) {
    try {
      setA(await setAlertSettings(project, cambios));
      setMsg({ ok: true, texto });
      setSlack("");
      setWebhook("");
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("seg.error.guardar") });
    }
  }

  async function probar() {
    try {
      const r = await testAlert(project);
      setMsg(
        r.delivered
          ? { ok: true, texto: t("aj.al.prueba_ok") }
          : { ok: false, texto: t("aj.al.prueba_mal") },
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("aj.al.error.probar") });
    }
  }

  return (
    <section className="sec">
      <h3>{t("aj.al.titulo")}</h3>
      <p className="lead">{t("aj.al.lead")}</p>
      <p className={a.enabled ? "vok" : "muted"}>
        {a.muted
          ? t("aj.al.silenciadas")
          : a.enabled
            ? t("aj.al.activas")
            : t("aj.al.apagadas")}
      </p>

      <div className="canales">
        <Canal
          titulo="Slack"
          actual={a.slack}
          valor={slack}
          setValor={setSlack}
          placeholder="https://hooks.slack.com/services/…"
          guardar={() => guardar({ webhook_url: slack.trim() }, t("aj.al.slack_guardado"))}
          quitar={() => guardar({ webhook_url: "" }, t("aj.al.slack_quitado"))}
        />
        <Canal
          titulo={t("aj.al.webhook_titulo")}
          actual={a.webhook}
          valor={webhook}
          setValor={setWebhook}
          placeholder="https://…"
          guardar={() =>
            guardar({ generic_webhook_url: webhook.trim() }, t("aj.al.webhook_guardado"))
          }
          quitar={() => guardar({ generic_webhook_url: "" }, t("aj.al.webhook_quitado"))}
        />
        <div className="canal">
          <small>{t("aj.al.correo")}</small>
          <div className="ab" style={{ margin: 0 }}>
            <input
              className="field grow"
              type="email"
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              placeholder={t("aj.al.correo_placeholder")}
            />
            <button
              type="button"
              className="btn small"
              onClick={() =>
                guardar(
                  { email_to: correo.trim() },
                  correo.trim() ? t("aj.al.correo_guardado") : t("aj.al.correo_quitado"),
                )
              }
              disabled={!correo.trim() && !a.email_to}
            >
              {!correo.trim() && a.email_to ? t("comun.quitar") : t("comun.guardar")}
            </button>
          </div>
          {a.email_to && !a.email_ready && (
            <small className="verr">
              {t("aj.al.smtp")}
            </small>
          )}
        </div>
      </div>

      <div className="ab">
        <label>
          <small>{t("aj.al.umbral")}</small>
          <input
            className="field"
            inputMode="decimal"
            value={umbral}
            onChange={(e) => setUmbral(e.target.value)}
            style={{ width: 120 }}
          />
        </label>
        <label>
          <small>{t("aj.al.calma")}</small>
          <input
            className="field"
            inputMode="decimal"
            value={calma}
            onChange={(e) => setCalma(e.target.value)}
            style={{ width: 120 }}
          />
        </label>
        <button
          type="button"
          className="btn"
          onClick={() =>
            guardar(
              {
                threshold: Number(umbral.replace(",", ".")) || 0,
                quiet_hours: Number(calma.replace(",", ".")) || 0,
              },
              t("aj.al.umbral_guardado"),
            )
          }
        >
          {t("comun.guardar")}
        </button>
      </div>

      <fieldset className="reglas">
        <legend>{t("aj.al.que_avisa")}</legend>
        {REGLAS.map(([clave, nombre]) => (
          <label key={clave}>
            <input
              type="checkbox"
              checked={!a.muted_kinds.includes(clave)}
              onChange={(e) =>
                guardar(
                  {
                    muted_kinds: e.target.checked
                      ? a.muted_kinds.filter((k) => k !== clave)
                      : [...a.muted_kinds, clave],
                  },
                  t("aj.al.reglas_guardadas"),
                )
              }
            />{" "}
            {t(nombre)}
          </label>
        ))}
      </fieldset>

      <div className="actions" style={{ paddingTop: 14 }}>
        <button type="button" className="btn" onClick={probar} disabled={!a.enabled}>
          {t("aj.al.probar")}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() =>
            guardar({ muted: !a.muted }, a.muted ? t("aj.al.reactivadas") : t("aj.al.silenciadas_ok"))
          }
        >
          {a.muted ? t("aj.al.reactivar") : t("aj.al.silenciar")}
        </button>
      </div>
      <Aviso {...msg} />
    </section>
  );
}

export function Canal(props: {
  titulo: string;
  actual: string;
  valor: string;
  setValor: (v: string) => void;
  placeholder: string;
  guardar: () => void;
  quitar: () => void;
}) {
  return (
    <div className="canal">
      <small>
        {props.titulo}
        {props.actual && <span className="puesto">{t("aj.al.puesto", { actual: props.actual })}</span>}
      </small>
      <div className="ab" style={{ margin: 0 }}>
        <input
          className="field grow"
          value={props.valor}
          onChange={(e) => props.setValor(e.target.value)}
          placeholder={props.actual ? t("aj.al.otro") : props.placeholder}
          aria-label={props.titulo}
        />
        <button
          type="button"
          className="btn small"
          onClick={props.guardar}
          disabled={!props.valor.trim()}
        >
          {t("comun.guardar")}
        </button>
        {props.actual && (
          <button type="button" className="btn small" onClick={props.quitar}>
            {t("comun.quitar")}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------

export const REGLAS = [
  ["repeticion", "aj.regla.repeticion"],
  ["bucle", "aj.regla.bucle"],
  ["modelo_caro", "aj.regla.modelo_caro"],
  ["contexto_fijo", "aj.regla.contexto_fijo"],
  ["prompt_caro", "aj.regla.prompt_caro"],
] as const;
