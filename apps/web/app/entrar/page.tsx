"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { entrarConClave, getMe, login, ssoStart } from "@/lib/api";
import { t } from "@/lib/textos";

/**
 * Entrar (D-127). Email y contraseña; nada más en la primera pantalla.
 *
 * El error es siempre el mismo —«email o contraseña incorrectos»— aunque el email no
 * exista: decir cuál de los dos falla es decirle a quien prueba qué correos tienen
 * cuenta.
 */
function Contenido() {
  const params = useSearchParams();
  const siguiente = seguro(params.get("next"));
  const [email, setEmail] = useState("");
  const [contrasena, setContrasena] = useState("");
  // La vuelta de un SSO que no salió (D-182) trae el motivo en la URL.
  const vueltaSso = params.get("sso_error");
  const [error, setError] = useState(vueltaSso ? t(errorSso(vueltaSso)) : "");
  const [enviando, setEnviando] = useState(false);
  const [listo, setListo] = useState(false);

  useEffect(() => {
    getMe()
      .then((me) => {
        if (me.mode === "local" || me.user) window.location.href = siguiente;
        else if (me.needs_setup) window.location.href = "/configurar";
        else setListo(true);
      })
      .catch(() => setListo(true));
  }, [siguiente]);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError("");
    try {
      await login(email.trim(), contrasena);
      window.location.href = siguiente;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ent.error"));
      setEnviando(false);
    }
  }

  async function entrarSso() {
    setEnviando(true);
    setError("");
    try {
      const { url } = await ssoStart(email.trim(), siguiente);
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ent.error"));
      setEnviando(false);
    }
  }

  if (!listo) return <Cargando />;
  return (
    <main className="auth">
      <form className="auth-card" onSubmit={entrar}>
        <h1>{t("ent.titulo")}</h1>
        <label>
          <small>{t("org.email")}</small>
          <input
            className="field"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label>
          <small>{t("ent.contrasena")}</small>
          <input
            className="field"
            type="password"
            autoComplete="current-password"
            value={contrasena}
            onChange={(e) => setContrasena(e.target.value)}
            required
          />
        </label>
        {error && <p className="verr">{error}</p>}
        <button type="submit" className="btn primary" disabled={enviando}>
          {enviando ? t("ent.entrando") : t("ent.entrar")}
        </button>
        {/* Con el correo basta: el dominio dice qué proveedor es (D-182). */}
        <button
          type="button"
          className="btn"
          disabled={enviando || !email.includes("@")}
          onClick={entrarSso}
        >
          {t("ent.sso")}
        </button>
        <p className="muted">
          {t("ent.sin_cuenta")}
        </p>
        <ConClave siguiente={siguiente} />
      </form>
    </main>
  );
}

/**
 * Entrar con una clave de API en vez de con cuenta: para quien sólo tiene la de un
 * proyecto. Plegado, porque no es lo normal para una persona.
 */
function ConClave({ siguiente }: { siguiente: string }) {
  const [clave, setClave] = useState("");
  const [comprobando, setComprobando] = useState(false);
  const [fallo, setFallo] = useState("");
  return (
    <details className="porque con-clave">
      <summary>{t("ent.tengo_clave")}</summary>
      <div className="ab" style={{ margin: "8px 0 0" }}>
        <input
          className="field grow"
          type="password"
          placeholder="lp_…"
          value={clave}
          onChange={(e) => setClave(e.target.value)}
        />
        <button
          type="button"
          className="btn"
          disabled={!clave.startsWith("lp_") || comprobando}
          onClick={() => {
            setComprobando(true);
            setFallo("");
            entrarConClave(clave)
              .then(() => {
                window.location.href = siguiente;
              })
              .catch((e: Error) => {
                setFallo(e.message);
                setComprobando(false);
              });
          }}
        >
          {comprobando ? t("ent.comprobando") : t("ent.usar_clave")}
        </button>
      </div>
      {fallo && <p className="disclaimer">{fallo}</p>}
    </details>
  );
}

const ERRORES_SSO = {
  estado: "ent.sso_error.estado",
  cancelado: "ent.sso_error.cancelado",
  token: "ent.sso_error.token",
  correo: "ent.sso_error.correo",
  dominio: "ent.sso_error.dominio",
  proveedor: "ent.sso_error.proveedor",
  sin_invitacion: "ent.sso_error.sin_invitacion",
  fuera: "ent.sso_error.fuera",
} as const;

function errorSso(codigo: string) {
  return ERRORES_SSO[codigo as keyof typeof ERRORES_SSO] ?? "ent.sso_error.otro";
}

/** Sólo rutas de este mismo sitio: un `next` hacia fuera sería un redirector abierto. */
function seguro(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return "/";
  return next;
}

export default function EntrarPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
