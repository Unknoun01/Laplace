"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Cargando } from "@/components/states";
import { verifyEmail } from "@/lib/api";
import { t } from "@/lib/textos";

/**
 * El enlace del correo de verificación (D-182). Se gasta al abrirlo, con o sin sesión:
 * lo que prueba es que se ha leído ese buzón, no quién está en el navegador.
 */
function Contenido() {
  const token = useSearchParams().get("token") ?? "";
  const [estado, setEstado] = useState<"pidiendo" | "ok" | "mal">("pidiendo");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setEstado("mal");
      return;
    }
    verifyEmail(token)
      .then(() => setEstado("ok"))
      .catch((e: Error) => {
        setError(e.message);
        setEstado("mal");
      });
  }, [token]);

  if (estado === "pidiendo") return <Cargando />;
  return (
    <main className="auth">
      <div className="auth-card">
        <h1>{estado === "ok" ? t("verif.ok") : t("verif.mal")}</h1>
        {estado === "mal" && <p className="verr">{error || t("verif.sin_token")}</p>}
        <Link className="btn primary" href="/">
          {t("verif.seguir")}
        </Link>
      </div>
    </main>
  );
}

export default function VerificarPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
