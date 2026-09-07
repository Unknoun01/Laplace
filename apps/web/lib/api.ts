import type {
  FindingDetail,
  Overview,
  ProjectStats,
  Trace,
  TraceListPage,
} from "./types";

/**
 * Cliente del backend.
 *
 * Se llama desde el navegador y **siempre contra el mismo origen**. En modo local, la
 * interfaz la sirve el propio proceso de Python que atiende la API. En la nube, Next
 * reescribe `/api` y `/health` hacia el backend (ver `next.config.mjs`). Así no hay
 * URL que configurar en el build, ni CORS que abrir, ni una variable que se olvide en
 * un despliegue y deje la interfaz en blanco (D-069).
 *
 * Nada se cachea: mirar trazas es mirar lo que acaba de pasar.
 */
const API_URL =
  typeof window === "undefined" ? (process.env.LAPLACE_API_URL ?? "http://localhost:8000") : "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | undefined>;

async function get<T>(path: string, params?: Params): Promise<T> {
  const url = new URL(path, API_URL || window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }

  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(`${response.status} en ${path}`, response.status);
  }
  return (await response.json()) as T;
}

/** Los rangos que ofrece el selector de la barra superior. */
export const RANGES = [
  { days: 1, label: "24 horas" },
  { days: 7, label: "7 días" },
  { days: 30, label: "30 días" },
] as const;

export const DEFAULT_DAYS = 7;

export function parseDays(raw: string | undefined): number {
  const days = Number(raw);
  return RANGES.some((r) => r.days === days) ? days : DEFAULT_DAYS;
}

// ---------------------------------------------------------------------------------

export function getOverview(projectId: string, days: number): Promise<Overview> {
  return get<Overview>("/api/overview", { project_id: projectId, days });
}

export async function getFinding(
  findingId: string,
  projectId: string,
  days: number,
): Promise<FindingDetail | null> {
  try {
    return await get<FindingDetail>(`/api/findings/${encodeURI(findingId)}`, {
      project_id: projectId,
      days,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export interface TraceQuery {
  project_id?: string;
  search?: string;
  status?: string;
  session_id?: string;
  span_type?: string;
  /** `recent` (por defecto), `cost` o `duration`. */
  sort?: string;
  /** Filtros que sólo ofrece el modo avanzado del explorador. */
  model?: string;
  min_cost_usd?: number;
  since?: string;
  /** Valor de `next_cursor` de la página anterior. Opaco: no construirlo a mano. */
  cursor?: string;
  limit?: number;
}

export function listTraces(query: TraceQuery = {}): Promise<TraceListPage> {
  return get<TraceListPage>("/api/traces", { limit: 50, ...query });
}

export async function getTrace(traceId: string, projectId?: string): Promise<Trace | null> {
  try {
    // El proyecto va siempre que se sepa: un identificador de traza es único dentro
    // de un proyecto, no entre proyectos.
    return await get<Trace>(`/api/traces/${traceId}`, { project_id: projectId });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function listProjects(): Promise<ProjectStats[]> {
  const data = await get<{ projects: ProjectStats[] }>("/api/projects");
  return data.projects;
}

/** True si el backend responde. Distingue "no hay datos" de "no hay backend". */
export async function backendReachable(): Promise<boolean> {
  try {
    await get<unknown>("/health");
    return true;
  } catch {
    return false;
  }
}

/** Inicio de la ventana activa, en ISO, para filtrar la lista de trazas. */
export function windowStart(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}
