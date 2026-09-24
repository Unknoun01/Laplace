"use client";

import { useState } from "react";
import { setAlertSettings, testAlert } from "@/lib/api";
import type { AlertSettings } from "@/lib/types";
import { Aviso } from "./aviso";

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
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido guardar" });
    }
  }

  async function probar() {
    try {
      const r = await testAlert(project);
      setMsg(
        r.delivered
          ? { ok: true, texto: "Mensaje de prueba enviado. Míralo en el canal." }
          : { ok: false, texto: "No ha llegado por ningún canal. Revisa las direcciones." },
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido probar" });
    }
  }

  return (
    <section className="sec">
      <h3>Alertas</h3>
      <p className="lead">
        Un mensaje cuando un problema pasa del umbral en dinero ya gastado. Nunca se repite
        el mismo aviso dentro del periodo de calma, y lo que marques como arreglado o
        ignorado no avisa.
      </p>
      <p className={a.enabled ? "vok" : "muted"}>
        {a.muted
          ? "Silenciadas para este proyecto."
          : a.enabled
            ? "Activas."
            : "Apagadas: pon al menos un canal."}
      </p>

      <div className="canales">
        <Canal
          titulo="Slack"
          actual={a.slack}
          valor={slack}
          setValor={setSlack}
          placeholder="https://hooks.slack.com/services/…"
          guardar={() => guardar({ webhook_url: slack.trim() }, "Webhook de Slack guardado.")}
          quitar={() => guardar({ webhook_url: "" }, "Slack quitado.")}
        />
        <Canal
          titulo="Webhook (Teams, Discord, n8n…)"
          actual={a.webhook}
          valor={webhook}
          setValor={setWebhook}
          placeholder="https://…"
          guardar={() =>
            guardar({ generic_webhook_url: webhook.trim() }, "Webhook guardado.")
          }
          quitar={() => guardar({ generic_webhook_url: "" }, "Webhook quitado.")}
        />
        <div className="canal">
          <small>Correo</small>
          <div className="ab" style={{ margin: 0 }}>
            <input
              className="field grow"
              type="email"
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              placeholder="equipo@empresa.com"
            />
            <button
              type="button"
              className="btn small"
              onClick={() =>
                guardar({ email_to: correo.trim() }, correo.trim() ? "Correo guardado." : "Correo quitado.")
              }
              disabled={!correo.trim() && !a.email_to}
            >
              {!correo.trim() && a.email_to ? "Quitar" : "Guardar"}
            </button>
          </div>
          {a.email_to && !a.email_ready && (
            <small className="verr">
              Falta el servidor de correo de la instalación: arranca Laplace con
              LAPLACE_SMTP_HOST, LAPLACE_SMTP_USER y LAPLACE_SMTP_PASSWORD.
            </small>
          )}
        </div>
      </div>

      <div className="ab">
        <label>
          <small>Umbral (dólares ya gastados)</small>
          <input
            className="field"
            inputMode="decimal"
            value={umbral}
            onChange={(e) => setUmbral(e.target.value)}
            style={{ width: 120 }}
          />
        </label>
        <label>
          <small>Calma por problema (horas)</small>
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
              "Umbral y calma guardados.",
            )
          }
        >
          Guardar
        </button>
      </div>

      <fieldset className="reglas">
        <legend>Qué avisa</legend>
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
                  "Reglas guardadas.",
                )
              }
            />{" "}
            {nombre}
          </label>
        ))}
      </fieldset>

      <div className="actions" style={{ paddingTop: 14 }}>
        <button type="button" className="btn" onClick={probar} disabled={!a.enabled}>
          Enviar un mensaje de prueba
        </button>
        <button
          type="button"
          className="btn"
          onClick={() =>
            guardar({ muted: !a.muted }, a.muted ? "Alertas reactivadas." : "Alertas silenciadas.")
          }
        >
          {a.muted ? "Reactivar" : "Silenciar este proyecto"}
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
        {props.actual && <span className="puesto"> · puesto ({props.actual})</span>}
      </small>
      <div className="ab" style={{ margin: 0 }}>
        <input
          className="field grow"
          value={props.valor}
          onChange={(e) => props.setValor(e.target.value)}
          placeholder={props.actual ? "Pega otro para cambiarlo" : props.placeholder}
          aria-label={props.titulo}
        />
        <button
          type="button"
          className="btn small"
          onClick={props.guardar}
          disabled={!props.valor.trim()}
        >
          Guardar
        </button>
        {props.actual && (
          <button type="button" className="btn small" onClick={props.quitar}>
            Quitar
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------

export const REGLAS: [string, string][] = [
  ["repeticion", "Repeticiones"],
  ["bucle", "Bucles"],
  ["modelo_caro", "Modelo caro"],
  ["contexto_fijo", "Contexto sin caché"],
];
