"use client";

import { useState } from "react";
import { deleteCustomPrice, getCustomPrices, setCustomPrice } from "@/lib/api";
import { money } from "@/lib/format";
import type { CustomPrices } from "@/lib/types";
import { Aviso } from "./aviso";

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
        `Tarifa de ${model} guardada. ${r.repriced_spans} llamadas ya guardadas tienen ahora su coste.`,
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido guardar" });
    }
  }

  async function quitar(model: string) {
    try {
      await deleteCustomPrice(model);
      await recargar(`Tarifa de ${model} quitada.`);
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido quitar" });
    }
  }

  const propias = Object.entries(precios.models);
  return (
    <section className="sec">
      <h3>Tarifas propias</h3>
      <p className="lead">
        Para un modelo que no está en nuestra tabla, o uno con precio negociado. En dólares
        por millón de tokens, de la página del proveedor. Al guardarla se recalcula también
        lo que ya habías enviado. Si el modelo corre en tu máquina, no le pongas precio: no
        te cobra nadie.
      </p>
      {!precios.editable && (
        <p className="muted">
          Las tarifas valen para toda la instalación: sólo las puede cambiar una clave de
          instalación.
        </p>
      )}

      {precios.unpriced.length > 0 && (
        <p>
          Sin tarifa ahora mismo:{" "}
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
              <th>Modelo</th>
              <th>Entrada</th>
              <th>Salida</th>
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
                      Quitar
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
            <small>Modelo</small>
            <input
              className="field"
              value={nuevo.model}
              onChange={(e) => setNuevo({ ...nuevo, model: e.target.value })}
              placeholder="mi-modelo"
            />
          </label>
          <label>
            <small>Entrada ($/M)</small>
            <input
              className="field"
              inputMode="decimal"
              value={nuevo.input}
              onChange={(e) => setNuevo({ ...nuevo, input: e.target.value })}
              style={{ width: 110 }}
            />
          </label>
          <label>
            <small>Salida ($/M)</small>
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
            Guardar tarifa
          </button>
        </div>
      )}
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------
