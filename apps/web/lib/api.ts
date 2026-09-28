import { idiomaActual } from "./idioma";
import { t } from "./textos";
import type {
  Annotation,
  AnnotationVerdict,
  AlertSettings,
  Breakdown,
  Budget,
  Comparison,
  CustomPrices,
  Dataset,
  DatasetItem,
  Diff,
  FindingDetail,
  Instance,
  JudgeStatus,
  MarginView,
  StripeStatus,
  Overview,
  Panel,
  ProjectStats,
  PromptCard,
  PromptsView,
  RunSummary,
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

/**
 * Entrar con clave de API, para quien no tiene cuenta (D-130).
 *
 * La clave se manda una vez al backend, que la comprueba y la deja en una cookie
 * `httpOnly`: la interfaz no la guarda en ningún sitio que JavaScript pueda leer. Antes
 * vivía en `localStorage` y viajaba en cada petición; cualquier XSS se la llevaba, y una
 * clave de API no caduca como una sesión.
 */
export async function entrarConClave(clave: string): Promise<void> {
  await send<{ ok: boolean }>("/api/auth/key", "POST", { key: clave.trim() });
}

/** Deja de usar la clave en este navegador. */
export async function olvidarClave(): Promise<void> {
  await send<{ ok: boolean }>("/api/auth/key", "DELETE");
}

/**
 * Los navegadores que ya tenían la clave en `localStorage` la pasan a la cookie una sola
 * vez y la borran de ahí. Si la clave ya no vale, se borra igual: no hay razón para
 * seguir guardando en claro una credencial que el backend rechaza.
 */
const CLAVE_ANTIGUA = "laplace.api_key";
let migracion: Promise<void> | null = null;

function migrarClaveAntigua(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (migracion) return migracion;
  let antigua = "";
  try {
    antigua = window.localStorage.getItem(CLAVE_ANTIGUA) ?? "";
    window.localStorage.removeItem(CLAVE_ANTIGUA);
  } catch {
    /* almacenamiento bloqueado: no hay nada que migrar */
  }
  migracion = antigua
    ? fetch(new URL("/api/auth/key", API_URL || window.location.origin), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Laplace": "1" },
        body: JSON.stringify({ key: antigua }),
        cache: "no-store",
      }).then(
        () => undefined,
        () => undefined,
      )
    : Promise.resolve();
  return migracion;
}

