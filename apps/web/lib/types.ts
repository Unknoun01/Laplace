/**
 * Espejo en TypeScript del contrato de traza.
 *
 * Fuente de verdad: packages/sdk-python/laplace/schema.py y docs/trace-contract.md.
 * Un cambio allí es un cambio aquí, en el mismo commit.
 */

export type SpanType = "agent" | "llm" | "tool" | "retrieval" | "chain";
export type SpanStatus = "ok" | "error" | "unset";

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  /** Servidos desde caché. Van DENTRO de `input_tokens`, no se suman aparte. */
  cached_input_tokens: number;
  /** Escritos en caché, por duración. También dentro de `input_tokens`. */
  cache_write_tokens: number;
  cache_write_1h_tokens: number;
  reasoning_tokens: number;
  /**
   * Los tokens los contó el SDK porque el proveedor no los dio (streaming sin
   * `include_usage`). El coste derivado es una aproximación, y se dice.
   */
  estimated: boolean;
}

export interface Cost {
  input_usd: number;
  output_usd: number;
  total_usd: number;
  /**
   * No sabemos cuánto cuesta: el modelo no está en la tabla de precios. No es cero.
   * Donde esto sea true, la interfaz tiene que decirlo en vez de enseñar un total.
   */
  /** Parte de `input_usd` que se fue en leer y escribir caché. */
  cache_read_usd: number;
  cache_write_usd: number;
  /** Lo que la caché ya ha ahorrado aquí. Dinero medido, no proyectado. */
  cache_saving_usd: number;
  unknown: boolean;
  /**
   * No se pudo saber qué metro de facturación aplicó (contexto largo, residencia de
   * datos, modo rápido) y se cobró el estándar. El coste real podría ser mayor.
   */
  rate_assumed: boolean;
  rate_note: string;
  /** Tarifa aplicada: `<modelo de la tabla> @ <versión de la tabla>`. */
  rate: string;
  currency: "USD";
}

export interface LLMAttributes {
  system: string | null;
  request_model: string | null;
  response_model: string | null;
  response_id: string | null;
  operation: string | null;
  usage: TokenUsage;
  cost: Cost;
  /** Metro pedido por la llamada: `standard`, `batch`, `fast`… */
  billing_tier: string;
  billing_region: string;
  input_messages: Record<string, unknown>[];
  output_messages: Record<string, unknown>[];
  params: Record<string, unknown>;
  finish_reasons: string[];
}

export interface ToolAttributes {
  name: string | null;
  call_id: string | null;
  description: string | null;
  arguments: unknown;
  output: unknown;
}

export interface RetrievalAttributes {
  query: string | null;
  top_k: number | null;
  documents: Record<string, unknown>[];
}

export interface SpanEvent {
  name: string;
  timestamp: string;
  attributes: Record<string, unknown>;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  project_id: string;
  name: string;
  type: SpanType;
  status: SpanStatus;
  status_message: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  llm: LLMAttributes | null;
  tool: ToolAttributes | null;
  retrieval: RetrievalAttributes | null;
  input: unknown;
  output: unknown;
  session_id: string | null;
  user_id: string | null;
  tags: string[];
  metadata: Record<string, unknown>;
  dedup_hash: string;
  events: SpanEvent[];
  attributes: Record<string, unknown>;
}

export interface Rollup {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  span_count: number;
  error_count: number;
}

export interface TraceTreeNode {
  span: Span;
  subtree: Rollup;
  children: TraceTreeNode[];
  /** Spans de esta traza que comparten dedup_hash. Mayor que 1 = repetición. */
  repeat_count: number;
}

export interface TraceSummary {
  trace_id: string;
  project_id: string;
  root_name: string;
  status: SpanStatus;
  start_time: string;
  end_time: string;
  duration_ms: number;
  span_count: number;
  error_count: number;
  llm_call_count: number;
  tool_call_count: number;
  /** Pasos cuyo modelo no está en la tabla: si es > 0, el coste está incompleto. */
  unknown_cost_spans: number;
  /** Pasos cobrados a tarifa estándar sin poder confirmar qué metro aplicó. */
  assumed_rate_spans: number;
  /** Modelos distintos usados en la traza. Sólo se pinta en modo avanzado. */
  models: string[];
  usage: TokenUsage;
  cost: Cost;
  session_id: string | null;
  user_id: string | null;
}

