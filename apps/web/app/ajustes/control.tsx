"use client";

import { useEffect, useState } from "react";
import { getControl, setControl } from "@/lib/api";
import { dayHour } from "@/lib/format";
import { Euros } from "@/lib/moneda";
import type { ControlRules } from "@/lib/types";
import { t } from "@/lib/textos";
import { Aviso } from "./aviso";

/**
 * Tope y parada (D-187). Lo que se pone aquí lo pide el SDK cada 30 s y lo aplica como
 * `laplace.guard`: se suma a lo del código y manda el más estricto. Parar corta todas las
 * llamadas al modelo y todos los pasos del proyecto hasta que se reanude; las ejecuciones
 * que estaban en marcha no siguen.
 */
export function Control({ project }: { project: string }) {
  const [reglas, setReglas] = useState<ControlRules | null>(null);
  const [tope, setTope] = useState("");
  const [bucles, setBucles] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  useEffect(() => {
    getControl(project)
      .then((r) => {
        setReglas(r);
        setTope(r.max_usd_per_run === null ? "" : String(r.max_usd_per_run));
        setBucles(r.max_loop === null ? "" : String(r.max_loop));
      })
      .catch(() => setReglas(null));
  }, [project]);

  async function guardar(cambios: Partial<{ max_usd_per_run: number | null; max_loop: number | null; stopped: boolean }>, texto: string) {
    if (!reglas) return;
    try {
      const nuevas = await setControl(project, {
        max_usd_per_run: reglas.max_usd_per_run,
        max_loop: reglas.max_loop,
        stopped: reglas.stopped,
        ...cambios,
      });
      setReglas(nuevas);
      setTope(nuevas.max_usd_per_run === null ? "" : String(nuevas.max_usd_per_run));
      setBucles(nuevas.max_loop === null ? "" : String(nuevas.max_loop));
      setMsg({ ok: true, texto });
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("seg.error.guardar") });
    }
  }

  function guardarLimites() {
    const usd = Number(tope.replace(",", "."));
    const vueltas = Number(bucles);
    if (tope && !(usd > 0)) return setMsg({ ok: false, texto: t("aj.control.tope_invalido") });
    if (bucles && !(Number.isInteger(vueltas) && vueltas >= 2))
      return setMsg({ ok: false, texto: t("aj.control.bucles_invalido") });
    guardar(
      { max_usd_per_run: tope ? usd : null, max_loop: bucles ? vueltas : null },
      t("aj.control.guardado"),
    );
  }

  if (!reglas) return null;
  return (
    <section className="sec control">
      <h3>{t("aj.control.titulo")}</h3>
      <p className="lead">{t("aj.control.lead")}</p>

      <div className={reglas.stopped ? "parada parado" : "parada"}>
        <p>
          {reglas.stopped
            ? t("aj.control.parado", { cuando: reglas.stopped_at ? dayHour(reglas.stopped_at) : "" })
            : t("aj.control.en_marcha")}
        </p>
        {reglas.stopped ? (
          <button type="button" className="btn" onClick={() => guardar({ stopped: false }, t("aj.control.reanudado"))}>
            {t("aj.control.reanudar")}
          </button>
        ) : (
          <button type="button" className="btn danger" onClick={() => guardar({ stopped: true }, t("aj.control.parado_ok"))}>
            {t("aj.control.parar")}
          </button>
        )}
      </div>

      <div className="ab">
        <label>
          <small>{t("aj.control.tope")}</small>
          <input
            className="field"
            inputMode="decimal"
            value={tope}
            onChange={(e) => setTope(e.target.value)}
            placeholder="0.50"
            style={{ width: 120 }}
          />
        </label>
        {tope && <Euros usd={Number(tope.replace(",", ".")) || 0} />}
        <label>
          <small>{t("aj.control.bucles")}</small>
          <input
            className="field"
            inputMode="numeric"
            value={bucles}
            onChange={(e) => setBucles(e.target.value)}
            placeholder="5"
            style={{ width: 80 }}
          />
        </label>
        <button type="button" className="btn" onClick={guardarLimites}>
          {t("comun.guardar")}
        </button>
      </div>
      <p className="muted">{t("aj.control.como")}</p>
      <Aviso {...msg} />
    </section>
  );
}
