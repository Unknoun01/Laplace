/**
 * Formateo compartido.
 *
 * El dinero se muestra en la moneda en la que está guardado, que hoy es siempre USD:
 * las tarifas de los proveedores están en dólares y no hay fuente de tipo de cambio.
 * La moneda llega desde la API (`currency`), nunca incrustada en el componente, para
 * que el día que exista conversión por proyecto no haya que tocar las pantallas.
 */

const SYMBOLS: Record<string, string> = { USD: "$", EUR: "€" };

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

function round(value: number, decimals: number): string {
  const fixed = value.toFixed(decimals);
  return decimals > 2 ? fixed.replace(/0+$/, "").replace(/\.$/, "") : fixed;
}

export function duration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} m ${Math.round((ms % 60_000) / 1000)} s`;
}

export function tokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)} k`;
  return `${(count / 1_000_000).toFixed(1)} M`;
}

export function number(value: number): string {
  return new Intl.NumberFormat("es-ES").format(value);
}

export function percent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)} %`;
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
function decimal(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "").replace(".", ",");
}
