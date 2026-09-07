import type { Metadata } from "next";
import { Suspense } from "react";
import { TopBar } from "@/components/TopBar";
import { listProjects } from "@/lib/api";
import type { ProjectStats } from "@/lib/types";
import "./globals.css";

export const metadata: Metadata = {
  title: "Laplace",
  description: "Observabilidad y optimización de agentes de IA.",
};

/**
 * Restaura el modo antes del primer pintado.
 *
 * Va como script en línea a propósito: si el modo se aplicara desde React, un
 * desarrollador con "Avanzado" guardado vería primero la versión simple y luego un
 * salto. El salto es peor que el script.
 */
const RESTORE_MODE = `
try {
  var m = localStorage.getItem("laplace.mode");
  document.body.dataset.mode = m === "pro" ? "pro" : "simple";
} catch (e) {
  document.body.dataset.mode = "simple";
}`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Si el backend no responde, la barra se pinta vacía y cada pantalla explica el
  // problema: el armazón nunca debe caerse por eso.
  let projects: ProjectStats[] = [];
  try {
    projects = await listProjects();
  } catch {
    projects = [];
  }

  return (
    <html lang="es">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      {/* El script de abajo cambia `data-mode` antes de que React hidrate, así que
          este atributo diverge a propósito entre servidor y cliente. */}
      <body data-mode="simple" suppressHydrationWarning>
        <script dangerouslySetInnerHTML={{ __html: RESTORE_MODE }} />
        <div className="shell wide">
          <Suspense fallback={<div className="topbar" />}>
            <TopBar projects={projects} />
          </Suspense>
          {children}
        </div>
      </body>
    </html>
  );
}
