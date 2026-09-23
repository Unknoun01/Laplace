"use client";

import { useEffect, useState } from "react";

/**
 * El equivalente en euros, con el tipo de cambio que pone el usuario (D-123).
 *
 * Los proveedores facturan en dólares y ésa sigue siendo la cifra: el euro va al lado,
 * con «≈», porque es una conversión y no la factura. El tipo no se descarga de ninguna
 * parte —el modo local no llama a nadie— y no se inventa: lo escribe quien lo quiere
 * ver, y la pantalla de ajustes dice que es suyo. Es una preferencia de este navegador,
 * no del proyecto: dos personas del mismo equipo pueden querer verlo distinto.
 */
const CLAVE = "laplace.eur_rate";
const EVENTO = "laplace:eur-rate";

export function leerTipo(): number | null {
  try {
    const valor = Number(window.localStorage.getItem(CLAVE));
    return Number.isFinite(valor) && valor > 0 ? valor : null;
  } catch {
    return null;
  }
}

export function guardarTipo(tipo: number | null): void {
  try {
    if (tipo && tipo > 0) window.localStorage.setItem(CLAVE, String(tipo));
    else window.localStorage.removeItem(CLAVE);
  } catch {
    /* almacenamiento bloqueado: la preferencia dura lo que la pestaña */
  }
  window.dispatchEvent(new Event(EVENTO));
}

/** El tipo en vigor, y se actualiza si se cambia en otra pantalla. */
export function useTipoEuro(): number | null {
  const [tipo, setTipo] = useState<number | null>(null);
  useEffect(() => {
    const actualizar = () => setTipo(leerTipo());
    actualizar();
    window.addEventListener(EVENTO, actualizar);
    return () => window.removeEventListener(EVENTO, actualizar);
  }, []);
  return tipo;
}

export function euros(usd: number, tipo: number): string {
  const valor = usd * tipo;
  const decimales = Math.abs(valor) >= 100 ? 0 : Math.abs(valor) >= 1 ? 2 : 4;
  const texto = new Intl.NumberFormat("es-ES", {
    useGrouping: "always",
    minimumFractionDigits: decimales === 4 ? 0 : decimales,
    maximumFractionDigits: decimales,
  } as unknown as Intl.NumberFormatOptions).format(valor);
  return `≈ ${texto} €`;
}

/** «≈ 1,38 €» al lado de una cifra en dólares, o nada si no hay tipo puesto. */
export function Euros({ usd, className }: { usd: number | null | undefined; className?: string }) {
  const tipo = useTipoEuro();
  if (!tipo || usd === null || usd === undefined || usd === 0) return null;
  return <span className={className ?? "eur"}>{euros(usd, tipo)}</span>;
}
