"use client";

import { useEffect } from "react";

/**
 * El título de la pestaña con lo que se está viendo de verdad —la traza, el problema—
 * y no sólo la sección (D-125). Con tres fichas abiertas, «Problema · Laplace» tres
 * veces no ayuda a encontrar ninguna.
 */
export function useTitulo(texto: string | null | undefined): void {
  useEffect(() => {
    if (!texto) return;
    const corto = texto.length > 60 ? `${texto.slice(0, 57)}…` : texto;
    document.title = `${corto} · Laplace`;
  }, [texto]);
}
