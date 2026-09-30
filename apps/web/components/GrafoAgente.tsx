"use client";

import { useMemo } from "react";
import { money } from "@/lib/format";
import { t, tn } from "@/lib/textos";
import type { Trace, TraceTreeNode } from "@/lib/types";

/**
 * El grafo del agente, sacado de una traza (D-153).
 *
 * El árbol dice qué pasó llamada a llamada; el grafo dice **cómo está hecho el agente**:
 * un nodo por paso —con la misma identidad que el resto del producto, `step_key`, o el
 * tipo y el nombre si no la hay— y una flecha por cada «este paso llama a este otro»,
 * con cuántas veces. Un bucle deja de ser treinta filas en el árbol y pasa a ser una
 * flecha «×6» entre dos cajas. Es la semilla del plano de control de la hoja de ruta,
 * y por eso no inventa nada: sólo lo que esta ejecución hizo.
 *
 * Disposición por capas: la columna de un paso es la menor profundidad a la que aparece,
 * y dentro de la columna, el orden en que apareció por primera vez. Una llamada hacia
 * atrás (un paso que vuelve a llamar a uno anterior) se dibuja curvada por debajo.
 */

interface Nodo {
  id: string;
  nombre: string;
  tipo: string;
  llamadas: number;
  coste: number;
  /** Llamadas sin tarifa: el coste del paso es entonces un suelo, no un total. */
  sinTarifa: number;
  errores: number;
  columna: number;
  fila: number;
}

interface Arista {
  de: string;
  a: string;
  veces: number;
}

const ANCHO = 176;
const ALTO = 52;
const HUECO_X = 64;
const HUECO_Y = 18;
const MARGEN = 12;

function identidad(node: TraceTreeNode): string {
  return node.span.step_key || `${node.span.type}:${node.span.name}`;
}

function construirGrafo(trace: Trace): { nodos: Nodo[]; aristas: Arista[] } {
  const nodos = new Map<string, Nodo>();
  const aristas = new Map<string, Arista>();
  const orden: string[] = [];

  const visitar = (node: TraceTreeNode, profundidad: number, padre: string | null) => {
    const id = identidad(node);
    let nodo = nodos.get(id);
    if (!nodo) {
      nodo = {
        id,
        nombre: node.span.step_label || node.span.name,
        tipo: node.span.type,
        llamadas: 0,
        coste: 0,
        sinTarifa: 0,
        errores: 0,
        columna: profundidad,
        fila: 0,
      };
      nodos.set(id, nodo);
      orden.push(id);
    }
    nodo.llamadas += 1;
    const coste = node.span.llm?.cost;
    if (coste?.unknown) nodo.sinTarifa += 1;
    else nodo.coste += coste?.total_usd ?? 0;
    if (node.span.status === "error") nodo.errores += 1;
    nodo.columna = Math.min(nodo.columna, profundidad);
    if (padre !== null && padre !== id) {
      const clave = `${padre}→${id}`;
      const arista = aristas.get(clave) ?? { de: padre, a: id, veces: 0 };
      arista.veces += 1;
      aristas.set(clave, arista);
    }
    for (const hijo of node.children) visitar(hijo, profundidad + 1, id);
  };
  for (const raiz of trace.roots) visitar(raiz, 0, null);

  // Filas: dentro de cada columna, por orden de primera aparición.
  const porColumna = new Map<number, number>();
  for (const id of orden) {
    const nodo = nodos.get(id)!;
    const fila = porColumna.get(nodo.columna) ?? 0;
    nodo.fila = fila;
    porColumna.set(nodo.columna, fila + 1);
  }
  return { nodos: orden.map((id) => nodos.get(id)!), aristas: [...aristas.values()] };
}

/** Un modelo sin tarifa no cuesta cero: se dice, o la cifra va como suelo. */
function costeDe(n: Nodo): string {
  if (n.sinTarifa > 0) {
    return n.coste > 0 ? t("grafo.al_menos", { coste: money(n.coste) }) : t("grafo.sin_tarifa");
  }
  return n.coste > 0 ? money(n.coste) : "";
}

/**
 * La segunda línea de la caja: tipo, llamadas y coste. Un coste largo («≥ 0,003072 US$»)
 * se salía por el borde; cuando no cabe, SVG la estrecha hasta el ancho de la caja
 * (`textLength`). El texto entero sigue en el `title` de la caja.
 */
function Meta({ texto }: { texto: string }) {
  const cabe = texto.length <= META_MAX;
  return (
    <text
      x={12}
      y={39}
      className="meta"
      textLength={cabe ? undefined : ANCHO - 24}
      lengthAdjust={cabe ? undefined : "spacingAndGlyphs"}
    >
      {texto}
    </text>
  );
}

