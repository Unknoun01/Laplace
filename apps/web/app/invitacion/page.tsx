"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { type Rol, acceptInvitation, getInvitation } from "@/lib/api";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

const QUE_PUEDE = {
  lector: "inv.puede.lector",
  miembro: "inv.puede.miembro",
  admin: "inv.puede.admin",
  propietario: "inv.puede.propietario",
} as const;
const NOMBRE_ROL = {
  lector: "rol.lector",
  miembro: "rol.miembro",
  admin: "rol.admin",
  propietario: "rol.propietario",
} as const;

/**
 * Aceptar una invitación (D-127). Si ese email ya tiene cuenta, pide su contraseña y
 * suma la organización; si no, la crea. El email no se elige: es al que se invitó.
 */
function Contenido() {
  const token = useSearchParams().get("token") ?? "";
  const [info, setInfo] = useState<Awaited<ReturnType<typeof getInvitation>> | null>(null);
  const [fallo, setFallo] = useState("");
  const [nombre, setNombre] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    getInvitation(token)
      .then(setInfo)
      .catch((e) => setFallo(e instanceof Error ? e.message : t("inv.no_vale")));
  }, [token]);

  async function aceptar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError("");
    try {
      await acceptInvitation(token, nombre, contrasena);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : t("inv.error"));
      setEnviando(false);
    }
  }

  if (fallo) {
    return (
      <main className="auth">
        <div className="auth-card">
          <h1>{t("inv.no_vale.titulo")}</h1>
          <p className="muted">{t("inv.no_vale.texto", { motivo: fallo })}</p>
        </div>
      </main>
    );
  }
  if (!info) return <Cargando />;

  return (
    <main className="auth">
      <form className="auth-card" onSubmit={aceptar}>
        <h1>{t("inv.unete", { org: info.org_name })}</h1>
        <p className="muted">
          {tr("inv.como", {
            rol: <strong>{t(NOMBRE_ROL[info.role as Rol])}</strong>,
            puede: t(QUE_PUEDE[info.role as Rol]),
          })}
        </p>
        <label>
          <small>{t("org.email")}</small>
          <input className="field" value={info.email} disabled />
        </label>
        {!info.has_account && (
          <label>
            <small>{t("conf.nombre")}</small>
            <input
              className="field"
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              autoComplete="name"
            />
          </label>
        )}
        <label>
          <small>
            {info.has_account
              ? t("inv.con_cuenta")
              : t("inv.elige")}
          </small>
          <input
            className="field"
            type="password"
            value={contrasena}
            onChange={(e) => setContrasena(e.target.value)}
            required
            minLength={info.has_account ? undefined : 10}
            autoComplete={info.has_account ? "current-password" : "new-password"}
            autoFocus
          />
        </label>
        {error && <p className="verr">{error}</p>}
        <button type="submit" className="btn primary" disabled={enviando}>
          {enviando ? t("inv.uniendote") : t("inv.aceptar")}
        </button>
      </form>
    </main>
  );
}

export default function InvitacionPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
