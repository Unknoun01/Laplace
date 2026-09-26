/**
 * Formateo compartido.
 *
 * El dinero se muestra en la moneda en la que está guardado, que hoy es siempre USD:
 * las tarifas de los proveedores están en dólares y no hay fuente de tipo de cambio.
 * La moneda llega desde la API (`currency`), nunca incrustada en el componente, para
 * que el día que exista conversión por proyecto no haya que tocar las pantallas.
 */

import { ETIQUETAS, idiomaActual, type Idioma } from "./idioma";
import { t, tn } from "./textos";

/**
 * Cómo se escribe una cifra en cada idioma (D-147). Espejo exacto de `CONVENCIONES` en
 * `cifras.py`, y comprobado contra él valor a valor en `test_cifras.py`, que ejecuta este
 * fichero con Node.
 *
 * La copia existe porque esto corre en otro runtime (D-069). Lo que no puede pasar es
 * que las dos diverjan: el resumen de un hallazgo lo redacta el backend y el importe de
 * su tarjeta lo pinta esta función, y se leen **uno al lado del otro** en la misma
 * tarjeta. Antes divergían, y en la cabecera de una traza convivían «120.255 / 108» y
 * «$0.007181» y «12,8 pasos por ejecución»: tres lecturas del mismo carácter (D-120).
 *
 * No se usa `Intl.NumberFormat` para las cifras: su salida cambia con la versión de ICU
 * del navegador, y entonces el espejo con Python sería una casualidad.
 */
const NBSP = " ";
const NNBSP = " ";

interface Convencion {
  millar: string;
  decimal: string;
  dinero: string;
  simbolos: Record<string, string>;
  porcentaje: string;
}

export const CONVENCIONES: Record<Idioma, Convencion> = {
  es: { millar: ".", decimal: ",", dinero: `{n}${NBSP}{s}`, simbolos: { USD: "US$", EUR: "€" }, porcentaje: `{n}${NBSP}%` },
  en: { millar: ",", decimal: ".", dinero: "{s}{n}", simbolos: { USD: "$", EUR: "€" }, porcentaje: "{n}%" },
  pt: { millar: ".", decimal: ",", dinero: `{s}${NBSP}{n}`, simbolos: { USD: "US$", EUR: "€" }, porcentaje: "{n}%" },
  fr: { millar: NNBSP, decimal: ",", dinero: `{n}${NBSP}{s}`, simbolos: { USD: "$US", EUR: "€" }, porcentaje: `{n}${NBSP}%` },
  zh: { millar: ",", decimal: ".", dinero: "{s}{n}", simbolos: { USD: "US$", EUR: "€" }, porcentaje: "{n}%" },
};

function convencion(): Convencion {
  return CONVENCIONES[idiomaActual()];
}

/**
 * |valor| con `decimales` decimales, en cifras inglesas y sin agrupar. `toFixed` redondea
 * el valor binario exacto y, en empate, hacia arriba: lo mismo que `_fijo` en Python.
 * Es el único sitio donde se permite `toFixed` con decimales (`test_cifras.py`).
 */
function fijo(valor: number, decimales: number): string {
  return Math.abs(valor).toFixed(decimales);
}

function local(cifras: string, negativo: boolean, quitarCeros: boolean): string {
  const c = convencion();
  let [entero, fraccion = ""] = cifras.split(".");
  if (quitarCeros) fraccion = fraccion.replace(/0+$/, "");
  const grupos: string[] = [];
  while (entero.length > 3) {
    grupos.unshift(entero.slice(-3));
    entero = entero.slice(0, -3);
  }
  grupos.unshift(entero);
  const texto = grupos.join(c.millar) + (fraccion ? c.decimal + fraccion : "");
  return (negativo && /[1-9]/.test(texto) ? "-" : "") + texto;
}

function numero(valor: number, decimales: number, quitarCeros: boolean): string {
  return local(fijo(valor, decimales), valor < 0, quitarCeros);
}

function conSimbolo(cifra: string, currency: string): string {
  const c = convencion();
  const negativo = cifra.startsWith("-");
  const texto = c.dinero.replace("{n}", cifra.replace(/^-/, "")).replace("{s}", c.simbolos[currency] ?? currency);
  return (negativo ? "-" : "") + texto;
}

/** Espejo de `cifras.dinero`: la precisión que la magnitud necesita. */
export function money(amount: number, currency = "USD"): string {
  const value = Math.abs(amount);
  let cifra: string;
  if (value === 0) cifra = "0";
  else if (value >= 100) cifra = numero(amount, 0, false);
  // Dos decimales hasta los diez céntimos, como cualquier precio.
  else if (value >= 0.1) cifra = numero(amount, 2, false);
  // Dos cifras significativas entre el céntimo y los diez céntimos: «0,0692 al mes» son
  // cuatro decimales que nadie lee, y «0,069» dice lo mismo.
  else if (value >= 0.01) cifra = numero(amount, 3, true);
  // Por debajo del céntimo hacen falta más decimales: el coste de un paso es
  // minúsculo y el de un mes no. Redondear a dos lo borraría todo.
  else cifra = numero(amount, 6, true);
  return conSimbolo(cifra, currency);
}

