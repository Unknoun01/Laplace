"use client";

import { useEffect, useRef, useState } from "react";
import type { TraceSummary } from "./types";

/**
 * Modo en vivo del explorador: las trazas nuevas se van añadiendo según llegan.
 *
 * Es **polling**, no WebSockets, y a propósito. Un socket obliga a un servidor con
 * estado, a reconexión, a latidos y a un camino distinto en local y en la nube; una
 * petición cada pocos segundos contra la misma API de lectura que ya existe no obliga a
 * nada de eso y se comporta igual en los dos modos. Mirar trazas es mirar lo que acaba
 * de pasar, y unos segundos de retraso no cambian ninguna decisión. Cuando haya
 * volumen suficiente para que el coste del polling importe, se anota como evolución
 * (D-079).
 *
 * Dos cuidados que no son opcionales:
 *
 * - **Se para al desmontar y al pausar.** Un intervalo huérfano sigue pidiendo contra
 *   el backend de alguien para siempre.
 * - **No se pisan las peticiones.** Si una tarda más que el intervalo, no se lanza otra
 *   encima: contra un backend lento, eso convierte una pantalla abierta en una carga.
 */
export const LIVE_INTERVAL_MS = 5000;

/**
 * Cuántas trazas se guardan en la lista en vivo. Con el modo encendido toda una tarde,
 * la lista crecía sin fin y cada vuelta recorría y pintaba miles de filas.
 */
const LIVE_MAX_TRACES = 500;

export interface Live {
  /** Las trazas a pintar: las nuevas delante, las de la carga inicial detrás. */
  traces: TraceSummary[];
  /** Identificadores llegados en la última vuelta, para marcarlas un momento. */
  nuevas: Set<string>;
  /** Cuántas han entrado desde que se encendió. Es lo que hace visible que va. */
  recibidas: number;
  /** Instante de la última consulta con éxito, o `null` si aún no ha habido ninguna. */
  ultima: Date | null;
  /** El backend ha dejado de responder. Se dice, no se finge que sigue en vivo. */
  fallando: boolean;
}

export function useLive(
  iniciales: TraceSummary[],
  recargar: () => Promise<TraceSummary[]>,
  activo: boolean,
): Live {
  const [traces, setTraces] = useState<TraceSummary[]>(iniciales);
  const [nuevas, setNuevas] = useState<Set<string>>(new Set());
  const [recibidas, setRecibidas] = useState(0);
  const [ultima, setUltima] = useState<Date | null>(null);
  const [fallando, setFallando] = useState(false);

  // La carga inicial manda: si cambian los filtros, se empieza de cero en vez de
  // arrastrar trazas que ya no cumplen lo que el usuario ha pedido.
  const firma = iniciales.map((t) => t.trace_id).join(",");
  useEffect(() => {
    setTraces(iniciales);
    setNuevas(new Set());
    setRecibidas(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firma]);

  const enVuelo = useRef(false);
  const fn = useRef(recargar);
  fn.current = recargar;
  const actuales = useRef(traces);
  actuales.current = traces;

  useEffect(() => {
    if (!activo) return;
    let vigente = true;

    const vuelta = async () => {
      if (enVuelo.current) return; // no encadenar peticiones sobre un backend lento
      enVuelo.current = true;
      try {
        const frescas = await fn.current();
        if (!vigente) return;
        setFallando(false);
        setUltima(new Date());
        // Las nuevas se calculan contra la lista que hay ahora, **fuera** del
        // actualizador. Dentro, `setNuevas` y `setRecibidas` eran efectos en una función
        // que React puede llamar dos veces (StrictMode lo hace en desarrollo), y el
        // contador de recibidas se duplicaba.
        const conocidas = new Set(actuales.current.map((t) => t.trace_id));
        const entrantes = frescas.filter((t) => !conocidas.has(t.trace_id));
        const porId = new Map(frescas.map((t) => [t.trace_id, t]));
        setTraces((previas) => {
          // Aunque no haya trazas nuevas, las que ya estaban pueden haber crecido: una
          // ejecución en curso suma pasos y coste después de aparecer.
          const puestas = previas.map((t) => porId.get(t.trace_id) ?? t);
          const yaEstan = new Set(previas.map((t) => t.trace_id));
          const delante = entrantes.filter((t) => !yaEstan.has(t.trace_id));
          return [...delante, ...puestas].slice(0, LIVE_MAX_TRACES);
        });
        if (entrantes.length > 0) {
          setNuevas(new Set(entrantes.map((t) => t.trace_id)));
          setRecibidas((n) => n + entrantes.length);
        }
      } catch {
        if (vigente) setFallando(true);
      } finally {
        enVuelo.current = false;
      }
    };

    const id = setInterval(vuelta, LIVE_INTERVAL_MS);
    void vuelta();
    return () => {
      vigente = false;
      clearInterval(id);
    };
  }, [activo]);

  // La marca de «recién llegada» dura lo que dura la animación y se va sola: si se
  // quedara, a los cinco minutos media tabla estaría resaltada y no diría nada.
  useEffect(() => {
    if (nuevas.size === 0) return;
    const id = setTimeout(() => setNuevas(new Set()), 2000);
    return () => clearTimeout(id);
  }, [nuevas]);

  return { traces, nuevas, recibidas, ultima, fallando };
}
