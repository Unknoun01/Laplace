/** Formateo compartido. El coste está en USD: es la moneda de las tarifas de los proveedores. */

export function formatCost(usd: number): string {
  if (!usd) return "$0";
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(4)}`;
  // Por debajo del céntimo se enseñan más decimales: en un agente, el gasto de un
  // paso es minúsculo y el de un mes no. Redondear a dos decimales lo borraría todo.
  return `$${trimZeros(usd.toFixed(6))}`;
}

function trimZeros(value: string): string {
  return value.replace(/0+$/, "").replace(/\.$/, "");
}

export function formatDuration(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes} m ${seconds} s`;
}

export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("es-ES").format(value);
}

export function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelative(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "hace un momento";
  if (seconds < 3600) return `hace ${Math.floor(seconds / 60)} min`;
  if (seconds < 86_400) return `hace ${Math.floor(seconds / 3600)} h`;
  return `hace ${Math.floor(seconds / 86_400)} d`;
}

/** Paleta por tipo de span. Un color por tipo, el mismo en toda la aplicación. */
export const SPAN_COLORS: Record<string, { dot: string; chip: string; bar: string }> = {
  agent: {
    dot: "bg-violet-500",
    chip: "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-500/10 dark:text-violet-300 dark:ring-violet-400/20",
    bar: "bg-violet-500",
  },
  llm: {
    dot: "bg-sky-500",
    chip: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-400/20",
    bar: "bg-sky-500",
  },
  tool: {
    dot: "bg-amber-500",
    chip: "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-400/20",
    bar: "bg-amber-500",
  },
  retrieval: {
    dot: "bg-teal-500",
    chip: "bg-teal-50 text-teal-700 ring-teal-600/20 dark:bg-teal-500/10 dark:text-teal-300 dark:ring-teal-400/20",
    bar: "bg-teal-500",
  },
  chain: {
    dot: "bg-slate-400",
    chip: "bg-slate-100 text-slate-600 ring-slate-500/20 dark:bg-slate-500/10 dark:text-slate-300 dark:ring-slate-400/20",
    bar: "bg-slate-400",
  },
};

export function spanColors(type: string) {
  return SPAN_COLORS[type] ?? SPAN_COLORS.chain;
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