/** Caracteres de la segunda línea que caben sin estrecharla, con la letra de `.meta`. */
const META_MAX = 24;

function recortar(texto: string, max = 22): string {
  return texto.length > max ? `${texto.slice(0, max - 1)}…` : texto;
}

export function GrafoAgente({
  trace,
  resaltados,
}: {
  trace: Trace;
  /** Pasos con un problema del Diagnóstico en esta traza: se marcan en ámbar. */
  resaltados: Set<string>;
}) {
  const { nodos, aristas } = useMemo(() => construirGrafo(trace), [trace]);
  // Una traza de un solo paso no tiene forma que enseñar.
  if (nodos.length < 2) return null;

  const columnas = Math.max(...nodos.map((n) => n.columna)) + 1;
  const filas = Math.max(...nodos.map((n) => n.fila)) + 1;
  const ancho = MARGEN * 2 + columnas * ANCHO + (columnas - 1) * HUECO_X;
  const alto = MARGEN * 2 + filas * ALTO + (filas - 1) * HUECO_Y + 28;
  const pos = new Map(
    nodos.map((n) => [
      n.id,
      { x: MARGEN + n.columna * (ANCHO + HUECO_X), y: MARGEN + n.fila * (ALTO + HUECO_Y) },
    ]),
  );

  return (
    <section className="grafo-agente">
      <h2>{t("grafo.titulo")}</h2>
      <p className="lead">{t("grafo.lead")}</p>
      <div className="grafo-marco">
        <svg
          width={ancho}
          height={alto}
          viewBox={`0 0 ${ancho} ${alto}`}
          role="img"
          aria-label={t("grafo.aria", { pasos: nodos.length })}
        >
          <defs>
            <marker id="punta" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0 L8 4 L0 8 z" className="punta" />
            </marker>
          </defs>
          {aristas.map((a) => {
            const de = pos.get(a.de)!;
            const aq = pos.get(a.a)!;
            const adelante = aq.x > de.x;
            const x1 = adelante ? de.x + ANCHO : de.x + ANCHO / 2;
            const y1 = adelante ? de.y + ALTO / 2 : de.y + ALTO;
            const x2 = adelante ? aq.x : aq.x + ANCHO / 2;
            const y2 = adelante ? aq.y + ALTO / 2 : aq.y + ALTO;
            const d = adelante
              ? `M${x1} ${y1} C ${x1 + HUECO_X / 2} ${y1}, ${x2 - HUECO_X / 2} ${y2}, ${x2} ${y2}`
              : `M${x1} ${y1} C ${x1} ${y1 + 30}, ${x2} ${y2 + 30}, ${x2} ${y2 + 2}`;
            const mx = adelante ? (x1 + x2) / 2 : (x1 + x2) / 2;
            const my = adelante ? (y1 + y2) / 2 - 6 : Math.max(y1, y2) + 24;
            return (
              <g key={`${a.de}→${a.a}`} className={a.veces > 1 ? "arista varias" : "arista"}>
                <path d={d} markerEnd="url(#punta)" />
                {a.veces > 1 && (
                  <text x={mx} y={my} textAnchor="middle">
                    ×{a.veces}
                  </text>
                )}
              </g>
            );
          })}
          {nodos.map((n) => {
            const p = pos.get(n.id)!;
            const clase = [
              "nodo",
              n.tipo,
              resaltados.has(n.id) ? "problema" : "",
              n.errores > 0 ? "error" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <g key={n.id} className={clase} transform={`translate(${p.x} ${p.y})`}>
                <title>
                  {`${n.nombre} · ${tn("grafo.llamadas", n.llamadas, { n: n.llamadas })}` +
                    (costeDe(n) ? ` · ${costeDe(n)}` : "") +
                    (n.errores > 0 ? ` · ${tn("grafo.errores", n.errores, { n: n.errores })}` : "")}
                </title>
                <rect width={ANCHO} height={ALTO} rx={8} />
                <text x={12} y={21} className="nombre">
                  {recortar(n.nombre)}
                </text>
                <Meta
                  texto={
                    `${t(`grafo.tipo.${tipoConocido(n.tipo)}`)} · ×${n.llamadas}` +
                    (costeDe(n) ? ` · ${costeDe(n)}` : "")
                  }
                />
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}

const TIPOS = ["agent", "llm", "tool", "retrieval", "chain"] as const;
type TipoConocido = (typeof TIPOS)[number] | "otro";

function tipoConocido(tipo: string): TipoConocido {
  return (TIPOS as readonly string[]).includes(tipo) ? (tipo as TipoConocido) : "otro";
}