/** Cabeceras de una petición. La credencial va en cookie; aquí sólo lo anti-CSRF. */
function cabeceras(extra?: Record<string, string>): Record<string, string> {
  return {
    ...(extra ?? {}),
    // Toda petición la lleva: el backend la exige en las escrituras con cookie, y un
    // formulario de otro sitio no puede ponerla sin pasar por CORS (D-127).
    "X-Laplace": "1",
    // Las frases del motor —títulos, lecturas, avisos— se redactan en el backend: tienen
    // que llegar en el idioma de la pantalla (D-147).
    "Accept-Language": idiomaActual(),
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** El caso, cuando el backend lo dice: el mensaje viene traducido (D-149). */
    readonly code: string = "",
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | undefined>;

/**
 * El error del backend con la frase que lo explica, ya en el idioma de la pantalla: el backend
 * la redacta en el de `Accept-Language` (D-149). Es la que hay que leer para arreglarlo
 * —«esta clave no tiene acceso a ese proyecto», «ese filtro no selecciona ninguna
 * traza»—, y tragarla dejaría un «500» pelado. La validación de FastAPI (422) no trae
 * frase sino una lista de campos, y `String()` de eso era «[object Object]».
 */
async function errorDe(response: Response, path: string): Promise<ApiError> {
  const error = (mensaje: string, codigo = "") => new ApiError(mensaje, response.status, codigo);
  try {
    const cuerpo = await response.json();
    const detalle = cuerpo?.detail;
    if (typeof detalle === "string" && detalle) return error(detalle, String(cuerpo?.code ?? ""));
    if (Array.isArray(detalle) && detalle.length) {
      const campos = detalle
        .map((e: { loc?: unknown[] }) => (e?.loc ?? []).filter((x) => x !== "body").join("."))
        .filter(Boolean);
      return error(t("api.no_valida", { campos: [...new Set(campos)].join(", ") || "?" }));
    }
  } catch {
    /* la respuesta no era JSON: se queda el mensaje genérico */
  }
  return error(t("api.fallo", { estado: response.status, ruta: path }));
}

/**
 * `senal` cancela la petición (D-131). Las lecturas con las que las pantallas se cargan
 * la reciben de `useApi`, que la dispara al cambiar de filtros o salir de la pantalla:
 * así el backend deja de trabajar en consultas que ya nadie va a mirar. Va explícita y
 * no «en el ambiente» porque en el navegador no hay forma de que sobreviva a un `await`,
 * y casi todas las pantallas piden primero los proyectos y después lo pesado.
 */
async function get<T>(path: string, params?: Params, senal?: AbortSignal): Promise<T> {
  await migrarClaveAntigua();
  const url = new URL(path, API_URL || window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }

  const response = await fetch(url, { cache: "no-store", headers: cabeceras(), signal: senal });
  if (!response.ok) {
    throw await errorDe(response, path);
  }
  return (await response.json()) as T;
}

/** Los rangos que ofrece el selector de la barra superior. */
export const RANGES = [
  { days: 1, label: "rango.1" },
  { days: 7, label: "rango.7" },
  { days: 30, label: "rango.30" },
] as const;

export const DEFAULT_DAYS = 7;

export function parseDays(raw: string | undefined): number {
  const days = Number(raw);
  return RANGES.some((r) => r.days === days) ? days : DEFAULT_DAYS;
}

// ---------------------------------------------------------------------------------

export function getOverview(
  projectId: string,
  days: number,
  senal?: AbortSignal,
): Promise<Overview> {
  return get<Overview>("/api/overview", { project_id: projectId, days }, senal);
}

export function getPanel(projectId: string, days: number, senal?: AbortSignal): Promise<Panel> {
  return get<Panel>("/api/panel", { project_id: projectId, days }, senal);
}

export async function getFinding(
  findingId: string,
  projectId: string,
  days: number,
  senal?: AbortSignal,
): Promise<FindingDetail | null> {
  try {
    return await get<FindingDetail>(
      `/api/findings/${encodeURI(findingId)}`,
      { project_id: projectId, days },
      senal,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export interface TraceQuery {
  project_id?: string;
  search?: string;
  /** Texto dentro de prompts, respuestas y herramientas; al menos 3 caracteres. */
  content?: string;
  /** Identidad exacta de un paso, la que trae cada hallazgo en `step_key`. */
  step_key?: string;
  user_id?: string;
  /** El cliente que paga (D-161). */
  customer_id?: string;
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

export function listTraces(query: TraceQuery = {}, senal?: AbortSignal): Promise<TraceListPage> {
  return get<TraceListPage>("/api/traces", { limit: 50, ...query }, senal);
}

export async function getTrace(
  traceId: string,
  projectId?: string,
  senal?: AbortSignal,
): Promise<Trace | null> {
  try {
    // El proyecto va siempre que se sepa: un identificador de traza es único dentro
    // de un proyecto, no entre proyectos.
    return await get<Trace>(`/api/traces/${traceId}`, { project_id: projectId }, senal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function listProjects(senal?: AbortSignal): Promise<ProjectStats[]> {
  const data = await get<{ projects: ProjectStats[] }>("/api/projects", undefined, senal);
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

// ---------------------------------------------------------------------------------
// Evaluaciones (Fase 5)
// ---------------------------------------------------------------------------------

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  if (path !== "/api/auth/key") await migrarClaveAntigua();
  const url = new URL(path, API_URL || window.location.origin);
  const response = await fetch(url, {
    method,
    headers: cabeceras(body === undefined ? {} : { "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    throw await errorDe(response, path);
  }
  return (await response.json()) as T;
}

/** Marca una traza. SIEMPRE crea una anotación humana: el juez tiene su propia ruta. */
export function annotate(input: {
  project_id: string;
  trace_id: string;
  verdict: AnnotationVerdict;
  comment?: string;
  author?: string;
}): Promise<Annotation> {
  return send<Annotation>("/api/annotations", "POST", input);
}

/** Con el proyecto: con cuentas, una escritura dice sobre qué proyecto es (D-127). */
export function deleteAnnotation(id: string, projectId: string): Promise<{ deleted: boolean }> {
  return send<{ deleted: boolean }>(
    `/api/annotations/${encodeURIComponent(id)}?project_id=${encodeURIComponent(projectId)}`,
    "DELETE",
  );
}

/** Con el proyecto: el backend acota las anotaciones a él y no devuelve las de otros. */
export async function annotationsFor(
  traceIds: string[],
  projectId: string,
): Promise<Record<string, Annotation[]>> {
  if (traceIds.length === 0) return {};
  const data = await get<{ annotations: Record<string, Annotation[]> }>("/api/annotations", {
    trace_ids: traceIds.join(","),
    project_id: projectId,
  });
  return data.annotations;
}

export function judgeStatus(): Promise<JudgeStatus> {
  return get<JudgeStatus>("/api/judge");
}

export interface JudgeResult {
  judged: number;
  failed: { trace_id: string; error: string }[];
  cost_usd: number;
  cost_unknown: boolean;
  model: string;
  prompt_version: string;
}

/** Pasa el juez por unas trazas o por una tirada entera. Devuelve lo que ha costado. */
export function runJudge(input: {
  project_id: string;
  trace_ids?: string[];
  run_id?: string;
}): Promise<JudgeResult> {
  return send<JudgeResult>("/api/judge", "POST", input);
}

export function judgePrompt(projectId: string, traceId: string): Promise<{ system: string; user: string }> {
  return get<{ system: string; user: string }>("/api/judge/prompt", {
    project_id: projectId,
    trace_id: traceId,
  });
}

export async function listDatasets(projectId: string): Promise<Dataset[]> {
  const data = await get<{ datasets: Dataset[] }>("/api/datasets", { project_id: projectId });
  return data.datasets;
}

export function getDataset(id: string): Promise<{ dataset: Dataset; items: DatasetItem[] }> {
  return get<{ dataset: Dataset; items: DatasetItem[] }>(`/api/datasets/${encodeURIComponent(id)}`);
}

/** Crea un conjunto a partir de un filtro del explorador: tráfico real, no inventado. */
export function createDataset(input: {
  project_id: string;
  name: string;
  description?: string;
  filter: Record<string, string | undefined>;
  limit?: number;
}): Promise<Dataset> {
  return send<Dataset>("/api/datasets", "POST", input);
}

export function deleteDataset(id: string, projectId: string): Promise<{ deleted: boolean }> {
  return send<{ deleted: boolean }>(
    `/api/datasets/${encodeURIComponent(id)}?project_id=${encodeURIComponent(projectId)}`,
    "DELETE",
  );
}

export async function listRuns(projectId: string, datasetId?: string): Promise<RunSummary[]> {
  const data = await get<{ runs: RunSummary[] }>("/api/runs", {
    project_id: projectId,
    dataset_id: datasetId,
  });
  return data.runs;
}

/** A vs B. Las dos tiradas tienen que ser del mismo conjunto; el backend lo exige. */
export function compareRuns(projectId: string, a: string, b: string): Promise<Comparison> {
  return get<Comparison>("/api/experiments/compare", { project_id: projectId, a, b });
}

// ---------------------------------------------------------------------------------
// Prompts (Fase 6)
// ---------------------------------------------------------------------------------

export function getPrompts(projectId: string, days: number): Promise<PromptsView> {
  return get<PromptsView>("/api/prompts", { project_id: projectId, days });
}

/** La ficha de un prompt: igual que la tarjeta de la lista, pero con los textos. */
export function getPrompt(id: string, projectId: string, days: number): Promise<PromptCard> {
  return get<PromptCard>(`/api/prompts/${encodeURIComponent(id)}`, {
    project_id: projectId,
    days,
  });
}

export function promptDiff(id: string, a: number, b: number): Promise<Diff> {
  return get<Diff>(`/api/prompts/${encodeURIComponent(id)}/diff`, { a, b });
}

export function createPrompt(input: {
  project_id: string;
  name: string;
  description?: string;
  text: string;
  notes?: string;
}): Promise<PromptCard> {
  return send<PromptCard>("/api/prompts", "POST", input);
}

/** Guarda una versión. Por defecto **no** la despliega: son dos gestos distintos. */
export function addPromptVersion(
  id: string,
  input: { project_id: string; text: string; notes?: string; deploy?: boolean },
): Promise<{ version: number }> {
  return send<{ version: number }>(
    `/api/prompts/${encodeURIComponent(id)}/versions`,
    "POST",
    input,
  );
}

/** Pone una versión en producción. Volver a una anterior es el rollback de un clic. */
export function setProduction(
  id: string,
  input: { project_id: string; version: number; note?: string },
): Promise<{ detail: string }> {
  return send<{ detail: string }>(
    `/api/prompts/${encodeURIComponent(id)}/production`,
    "POST",
    input,
  );
}

export function deletePrompt(id: string, projectId: string): Promise<{ deleted: boolean }> {
  return send<{ deleted: boolean }>(
    `/api/prompts/${encodeURIComponent(id)}?project_id=${encodeURIComponent(projectId)}`,
    "DELETE",
  );
}

// ---------------------------------------------------------------------------------
// Ajustes del proyecto (D-123)
// ---------------------------------------------------------------------------------

export function setFindingState(input: {
  project_id: string;
  finding_id: string;
  status: "arreglado" | "ignorado";
  note?: string;
}): Promise<{ status: string; at: string }> {
  return send("/api/finding-state", "POST", input);
}

export function clearFindingState(projectId: string, findingId: string): Promise<unknown> {
  const q = new URLSearchParams({ project_id: projectId, finding_id: findingId });
  return send(`/api/finding-state?${q}`, "DELETE");
}

/** Coste, ingresos y margen de cada cliente (D-161). */
export function getCustomers(
  projectId: string,
  days: number,
  senal?: AbortSignal,
): Promise<MarginView> {
  return get<MarginView>("/api/customers", { project_id: projectId, days }, senal);
}

/** Lo que paga un cliente al mes; `null` lo quita. */
export function setCustomerRevenue(
  projectId: string,
  customerId: string,
  monthly: number | null,
): Promise<{ customer_id: string; monthly: number | null }> {
  return send("/api/customers/revenue", "PUT", {
    project_id: projectId,
    customer_id: customerId,
    monthly,
  });
}

export function getStripe(projectId: string): Promise<StripeStatus> {
  return get<StripeStatus>("/api/stripe", { project_id: projectId });
}

/** Pone la clave de Stripe; `null` la quita. */
export function setStripe(projectId: string, apiKey: string | null): Promise<StripeStatus> {
  return send<StripeStatus>("/api/stripe", "PUT", { project_id: projectId, api_key: apiKey });
}

export function syncStripe(projectId: string): Promise<{ customers: number; detail: string }> {
  return send("/api/stripe/sync", "POST", { project_id: projectId });
}

export function getBudget(projectId: string, senal?: AbortSignal): Promise<Budget> {
  return get<Budget>("/api/budget", { project_id: projectId }, senal);
}

export function setBudget(projectId: string, monthlyLimit: number | null): Promise<Budget> {
  return send<Budget>("/api/budget", "PUT", { project_id: projectId, monthly_limit: monthlyLimit });
}

export function getAlertSettings(projectId: string, senal?: AbortSignal): Promise<AlertSettings> {
  return get<AlertSettings>("/api/alert-settings", { project_id: projectId }, senal);
}

export function setAlertSettings(
  projectId: string,
  cambios: Partial<{
    webhook_url: string;
    generic_webhook_url: string;
    email_to: string;
    threshold: number;
    quiet_hours: number;
    muted: boolean;
    muted_kinds: string[];
  }>,
): Promise<AlertSettings> {
  return send<AlertSettings>("/api/alert-settings", "PUT", { project_id: projectId, ...cambios });
}

export function testAlert(projectId: string): Promise<{ delivered: boolean }> {
  return send(`/api/alert-settings/test?project_id=${encodeURIComponent(projectId)}`, "POST");
}

export function getBreakdown(
  projectId: string,
  days: number,
  by: "user" | "session",
): Promise<Breakdown> {
  return get<Breakdown>("/api/breakdown", { project_id: projectId, days, by });
}

export function getCustomPrices(senal?: AbortSignal): Promise<CustomPrices> {
  return get<CustomPrices>("/api/pricing/custom", undefined, senal);
}

export function setCustomPrice(input: {
  model: string;
  input: number;
  output: number;
  cached_input?: number;
}): Promise<{ repriced_spans: number }> {
  return send("/api/pricing/custom", "PUT", input);
}

export function deleteCustomPrice(model: string): Promise<{ repriced_spans: number }> {
  return send(`/api/pricing/custom?model=${encodeURIComponent(model)}`, "DELETE");
}

export function getInstance(senal?: AbortSignal): Promise<Instance> {
  return get<Instance>("/api/instance", undefined, senal);
}

export function loadDemo(): Promise<{ project_id: string; traces: number }> {
  return send("/api/demo", "POST");
}

export function deleteProject(projectId: string): Promise<{ deleted: boolean }> {
  const q = new URLSearchParams({ project_id: projectId, confirm: projectId });
  return send(`/api/projects?${q}`, "DELETE");
}

// ---------------------------------------------------------------------------------
// Cuentas y organización (D-127)
// ---------------------------------------------------------------------------------

export type Rol = "lector" | "miembro" | "admin" | "propietario";

export interface Me {
  mode: "local" | "nube";
  user: { id: string; email: string; name: string; is_admin: boolean } | null;
  orgs?: { id: string; name: string; role: Rol }[];
  needs_setup?: boolean;
  by_key?: boolean;
  /** Rol en cada proyecto que ve. Vacío para quien administra la instalación. */
  roles?: Record<string, Rol>;
}

export interface OrgKey {
  id: string;
  project_id: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  created_by: string;
}

export interface Org {
  id: string;
  name: string;
  role: Rol;
  members: { user_id: string; email: string; name: string; role: Rol; since: string }[];
  projects: string[];
  invitations?: { email: string; role: Rol; created_at: string; expires_at: string }[];
  keys?: OrgKey[];
}

export function getMe(): Promise<Me> {
  return get<Me>("/api/auth/me");
}

export function login(email: string, password: string): Promise<{ ok: boolean }> {
  return send("/api/auth/login", "POST", { email, password });
}

export function logout(): Promise<{ ok: boolean }> {
  return send("/api/auth/logout", "POST");
}

export function logoutAll(): Promise<{ closed_sessions: number }> {
  return send("/api/auth/logout-all", "POST");
}

export function setupInstallation(input: {
  token: string;
  email: string;
  name: string;
  password: string;
  org_name: string;
}): Promise<{ ok: boolean }> {
  return send("/api/auth/setup", "POST", input);
}

export function changePassword(
  current: string,
  next: string,
): Promise<{ closed_sessions: number }> {
  return send("/api/auth/password", "POST", { current, new: next });
}

export function getInvitation(
  token: string,
): Promise<{ org_name: string; email: string; role: Rol; has_account: boolean }> {
  return get("/api/auth/invitation", { token });
}

export function acceptInvitation(
  token: string,
  name: string,
  password: string,
): Promise<{ org_name: string }> {
  return send("/api/auth/accept", "POST", { token, name, password });
}

export function getOrg(orgId: string): Promise<Org> {
  return get<Org>("/api/org", { org_id: orgId });
}

export function invite(
  orgId: string,
  email: string,
  role: Rol,
): Promise<{ link: string; emailed: boolean }> {
  return send("/api/org/invitations", "POST", { org_id: orgId, email, role });
}

export function cancelInvite(orgId: string, email: string): Promise<unknown> {
  const q = new URLSearchParams({ org_id: orgId, email });
  return send(`/api/org/invitations?${q}`, "DELETE");
}

export function setMemberRole(orgId: string, userId: string, role: Rol): Promise<unknown> {
  return send("/api/org/members", "PUT", { org_id: orgId, user_id: userId, role });
}

export function removeMember(orgId: string, userId: string): Promise<unknown> {
  const q = new URLSearchParams({ org_id: orgId, user_id: userId });
  return send(`/api/org/members?${q}`, "DELETE");
}

export function createKey(input: {
  org_id: string;
  project_id: string;
  name: string;
  expires_days: number;
}): Promise<{ id: string; key: string; project_id: string }> {
  return send("/api/org/keys", "POST", input);
}

export function revokeKey(orgId: string, keyId: string): Promise<unknown> {
  const q = new URLSearchParams({ org_id: orgId, key_id: keyId });
  return send(`/api/org/keys?${q}`, "DELETE");
}

export interface AuditEvent {
  at: string;
  action: string;
  target: string;
  ip: string;
  email: string;
}

export function getAudit(orgId: string): Promise<{ events: AuditEvent[] }> {
  return get("/api/org/audit", { org_id: orgId });
}
