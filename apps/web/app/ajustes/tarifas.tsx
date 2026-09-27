"use client";

import { useState } from "react";
import { deleteCustomPrice, getCustomPrices, setCustomPrice } from "@/lib/api";
import { money } from "@/lib/format";
import type { CustomPrices } from "@/lib/types";
import { Aviso } from "./aviso";
import { t } from "@/lib/textos";

export function Tarifas({ inicial }: { inicial: CustomPrices }) {
  const [precios, setPrecios] = useState(inicial);
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  const [nuevo, setNuevo] = useState({ model: "", input: "", output: "" });

  async function recargar(texto: string) {
    setPrecios(await getCustomPrices());
    setMsg({ ok: true, texto });
  }

  async function guardar(model: string, input: string, output: string) {
    try {
      const r = await setCustomPrice({
        model,
        input: Number(input.replace(",", ".")),
        output: Number(output.replace(",", ".")),
      });
      setNuevo({ model: "", input: "", output: "" });
      await recargar(
        t("aj.tar.guardada", { modelo: model, n: r.repriced_spans }),
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("seg.error.guardar") });
    }
  }

  async function quitar(model: string) {
    try {
      await deleteCustomPrice(model);
      await recargar(t("aj.tar.quitada", { modelo: model }));
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("aj.error.quitar") });
    }
  }

  const propias = Object.entries(precios.models);
  return (
    <section className="sec">
      <h3>{t("aj.tar.titulo")}</h3>
      <p className="lead">{t("aj.tar.lead")}</p>
      {!precios.editable && (
        <p className="muted">{t("aj.tar.instalacion")}</p>
      )}

      {precios.unpriced.length > 0 && (
        <p>
          {t("aj.tar.sin_tarifa")}{" "}
          {precios.unpriced.map((m) => (
            <button
              key={m}
              type="button"
              className="chip where as-btn"
              onClick={() => setNuevo({ ...nuevo, model: m })}
              disabled={!precios.editable}
            >
              {m}
            </button>
          ))}
        </p>
      )}

      {propias.length > 0 && (
        <table className="tabla-simple">
          <thead>
            <tr>
              <th>{t("aj.tar.modelo")}</th>
              <th>{t("aj.tar.entrada")}</th>
              <th>{t("aj.tar.salida")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {propias.map(([m, p]) => (
              <tr key={m}>
                <td className="num">{m}</td>
                <td className="num">{money(p.input)}</td>
                <td className="num">{money(p.output)}</td>
                <td>
                  {precios.editable && (
                    <button type="button" className="btn small" onClick={() => quitar(m)}>
                      {t("comun.quitar")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {precios.editable && (
        <div className="ab">
          <label className="grow">
            <small>{t("aj.tar.modelo")}</small>
            <input
              className="field"
              value={nuevo.model}
              onChange={(e) => setNuevo({ ...nuevo, model: e.target.value })}
              placeholder="mi-modelo"
            />
          </label>
          <label>
            <small>{t("aj.tar.entrada_m")}</small>
            <input
              className="field"
              inputMode="decimal"
              value={nuevo.input}
              onChange={(e) => setNuevo({ ...nuevo, input: e.target.value })}
              style={{ width: 110 }}
            />
          </label>
          <label>
            <small>{t("aj.tar.salida_m")}</small>
            <input
              className="field"
              inputMode="decimal"
              value={nuevo.output}
              onChange={(e) => setNuevo({ ...nuevo, output: e.target.value })}
              style={{ width: 110 }}
            />
          </label>
          <button
            type="button"
            className="btn"
            disabled={!nuevo.model.trim() || !nuevo.input || !nuevo.output}
            onClick={() => guardar(nuevo.model.trim(), nuevo.input, nuevo.output)}
          >
            {t("aj.tar.guardar")}
          </button>
        </div>
      )}
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------
