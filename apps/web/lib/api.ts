import type { ProjectStats, Trace, TraceListPage } from "./types";

/**
 * Cliente del backend de lectura.
 *
 * Se llama desde Server Components, así que la URL es la del servicio, no la del
 * navegador. Nada de esto se cachea: mirar trazas es mirar lo que acaba de pasar.
 */
const API_URL = process.env.LAPLACE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const url = new URL(path, API_URL);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, value);
  }

  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(`${response.status} en ${path}`, response.status);
  }
  return (await response.json()) as T;
}

export interface TraceQuery {
  project_id?: string;
  search?: string;
  status?: string;
  session_id?: string;
  /** Valor de `next_cursor` de la página anterior. Opaco: no construirlo a mano. */
  cursor?: string;
  limit?: string;
}

export function listTraces(query: TraceQuery = {}): Promise<TraceListPage> {
  return get<TraceListPage>("/api/traces", { limit: "50", ...query });
}

export async function getTrace(traceId: string): Promise<Trace | null> {
  try {
    return await get<Trace>(`/api/traces/${traceId}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function listProjects(): Promise<ProjectStats[]> {
  const data = await get<{ projects: ProjectStats[] }>("/api/projects");
  return data.projects;
}

/** True si el backend responde. Sirve para distinguir "no hay datos" de "no hay backend". */
export async function backendReachable(): Promise<boolean> {
  try {
    await get<unknown>("/health");
    return true;
  } catch {
    return false;
  }
}
