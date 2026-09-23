"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { type Rol, acceptInvitation, getInvitation } from "@/lib/api";

const QUE_PUEDE: Record<Rol, string> = {
  lector: "ver los datos de sus proyectos",
  miembro: "ver, anotar ejecuciones, marcar problemas y gestionar prompts",
  admin: "todo lo anterior, más miembros, claves, alertas y presupuesto",
  propietario: "todo, incluido hacer y deshacer propietarios",
};

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
      .catch((e) => setFallo(e instanceof Error ? e.message : "esa invitación no vale"));
  }, [token]);

  async function aceptar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError("");
    try {
      await acceptInvitation(token, nombre, contrasena);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se ha podido aceptar");
      setEnviando(false);
    }
  }

  if (fallo) {
    return (
      <main className="auth">
        <div className="auth-card">
          <h1>Esta invitación no vale</h1>
          <p className="muted">
            {fallo}. Las invitaciones caducan a los siete días y sólo sirven una vez: pide
            otra a quien te invitó.
          </p>
        </div>
      </main>
    );
  }
  if (!info) return <Cargando />;

  return (
    <main className="auth">
      <form className="auth-card" onSubmit={aceptar}>
        <h1>Únete a «{info.org_name}»</h1>
        <p className="muted">
          Como <strong>{info.role}</strong>: podrás {QUE_PUEDE[info.role]}.
        </p>
        <label>
          <small>Email</small>
          <input className="field" value={info.email} disabled />
        </label>
        {!info.has_account && (
          <label>
            <small>Tu nombre</small>
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
              ? "Ya tienes cuenta con este email: tu contraseña"
              : "Elige una contraseña (10 caracteres o más)"}
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
          {enviando ? "Uniéndote…" : "Aceptar la invitación"}
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
