"use client";

import { useEffect, useState } from "react";
import { getGraph } from "@/lib/api";
import { number } from "@/lib/format";
import { t, tn } from "@/lib/textos";
import type { ProjectGraph } from "@/lib/types";
import { type CajaDibujo, DibujoGrafo, type FlechaDibujo, costeDe, tipoConocido } from "./GrafoAgente";

/**
 * El grafo del proyecto (D-188): el mismo dibujo que el de una traza (D-153), pero con
 * todas las ejecuciones de la ventana sumadas. Sale del backend, no del árbol.
 *
 * Columnas por la menor distancia desde una entrada del agente (un paso que es raíz de
 * alguna traza, o al que no llega ninguna flecha de las que se dibujan). Cada flecha
 * dice cuántas veces se recorrió y lo que costaron esas llamadas; las que van a una
 * herramienta o a un subpaso no llevan dinero, porque el de debajo está en sus propias
 * flechas y sumarlo aquí lo contaría dos veces.
 */
export function GrafoProyecto({ project, days }: { project: string; days: number }) {
  const [grafo, setGrafo] = useState<ProjectGraph | null>(null);

  useEffect(() => {
    let vigente = true;
    setGrafo(null);
    getGraph(project, days)
      .then((g) => vigente && setGrafo(g))
      .catch(() => vigente && setGrafo(null));
    return () => {
      vigente = false;
    };
  }, [project, days]);

  if (!grafo || grafo.nodes.length < 2) return null;

  const columna = columnas(grafo);
  const porColumna = new Map<number, number>();
  const cajas: CajaDibujo[] = grafo.nodes.map((n) => {
    const col = columna.get(n.id) ?? 0;
    const fila = porColumna.get(col) ?? 0;
    porColumna.set(col, fila + 1);
    const coste = costeDe(n.cost_usd, n.unknown_cost_spans, n.estimated_spans);
    // En una llamada al modelo, lo que se lee es qué modelo; en lo demás, qué es.
    const que =
      n.type === "llm" && n.models.length > 0
        ? n.models.length > 1
          ? `${n.models[0]} +${n.models.length - 1}`
          : n.models[0]
        : t(`grafo.tipo.${tipoConocido(n.type)}`);
    return {
      id: n.id,
      nombre: n.label,
      tipo: n.type,
      errores: n.errors,
      columna: col,
      fila,
      meta: `${que} · ×${number(n.calls)}` + (coste ? ` · ${coste}` : ""),
      titulo:
        `${n.label} · ${tn("grafo.llamadas", n.calls, { n: number(n.calls) })}` +
        (n.models.length > 0 ? ` · ${n.models.join(", ")}` : "") +
        (coste ? ` · ${coste}` : "") +
        (n.errors > 0 ? ` · ${tn("grafo.errores", n.errors, { n: number(n.errors) })}` : ""),
    };
  });
  const flechas: FlechaDibujo[] = grafo.edges.map((a) => {
    const coste = costeDe(a.cost_usd, a.unknown_cost_spans);
    return {
      de: a.source,
      a: a.target,
      texto: `×${number(a.calls)}` + (coste ? ` · ${coste}` : ""),
      destacada: false,
    };
  });

  return (
    <section className="grafo-agente grafo-proyecto">
      <h2>{t("grafo.proyecto.titulo")}</h2>
      <p className="lead">
        {tn("grafo.proyecto.lead", grafo.traces, { n: number(grafo.traces), dias: grafo.days })}
      </p>
      <DibujoGrafo
        cajas={cajas}
        flechas={flechas}
        resaltados={new Set()}
        aria={t("grafo.aria", { pasos: grafo.nodes.length })}
        hueco={132}
      />
      {grafo.hidden_nodes > 0 && (
        <p className="muted">
          {t("grafo.proyecto.fuera", {
            pasos: number(grafo.hidden_nodes),
            llamadas: number(grafo.hidden_calls),
          })}
        </p>
      )}
    </section>
  );
}

/** La menor distancia de cada paso a una entrada del agente, recorriendo las flechas. */
function columnas(grafo: ProjectGraph): Map<string, number> {
  const llegan = new Set(grafo.edges.map((a) => a.target));
  const salen = new Map<string, string[]>();
  for (const a of grafo.edges) salen.set(a.source, [...(salen.get(a.source) ?? []), a.target]);

  const columna = new Map<string, number>();
  const cola: string[] = [];
  for (const n of grafo.nodes) {
    if (n.roots > 0 || !llegan.has(n.id)) {
      columna.set(n.id, 0);
      cola.push(n.id);
    }
  }
  while (cola.length > 0) {
    const id = cola.shift()!;
    for (const hijo of salen.get(id) ?? []) {
      if (!columna.has(hijo)) {
        columna.set(hijo, columna.get(id)! + 1);
        cola.push(hijo);
      }
    }
  }
  return columna;
}