/** Hueco reservado: se rellena en la Fase 3 (diagnóstico automático). */
export interface Diagnosis {
  trace_id: string;
  project_id: string;
  created_at: string;
  model: string;
  cause: string;
  explanation: string;
  suggestion: string;
  categories: string[];
  confidence: number | null;
  estimated_savings_usd: number | null;
}

/** Hueco reservado: se rellena en la Fase 4 (evaluación). */
export interface Annotation {
  id: string;
  trace_id: string;
  span_id: string | null;
  source: "human" | "llm_judge";
  verdict: "pass" | "fail" | "unknown";
  score: number | null;
  label: string | null;
  comment: string | null;
  author: string | null;
  created_at: string;
}

export interface Trace {
  summary: TraceSummary;
  roots: TraceTreeNode[];
  diagnosis: Diagnosis | null;
  annotations: Annotation[];
}

export interface TraceListPage {
  traces: TraceSummary[];
  /** `null` cuando no hay más, o cuando el orden pedido no es paginable de forma estable. */
  next_cursor: string | null;
  /** De las trazas de esta página, las que repiten algún paso con la misma entrada. */
  with_repeats: string[];
}

export interface ProjectStats {
  id: string;
  trace_count: number;
  span_count: number;
  total_cost_usd: number;
  last_seen: string | null;
}

// ---------------------------------------------------------------------------------
// Motor de detección (Fase 2). Espejo de apps/backend/laplace_backend/insights.py.
// No forma parte del contrato de traza: son modelos de la API de diagnóstico.
// ---------------------------------------------------------------------------------

export type FindingKind = "repeticion" | "modelo_caro" | "contexto_fijo";
export type Difficulty = "easy" | "mid" | "hard";

export interface TechItem {
  label: string;
  value: string;
}

export interface FixStep {
  title: string;
  body: string;
  code: string | null;
  /** Sólo se muestra en modo avanzado. */
  advanced: boolean;
}

export interface Finding {
  id: string;
  kind: FindingKind;
  title: string;
  summary: string;
  window_waste_usd: number;
  monthly_saving_usd: number;
  currency: string;
  window_waste_ms: number;
  difficulty: Difficulty;
  difficulty_label: string;
  scope_label: string;
  /** false = cuesta tiempo, no dinero. La tarjeta lo dice sin disimular. */
  costs_money: boolean;
  tech: TechItem[];
  sample_trace_id: string;
}

export interface FindingDetail extends Finding {
  what_happens: string;
  why: string;
  detection_explanation: string;
  /** La consulta que se ejecutó de verdad, no una copia a mano. */
  detection_query: string;
  fix_steps: FixStep[];
  savings_calculation: string;
  savings_note: string;
  evidence: Span[];
}

export interface Overview {
  project_id: string;
  days: number;
  currency: string;
  window_cost_usd: number;
  monthly_cost_usd: number;
  monthly_avoidable_usd: number;
  monthly_necessary_usd: number;
  traces: number;
  spans: number;
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  error_rate: number;
  p95_duration_ms: number;
  cost_per_trace_usd: number;
  /** Mientras no sea cero, el coste mostrado está incompleto. */
  unknown_cost_spans: number;
  models_without_price: string[];
  /** Pasos cobrados a tarifa estándar sin poder confirmar el metro. */
  assumed_rate_spans: number;
  /** Lo que la caché ya ha ahorrado en la ventana. Medido, no proyectado. */
  window_cache_saving_usd: number;
  /** Días de datos reales sobre los que se proyecta el mes. */
  observed_days: number;
  /** La proyección sale de menos de 24 h de datos. */
  thin_projection: boolean;
  /** El evitable pasa del umbral de cautela: presentarlo con reservas. */
  savings_needs_caution: boolean;
  findings: Finding[];
}
