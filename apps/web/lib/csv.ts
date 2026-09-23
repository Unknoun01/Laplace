/**
 * Exportar a CSV, para quien tiene que llevar las cifras a otro sitio (D-123).
 *
 * Punto y coma como separador y coma decimal: es lo que espera un Excel en español, y
 * un CSV con comas y puntos decimales se abre ahí con todas las cifras en una columna o
 * con los importes convertidos en fechas. Lleva BOM para que las tildes lleguen bien.
 *
 * Se arma en el navegador con lo que ya se ha pedido a la API, igual que el JSON de una
 * traza: no hace falta una ruta nueva, y la clave de API no tiene que viajar en una URL
 * de descarga.
 */

export type Celda = string | number | null | undefined;

function celda(valor: Celda): string {
  if (valor === null || valor === undefined) return "";
  let texto =
    typeof valor === "number"
      ? valor.toLocaleString("es-ES", { maximumFractionDigits: 6, useGrouping: false })
      : valor;
  if (/[";\n\r]/.test(texto)) texto = `"${texto.replace(/"/g, '""')}"`;
  return texto;
}

export function descargarCsv(nombre: string, cabecera: string[], filas: Celda[][]): void {
  const lineas = [cabecera, ...filas].map((fila) => fila.map(celda).join(";"));
  const blob = new Blob(["﻿" + lineas.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre.endsWith(".csv") ? nombre : `${nombre}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
