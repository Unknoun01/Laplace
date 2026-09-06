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
  cached_input_tokens: number;
  reasoning_tokens: number;
}

export interface Cost {
  input_usd: number;
  output_usd: number;
  total_usd: number;
  /** El modelo no estaba en la tabla de precios: el número es una aproximación. */
  estimated: boolean;
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
  next_cursor: string | null;
}

export interface ProjectStats {
  id: string;
  trace_count: number;
  span_count: number;
  total_cost_usd: number;
  last_seen: string | null;
}
