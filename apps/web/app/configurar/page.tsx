"use client";

import { useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { getMe, setupInstallation } from "@/lib/api";

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
      setError(err instanceof Error ? err.message : "no se ha podido configurar");
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
        <h1>Configura esta instalación</h1>
        <p className="muted">
          Todavía no hay ninguna cuenta. La primera administra la instalación entera y se
          queda con los proyectos que ya tengan datos.
        </p>
        {campo("token", "Código de configuración", { required: true, autoFocus: true })}
        <p className="hint">
          Está en el log del servidor, en la línea «no hay ninguna cuenta todavía». Con
          Docker: <code>docker compose logs backend | grep configurar</code>
        </p>
        {campo("org_name", "Nombre de tu organización", { placeholder: "Mi empresa" })}
        {campo("name", "Tu nombre", { autoComplete: "name" })}
        {campo("email", "Email", { type: "email", required: true, autoComplete: "username" })}
        {campo("password", "Contraseña (10 caracteres o más)", {
          type: "password",
          required: true,
          minLength: 10,
          autoComplete: "new-password",
        })}
        {error && <p className="verr">{error}</p>}
        <button type="submit" className="btn primary" disabled={enviando}>
          {enviando ? "Creando…" : "Crear la cuenta de administración"}
        </button>
      </form>
    </main>
  );
}
