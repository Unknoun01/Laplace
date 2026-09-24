import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import { Suspense } from "react";
import { TopBar } from "@/components/TopBar";
import "./globals.css";

/**
 * Las fuentes se descargan al construir y se sirven desde el propio Laplace (D-125).
 *
 * Antes iban enlazadas a Google Fonts, así que el modo local —que presume de no llamar a
 * nadie— pedía dos hojas de estilo a Google cada vez que se abría, y sin conexión se
 * pintaba con la del sistema.
 */
// Inter para el texto, Plex Mono para las cifras (D-133).
const sans = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});
const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  // Cada pantalla pone el suyo: con todas las pestañas llamadas «Laplace» no había
  // forma de distinguir la traza de la ficha en la barra del navegador.
  title: { default: "Laplace", template: "%s · Laplace" },
  description: "Observabilidad y optimización de agentes de IA.",
};

/**
 * Restaura el modo y el tema antes del primer pintado.
 *
 * Va como script en línea a propósito: si se aplicaran desde React, un desarrollador
 * con "Avanzado" guardado vería primero la versión simple y luego un salto, y quien
 * haya elegido el tema claro vería un fogonazo oscuro. El salto es peor que el script.
 */
const RESTORE_MODE = `
try {
  var m = localStorage.getItem("laplace.mode");
  document.body.dataset.mode = m === "pro" ? "pro" : "simple";
  var t = localStorage.getItem("laplace.theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {
  document.body.dataset.mode = "simple";
}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `data-theme` lo pone el script antes de hidratar: diverge a propósito.
    <html lang="es" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      {/* El script de abajo cambia `data-mode` antes de que React hidrate, así que
          este atributo diverge a propósito entre servidor y cliente. */}
      <body data-mode="simple" suppressHydrationWarning>
        <script dangerouslySetInnerHTML={{ __html: RESTORE_MODE }} />
        <div className="shell wide">
          <Suspense fallback={<div className="topbar" />}>
            <TopBar />
          </Suspense>
          {children}
        </div>
      </body>
    </html>
  );
}
