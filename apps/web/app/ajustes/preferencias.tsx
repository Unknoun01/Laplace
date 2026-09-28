"use client";

import { useEffect, useState } from "react";
import { guardarTipo, leerTipo } from "@/lib/moneda";
import { Aviso } from "./aviso";
import { t } from "@/lib/textos";

/**
 * Tema oscuro, claro o el del sistema (D-125, D-155). El oscuro es el de partida: sin
 * nada guardado no se toca `data-theme`. Lo elegido se guarda en este navegador y lo
 * aplica el script del layout antes del primer pintado.
 */
type Tema = "dark" | "light" | "system";

export function Apariencia() {
  const [tema, setTema] = useState<Tema>("dark");
  useEffect(() => {
    try {
      const guardado = window.localStorage.getItem("laplace.theme");
      if (guardado === "light" || guardado === "system") setTema(guardado);
    } catch {
      /* almacenamiento bloqueado: se queda en el oscuro */
    }
  }, []);

  function elegir(valor: Tema) {
    setTema(valor);
    try {
      if (valor === "dark") window.localStorage.removeItem("laplace.theme");
      else window.localStorage.setItem("laplace.theme", valor);
    } catch {
      /* dura lo que la pestaña */
    }
    if (valor === "dark") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = valor;
  }

  return (
    <section className="sec">
      <h3>{t("aj.apariencia")}</h3>
      <p className="lead">{t("aj.en_navegador")}</p>
      <div className="seg" role="group" aria-label={t("aj.tema")}>
        {([
          ["dark", t("aj.tema.oscuro")],
          ["light", t("aj.tema.claro")],
          ["system", t("aj.tema.sistema")],
        ] as const).map(([valor, nombre]) => (
          <button key={valor} type="button" aria-pressed={tema === valor} onClick={() => elegir(valor)}>
            {nombre}
          </button>
        ))}
      </div>
      <AltoContraste />
    </section>
  );
}

/**
 * Alto contraste (D-159), para el tema claro y para el oscuro: fondos opacos, sin
 * cristal ni desenfoque, y letra a 7:1. Se guarda en este navegador y lo aplica el
 * script del layout antes del primer pintado, igual que el tema.
 */
function AltoContraste() {
  const [alto, setAlto] = useState(false);
  useEffect(() => {
    setAlto(document.documentElement.dataset.contrast === "high");
  }, []);

  function cambiar() {
    const nuevo = !alto;
    setAlto(nuevo);
    try {
      if (nuevo) window.localStorage.setItem("laplace.contrast", "high");
      else window.localStorage.removeItem("laplace.contrast");
    } catch {
      /* dura lo que la pestaña */
    }
    if (nuevo) document.documentElement.dataset.contrast = "high";
    else delete document.documentElement.dataset.contrast;
  }

  return (
    <div className="contraste">
      <button type="button" className="btn" aria-pressed={alto} onClick={cambiar}>
        <span aria-hidden className="contraste-icono">◐</span>
        {t("aj.contraste.alto")}
      </button>
      <p className="muted">{t("aj.contraste.texto")}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------------

export function Moneda() {
  const [valor, setValor] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  useEffect(() => {
    const tipo = leerTipo();
    setValor(tipo ? String(tipo).replace(".", ",") : "");
  }, []);

  return (
    <section className="sec">
      <h3>{t("aj.euros")}</h3>
      <p className="lead">{t("aj.euros.lead")}</p>
      <div className="ab">
        <label>
          <small>{t("aj.euros.tipo")}</small>
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
            const tipo = Number(valor.replace(",", "."));
            guardarTipo(tipo > 0 ? tipo : null);
            setMsg({ ok: true, texto: tipo > 0 ? t("aj.euros.guardado") : t("aj.euros.desactivados") })
          }}
        >
          {t("comun.guardar")}
        </button>
        {valor && (
          <button
            type="button"
            className="btn"
            onClick={() => {
              guardarTipo(null);
              setValor("");
              setMsg({ ok: true, texto: t("aj.euros.desactivados") });
            }}
          >
            {t("comun.quitar")}
          </button>
        )}
      </div>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------
