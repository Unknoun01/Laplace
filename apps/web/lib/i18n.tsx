"use client";

import { usePathname } from "next/navigation";
import { Fragment, createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { ETIQUETAS, IDIOMAS, NOMBRES, detectar, fijarIdioma, idiomaActual, recordar, type Idioma } from "./idioma";
import { t, type Clave, type ClavePlural } from "./textos";

/** La sección de la URL y la clave de su título. */
const TITULOS: Record<string, Clave> = {
  ajustes: "titulo.ajustes",
  clientes: "titulo.clientes",
  configurar: "titulo.configurar",
  entrar: "titulo.entrar",
  evaluaciones: "titulo.evaluaciones",
  invitacion: "titulo.invitacion",
  organizacion: "titulo.organizacion",
  panel: "titulo.panel",
  problema: "titulo.problema",
  prompts: "titulo.prompts",
  traza: "titulo.traza",
  trazas: "titulo.trazas",
};

const Contexto = createContext<{ idioma: Idioma; cambiar: (idioma: Idioma) => void }>({
  idioma: "es",
  cambiar: () => {},
});

/**
 * Pone el idioma a toda la interfaz (D-147).
 *
 * El HTML estático sale en español; al montar se decide el idioma de verdad y, si es
 * otro, **se vuelve a montar todo** (`key`). No es un atajo: las frases del motor llegan
 * del backend ya redactadas, así que cambiar de idioma obliga a pedirlas otra vez, y
 * volver a montar es la forma de que ninguna pantalla se quede con cifras o frases del
 * idioma anterior.
 */
/**
 * Los textos que pinta el CSS con `content:` —el «¿por qué?» de un aviso plegado, la
 * marca «avanzado»— no pasan por React, y estaban escritos en español en la hoja de
 * estilos: salían en español en los cinco idiomas. Van como variables en `<html>`.
 */
function textosDelCss(): void {
  const raiz = document.documentElement.style;
  raiz.setProperty("--txt-porque", JSON.stringify(` ${t("css.porque")}`));
  raiz.setProperty("--txt-avanzado", JSON.stringify(t("css.avanzado")));
}

export function ProveedorIdioma({ children }: { children: ReactNode }) {
  const [idioma, setIdioma] = useState<Idioma>("es");

  useEffect(() => {
    const elegido = detectar();
    fijarIdioma(elegido);
    document.documentElement.lang = ETIQUETAS[elegido];
    textosDelCss();
    setIdioma(elegido);
  }, []);

  // El título de la pestaña, en el idioma de la pantalla. Lo pinta React (un `<title>`
  // que sube al `<head>`) y no los metadatos de Next, que salen fijos en el HTML
  // estático y además se vuelven a aplicar después de montar. Los títulos concretos
  // —un problema, una traza— los pone `useTitulo` cuando llegan los datos.
  const ruta = usePathname();
  const seccion = TITULOS[(ruta ?? "/").split("/")[1] ?? ""];
  const titulo = seccion ? `${t(seccion)} · Laplace` : "Laplace";

  const cambiar = (nuevo: Idioma) => {
    recordar(nuevo);
    fijarIdioma(nuevo);
    document.documentElement.lang = ETIQUETAS[nuevo];
    textosDelCss();
    setIdioma(nuevo);
  };

  return (
    <Contexto.Provider value={{ idioma, cambiar }}>
      <title>{titulo}</title>
      <Fragment key={idioma}>{children}</Fragment>
    </Contexto.Provider>
  );
}

function useIdioma() {
  return useContext(Contexto);
}

/**
 * Un texto con elementos dentro: `tr("estado.demo.terminal", { comando: <code>…</code> })`.
 * Los huecos que no vengan en `piezas` se quedan como están, que es más visible que
 * dejarlos en blanco.
 */
export function tr(clave: Clave, piezas: Record<string, ReactNode>): ReactNode {
  const texto = t(clave);
  const trozos = texto.split(/\{(\w+)\}/g);
  return trozos.map((trozo, i) =>
    i % 2 === 0 ? trozo : <Fragment key={i}>{trozo in piezas ? piezas[trozo] : `{${trozo}}`}</Fragment>,
  );
}

/** `tr` con singular o plural según `n`, como `tn` (D-156). */
export function trn(base: ClavePlural, n: number, piezas: Record<string, ReactNode>): ReactNode {
  const forma = new Intl.PluralRules(ETIQUETAS[idiomaActual()]).select(n);
  const clave = (forma === "one" ? `${base}_one` : `${base}_other`) as Clave;
  return tr(clave, piezas);
}

/** El selector de idioma: cada uno por su nombre en su idioma. */
export function SelectorIdioma() {
  const { idioma, cambiar } = useIdioma();
  return (
    <select
      className="ctx idioma"
      aria-label={t("barra.idioma")}
      value={idioma}
      onChange={(event) => cambiar(event.target.value as Idioma)}
    >
      {IDIOMAS.map((i) => (
        <option key={i} value={i} lang={ETIQUETAS[i]}>
          {NOMBRES[i]}
        </option>
      ))}
    </select>
  );
}