/** Espejo de `cifras.dinero_exacto`: todos los decimales, para cuadrar cuentas. */
export function moneyExact(amount: number, currency = "USD"): string {
  return conSimbolo(numero(amount, 6, true), currency);
}

/**
 * Un importe para leer de un vistazo, no para cuadrar cuentas (D-124).
 *
 * En la tarjeta de un problema, «0,000816 US$» no dice nada: por debajo del céntimo se
 * escribe «< 0,01 US$». La cifra exacta sigue en la ficha y en el `title`. Donde el
 * importe por paso o por ejecución ES la medida —el árbol, el explorador, el panel—
 * se usa `money()`, que no redondea a nada.
 */
export function moneyShort(amount: number, currency = "USD"): string {
  if (amount > 0 && amount < 0.01) return `< ${conSimbolo(numero(0.01, 2, false), currency)}`;
  return money(amount, currency);
}

/** Espejo de `cifras.miles`: 12345 → «12.345» en español. */
export function miles(value: number): string {
  return numero(value, 0, false);
}

/** Espejo de `cifras.decimal`: sin los ceros que sobran al final. */
export function decimal(value: number, decimales = 1): string {
  return numero(value, decimales, true);
}

/** Espejo de `cifras.porcentaje`: 0,934 → «93 %» en español, «93%» en inglés. */
export function porcentaje(ratio: number, decimales = 0): string {
  return convencion().porcentaje.replace("{n}", decimal(ratio * 100, decimales));
}

/** Una proporción con un decimal: «3,5 %». */
export function percent(ratio: number): string {
  return porcentaje(ratio, 1);
}

/**
 * Un número crudo con los decimales que se pidan, para el volcado de atributos del
 * modo avanzado. Va por aquí y no con `toFixed` porque `toFixed` escribe el punto
 * decimal inglés pase lo que pase, y entonces el mismo panel enseña «1.234,567» en una
 * fila y «1234.567» en la de al lado (D-120).
 */
export function exacto(value: number, decimals: number): string {
  return numero(value, decimals, true);
}

/** Un entero agrupado. */
export function number(value: number): string {
  return Number.isInteger(value) ? miles(value) : decimal(value, 2);
}

export function duration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${numero(ms / 1000, 2, false)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} m ${Math.round((ms % 60_000) / 1000)} s`;
}

export function tokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${numero(count / 1000, count < 10_000 ? 1 : 0, false)} k`;
  return `${numero(count / 1_000_000, 1, false)} M`;
}

export function timestamp(iso: string): string {
  return new Date(iso).toLocaleString(ETIQUETAS[idiomaActual()], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** «08 sept, 02:00». Para ejes y cabeceras, donde los segundos son ruido. */
export function dayHour(iso: string): string {
  return new Date(iso).toLocaleString(ETIQUETAS[idiomaActual()], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relative(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return t("relativo.ahora");
  if (seconds < 3600) return t("relativo.min", { n: Math.floor(seconds / 60) });
  if (seconds < 86_400) return t("relativo.h", { n: Math.floor(seconds / 3600) });
  return t("relativo.d", { n: Math.floor(seconds / 86_400) });
}

/** Vuelca cualquier payload a texto legible sin romperse. */
export function pretty(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Recorta un texto largo para usarlo como resumen de una línea. */
export function oneLine(value: unknown, max = 90): string {
  const text = pretty(value).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

type Unidad = "dias" | "horas" | "minutos";

/** La duración en la unidad que se lee mejor, o `null` si es menos de un minuto. */
function medir(days: number): { unidad: Unidad; n: number } | null {
  if (days >= 1) return { unidad: "dias", n: Math.abs(days - 1) < 0.05 ? 1 : days };
  const hours = days * 24;
  if (hours >= 1.5) return { unidad: "horas", n: hours };
  if (hours >= 0.95) return { unidad: "horas", n: 1 };
  const minutes = Math.round(hours * 60);
  return minutes <= 0 ? null : { unidad: "minutos", n: minutes };
}

/**
 * Una duración en palabras: «12 minutos», «1 hora», «5,2 horas», «2,5 días».
 *
 * Espejo de `span_label` en el backend. Está duplicado a propósito: el backend lo
 * necesita para redactar el cálculo de un hallazgo y una alerta de Slack, y la interfaz
 * para etiquetar la cifra grande sin pedir otra vez.
 */
export function spanLabel(days: number): string {
  const m = medir(days);
  if (!m) return t("tiempo.menos_de_un_minuto");
  return tn(`tiempo.${m.unidad}`, m.n, { n: decimal(m.n) });
}

/** La misma duración, como ventana: «la última hora», «los últimos 2,5 días». */
export function windowLabel(days: number): string {
  const m = medir(days);
  if (!m) return t("tiempo.menos_de_un_minuto");
  return tn(`ventana.${m.unidad}`, m.n, { n: decimal(m.n) });
}
