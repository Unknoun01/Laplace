"use client";

import { useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { getMe, setupInstallation } from "@/lib/api";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

/**
 * Configurar la instalación: la primera cuenta (D-127).
 *
 * Pide el código que el servidor escribió en su log al arrancar. Sin él, la primera
 * cuenta sería de quien primero llegara a la URL: desplegar sería una carrera. Quien
 * puede leer el log es quien administra el despliegue, que es a quien le toca.
 */
export default function ConfigurarPage() {
  const [listo, setListo] = useState(false);
  const [f, setF] = useState({ token: "", org_name: "", name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    getMe()
      .then((me) => {
        if (me.mode === "local" || me.user || !me.needs_setup) window.location.href = "/";
        else setListo(true);
      })
      .catch(() => setListo(true));
  }, []);

  async function crear(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError("");
    try {
      await setupInstallation({ ...f, email: f.email.trim(), token: f.token.trim() });
      window.location.href = "/organizacion";
    } catch (err) {
      setError(err instanceof Error ? err.message : t("conf.error"));
      setEnviando(false);
    }
  }

  const campo = (clave: keyof typeof f, etiqueta: string, extra: object = {}) => (
    <label>
      <small>{etiqueta}</small>
      <input
        className="field"
        value={f[clave]}
        onChange={(e) => setF({ ...f, [clave]: e.target.value })}
        {...extra}
      />
    </label>
  );

  if (!listo) return <Cargando />;
  return (
    <main className="auth">
      <form className="auth-card" onSubmit={crear}>
        <h1>{t("conf.titulo")}</h1>
        <p className="muted">{t("conf.texto")}</p>
        {campo("token", t("conf.codigo"), { required: true, autoFocus: true })}
        <p className="hint">
          {tr("conf.codigo.ayuda", {
            comando: <code>docker compose logs backend | grep configurar</code>,
          })}
        </p>
        {campo("org_name", t("conf.org"), { placeholder: t("conf.org_placeholder") })}
        {campo("name", t("conf.nombre"), { autoComplete: "name" })}
        {campo("email", t("org.email"), { type: "email", required: true, autoComplete: "username" })}
        {campo("password", t("conf.contrasena"), {
          type: "password",
          required: true,
          minLength: 10,
          autoComplete: "new-password",
        })}
        {error && <p className="verr">{error}</p>}
        <button type="submit" className="btn primary" disabled={enviando}>
          {enviando ? t("conf.creando") : t("conf.crear")}
        </button>
      </form>
    </main>
  );
}
