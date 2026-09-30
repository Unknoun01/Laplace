import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import { Suspense } from "react";
import { TopBar } from "@/components/TopBar";
import { ProveedorIdioma } from "@/lib/i18n";
// La hoja de estilos, partida por pantallas (D-174). El orden es la cascada: se
// importan en el orden en que estaban en el `globals.css` de antes, que pasaba de 4.100
// líneas. Los temas y los tokens van primero; lo de cada pantalla, después.
import "./estilos/00-temas.css";
import "./estilos/01-base.css";
import "./estilos/02-barra.css";
import "./estilos/03-heroe.css";
import "./estilos/04-secciones.css";
import "./estilos/05-ficha.css";
import "./estilos/06-arbol.css";
import "./estilos/07-controles.css";
import "./estilos/08-trazas.css";
import "./estilos/09-estados.css";
import "./estilos/10-responsive.css";
import "./estilos/11-panel.css";
import "./estilos/12-en-vivo.css";
import "./estilos/13-evaluaciones.css";
import "./estilos/14-prompts.css";
import "./estilos/15-cobertura.css";
import "./estilos/16-ajustes.css";
import "./estilos/17-estados-de-hallazgos.css";
import "./estilos/18-avisos.css";
import "./estilos/19-lista-del-inicio.css";
import "./estilos/20-pasos-de-evaluaciones.css";
import "./estilos/21-cuentas.css";
import "./estilos/22-diagnostico.css";
import "./estilos/23-cristal.css";
import "./estilos/24-pantallas-anchas.css";
import "./estilos/25-menos-texto.css";
import "./estilos/26-graficos.css";
import "./estilos/27-grafo.css";
import "./estilos/28-ciclo.css";

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
  // El título lo pone `ProveedorIdioma`, en el idioma de la pantalla (D-147).
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
  // Sin nada guardado no se toca: el oscuro es el de partida (D-155).
  if (t === "light" || t === "dark" || t === "system") document.documentElement.dataset.theme = t;
  // Alto contraste, encima del tema que sea (D-159).
  if (localStorage.getItem("laplace.contrast") === "high") document.documentElement.dataset.contrast = "high";
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
        <ProveedorIdioma>
          <div className="shell wide">
            <Suspense fallback={<div className="topbar" />}>
              <TopBar />
            </Suspense>
            {children}
          </div>
        </ProveedorIdioma>
      </body>
    </html>
  );
}
