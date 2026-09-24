"use client";

import { useEffect, useState } from "react";
import { guardarTipo, leerTipo } from "@/lib/moneda";
import { Aviso } from "./aviso";

/**
 * Tema claro u oscuro (D-125). Por defecto sigue al sistema; lo elegido aquí se guarda
 * en este navegador y lo aplica el script del layout antes del primer pintado.
 */
export function Apariencia() {
  const [tema, setTema] = useState<"sistema" | "light" | "dark">("sistema");
  useEffect(() => {
    try {
      const t = window.localStorage.getItem("laplace.theme");
      if (t === "light" || t === "dark") setTema(t);
    } catch {
      /* almacenamiento bloqueado: se queda en el del sistema */
    }
  }, []);

  function elegir(valor: "sistema" | "light" | "dark") {
    setTema(valor);
    try {
      if (valor === "sistema") window.localStorage.removeItem("laplace.theme");
      else window.localStorage.setItem("laplace.theme", valor);
    } catch {
      /* dura lo que la pestaña */
    }
    if (valor === "sistema") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = valor;
  }

  return (
    <section className="sec">
      <h3>Apariencia</h3>
      <p className="lead">Se guarda en este navegador.</p>
      <div className="seg" role="group" aria-label="Tema">
        {([
          ["sistema", "Como el sistema"],
          ["light", "Claro"],
          ["dark", "Oscuro"],
        ] as const).map(([valor, nombre]) => (
          <button key={valor} type="button" aria-pressed={tema === valor} onClick={() => elegir(valor)}>
            {nombre}
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------

export function Moneda() {
  const [valor, setValor] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  useEffect(() => {
    const t = leerTipo();
    setValor(t ? String(t).replace(".", ",") : "");
  }, []);

  return (
    <section className="sec">
      <h3>Ver también en euros</h3>
      <p className="lead">
        Los proveedores facturan en dólares y ésa sigue siendo la cifra. Si pones un tipo
        de cambio, al lado de los importes principales aparece el equivalente en euros con
        «≈». El tipo lo pones tú y se guarda sólo en este navegador: no lo descargamos de
        ninguna parte.
      </p>
      <div className="ab">
        <label>
          <small>1 $ son … €</small>
          <input
            className="field"
            inputMode="decimal"
            value={valor}
            onChange={(e) => setValor(e.target.value)}
            placeholder="0,92"
            style={{ width: 110 }}
          />
        </label>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const t = Number(valor.replace(",", "."));
            guardarTipo(t > 0 ? t : null);
            setMsg({ ok: true, texto: t > 0 ? "Tipo guardado." : "Euros desactivados." })
          }}
        >
          Guardar
        </button>
        {valor && (
          <button
            type="button"
            className="btn"
            onClick={() => {
              guardarTipo(null);
              setValor("");
              setMsg({ ok: true, texto: "Euros desactivados." });
            }}
          >
            Quitar
          </button>
        )}
      </div>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------
