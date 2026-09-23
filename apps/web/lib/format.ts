/**
 * Formateo compartido.
 *
 * El dinero se muestra en la moneda en la que está guardado, que hoy es siempre USD:
 * las tarifas de los proveedores están en dólares y no hay fuente de tipo de cambio.
 * La moneda llega desde la API (`currency`), nunca incrustada en el componente, para
 * que el día que exista conversión por proyecto no haya que tocar las pantallas.
 */

const SYMBOLS: Record<string, string> = { USD: "$", EUR: "€" };

/**
 * Punto para los millares, coma para los decimales. Espejo exacto de
 * `cifras.py` en el backend, y comprobado contra él en `test_cifras.py`.
 *
 * La copia existe porque esto corre en otro runtime (D-069). Lo que no puede pasar es
 * que las dos diverjan: el resumen de un hallazgo lo redacta el backend y el importe de
 * su tarjeta lo pinta esta función, y se leen **uno al lado del otro** en la misma
 * tarjeta. Antes divergían, y en la cabecera de una traza convivían «120.255 / 108» y
 * «$0.007181» y «12,8 pasos por ejecución»: tres lecturas del mismo carácter (D-120).
 */
export const SEPARADOR_DECIMAL = ",";
const LOCALE = "es-ES";
/**
 * `es-ES` no agrupa los números de cuatro cifras —escribe «1360» y «11.314»—, y el
 * backend sí («1.360»). En la misma tarjeta se leían las dos formas, así que el millar
 * se pone siempre, como en `cifras.miles`.
 */
const AGRUPAR = { useGrouping: "always" } as unknown as Intl.NumberFormatOptions;

export function money(amount: number, currency = "USD"): string {
  const symbol = SYMBOLS[currency] ?? `${currency} `;
  const value = Math.abs(amount);
  if (value === 0) return `${symbol}0`;
  if (value >= 100) return `${symbol}${round(amount, 0)}`;
  if (value >= 1) return `${symbol}${round(amount, 2)}`;
  if (value >= 0.01) return `${symbol}${round(amount, 4)}`;
  // Por debajo del céntimo hacen falta más decimales: el coste de un paso es
  // minúsculo y el de un mes no. Redondear a dos lo borraría todo.
  return `${symbol}${round(amount, 6)}`;
}

/**
 * Un importe para leer de un vistazo, no para cuadrar cuentas (D-124).
 *
 * En la tarjeta de un problema, «$0,000816» no dice nada: por debajo del céntimo se
 * escribe «< $0,01». La cifra exacta sigue en la ficha y en el `title`. Donde el
 * importe por paso o por ejecución ES la medida —el árbol, el explorador, el panel—
 * se usa `money()`, que no redondea a nada.
 */
export function moneyShort(amount: number, currency = "USD"): string {
  const symbol = SYMBOLS[currency] ?? `${currency} `;
  if (amount > 0 && amount < 0.01) return `< ${symbol}0${SEPARADOR_DECIMAL}01`;
  return money(amount, currency);
}

function round(value: number, decimals: number): string {
  const fixed = value.toLocaleString(LOCALE, {
    ...AGRUPAR,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  if (decimals <= 2) return fixed;
  // Los ceros de relleno a la derecha sobran; el separador decimal se queda sólo si
  // detrás de él queda algo.
  return fixed.replace(/0+$/, "").replace(new RegExp(`${SEPARADOR_DECIMAL}$`), "");
}

/**
 * Un número crudo con los decimales que se pidan, para el volcado de atributos del
 * modo avanzado. Va por aquí y no con `toFixed` porque `toFixed` escribe el punto
 * decimal inglés pase lo que pase, y entonces el mismo panel enseña «1.234,567» en una
 * fila y «1234.567» en la de al lado (D-120).
 */
export function exacto(value: number, decimals: number): string {
  return value.toLocaleString(LOCALE, { ...AGRUPAR, maximumFractionDigits: decimals });
}

export function duration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${round(ms / 1000, 2)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} m ${Math.round((ms % 60_000) / 1000)} s`;
}

export function tokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${round(count / 1000, count < 10_000 ? 1 : 0)} k`;
  return `${round(count / 1_000_000, 1)} M`;
}

export function number(value: number): string {
  return new Intl.NumberFormat(LOCALE, AGRUPAR).format(value);
}

export function percent(ratio: number): string {
  return `${round(ratio * 100, 1)} %`;
}

export function timestamp(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** «08 sept, 02:00». Para ejes y cabeceras, donde los segundos son ruido. */
export function dayHour(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relative(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "hace un momento";
  if (seconds < 3600) return `hace ${Math.floor(seconds / 60)} min`;
  if (seconds < 86_400) return `hace ${Math.floor(seconds / 3600)} h`;
  return `hace ${Math.floor(seconds / 86_400)} d`;
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

/**
 * Una duración en palabras: «12 minutos», «1 hora», «5,2 horas», «2,5 días».
 *
 * Espejo de `span_label` en `apps/backend/laplace_backend/insights.py`. Está duplicado
 * a propósito: el backend lo necesita para redactar el cálculo de un hallazgo y una
 * alerta de Slack, y la interfaz para etiquetar la cifra grande sin pedir otra vez.
 */
export function spanLabel(days: number): string {
  if (days >= 1) {
    if (Math.abs(days - 1) < 0.05) return "1 día";
    return `${decimal(days)} días`;
  }
  const hours = days * 24;
  if (hours >= 1.5) return `${decimal(hours)} horas`;
  if (hours >= 0.95) return "1 hora";
  const minutes = Math.round(hours * 60);
  if (minutes <= 0) return "menos de un minuto";
  return minutes === 1 ? "1 minuto" : `${minutes} minutos`;
}

/** La misma duración, como ventana: «la última hora», «los últimos 2,5 días». */
export function windowLabel(days: number): string {
  const text = spanLabel(days);
  if (text === "menos de un minuto") return text;
  if (text === "1 hora") return "la última hora";
  if (text === "1 día") return "el último día";
  if (text === "1 minuto") return "el último minuto";
  if (text.endsWith("horas")) return `las últimas ${text}`;
  return `los últimos ${text}`;
}

/** Un decimal, coma española, y sin el «,0» que sobra en «7,0 días». */
export function decimal(value: number): string {
  return round(value, 1).replace(new RegExp(`${SEPARADOR_DECIMAL}0$`), "");
}
