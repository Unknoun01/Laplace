"use client";

import { Fragment, createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { ETIQUETAS, IDIOMAS, NOMBRES, detectar, fijarIdioma, recordar, type Idioma } from "./idioma";
import { t, type Clave } from "./textos";

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
export function ProveedorIdioma({ children }: { children: ReactNode }) {
  const [idioma, setIdioma] = useState<Idioma>("es");

  useEffect(() => {
    const elegido = detectar();
    fijarIdioma(elegido);
    document.documentElement.lang = ETIQUETAS[elegido];
    setIdioma(elegido);
  }, []);

  const cambiar = (nuevo: Idioma) => {
    recordar(nuevo);
    fijarIdioma(nuevo);
    document.documentElement.lang = ETIQUETAS[nuevo];
    setIdioma(nuevo);
  };

  return (
    <Contexto.Provider value={{ idioma, cambiar }}>
      <Fragment key={idioma}>{children}</Fragment>
    </Contexto.Provider>
  );
}

export function useIdioma() {
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
