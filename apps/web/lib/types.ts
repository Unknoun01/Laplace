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
  /**
   * La tarifa sale de la tabla comunitaria de LiteLLM, no de la página del proveedor
   * (D-138). No es un suelo: puede quedarse corta o pasarse.
   */
  rate_unverified: boolean;
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
  /**
   * Identidad del paso: mismo sitio de llamada y mismas instrucciones. Es por lo que
   * agrupan las reglas, y por lo que se busca la ficha de un problema.
   */
  step_key: string;
  step_label: string;
  step_hint: string;
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
  /** La entrada del span raíz, en una línea (D-125). */
  input_preview?: string;
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

export type AnnotationSource = "human" | "llm_judge";
export type AnnotationVerdict = "pass" | "fail" | "unknown";

/** Veredicto sobre una traza. La fuente decide todo lo demás (D-083). */
export interface Annotation {
  id: string;
  trace_id: string;
  span_id: string | null;
  source: AnnotationSource;
  verdict: AnnotationVerdict;
  score: number | null;
  label: string | null;
  comment: string | null;
  author: string | null;
  created_at: string;
  /** Presente SÓLO cuando `source` es `llm_judge`. En una humana es `null`. */
  judge: JudgeRun | null;
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

export type FindingKind = "repeticion" | "modelo_caro" | "contexto_fijo" | "bucle";
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
  /** Una frase para la tarjeta del inicio (D-124). */
  lead: string;
  window_waste_usd: number;
  /** Proyección a 30 días, o `null` cuando no hay días suficientes para proyectar. */
  monthly_saving_usd: number | null;
  /** Días de datos reales detrás de `window_waste_usd`. */
  observed_days: number;
  currency: string;
  window_waste_ms: number;
  /** Tokens de más, medidos. Existen aunque el modelo no tenga tarifa. */
  window_waste_tokens: number;
  /** Por qué no hay cifra en dólares en este hallazgo, cuando no la hay. */
  cost_unavailable: string;
  /** La cifra es un SUELO: hay pasos sin tarifa o cobrados a tarifa asumida. */
  cost_is_floor: boolean;
  unknown_cost_spans: number;
  assumed_rate_spans: number;
  /** La cifra usa tarifas de LiteLLM sin verificar (D-138). No es un suelo. */
  cost_unverified: boolean;
  unverified_rate_models: string[];
  difficulty: Difficulty;
  difficulty_label: string;
  scope_label: string;
  /** false = cuesta tiempo, no dinero. La tarjeta lo dice sin disimular. */
  costs_money: boolean;
  tech: TechItem[];
  sample_trace_id: string;
  /** Identidad exacta del paso: con ella se filtran «las trazas afectadas». */
  step_key: string;
  /** Lo que el usuario ha dicho de él (D-123). Vacío si nada. */
  /** `desaparecido`: dejó de ocurrir dentro del rango sin que nadie lo marcara (D-135). */
  state: "" | "arreglado" | "ignorado" | "reaparecido" | "desaparecido";
  state_at: string | null;
  /** Última vez que ocurrió en el rango. */
  last_seen: string | null;
  state_note: string;
  fix_check: FixCheck | null;
}

/** Antes y después de marcar un hallazgo como arreglado, por ejecución (D-123). */
export interface FixCheck {
  marked_at: string;
  unit: "usd" | "tokens" | "ms";
  runs_before: number;
  runs_after: number;
  before_per_run: number | null;
  after_per_run: number | null;
  saved: number | null;
  verdict: "pendiente" | "arreglado" | "mejor" | "sigue";
  headline: string;
  cost_is_floor: boolean;
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

/**
 * Cobertura: cuánto de tu agente entendemos (D-096).
 *
 * Existe por el peor fallo posible de este producto, que es indistinguible del éxito:
 * cuando no entendemos las llamadas, las reglas se callan y «no estás tirando dinero»
 * se lee como una buena noticia.
 */
export interface CoverageSignal {
  key: "pasos" | "tarifa" | "tokens" | "prompts";
  label: string;
  /** `null` mientras no haya llamadas suficientes. Nunca cero por defecto. */
  value: number | null;
  counted: number;
  total: number;
  level: "sin-base" | "bien" | "flojo" | "malo" | "no-aplica";
  /** Qué significa para las cifras de abajo que esta señal esté baja. */
  consequence: string;
  /** Qué hacer para subirla, en concreto. */
  fix: string;
  unavailable: string;
}

export interface Coverage {
  llm_calls: number;
  signals: CoverageSignal[];
  level: "sin-base" | "bien" | "flojo" | "malo" | "no-aplica";
  headline: string;
  detail: string;
  /** True cuando esto va **antes** que el dinero, no después. */
  prominent: boolean;
  /** Pasos cuya identidad se parte en casi tantas versiones como ejecuciones. */
  split_steps: string[];
}

/** Un tramo del gráfico del Diagnóstico. `avoidable_usd` es un reparto, no una medida. */
export interface TramoGasto {
  start: string;
  cost_usd: number;
  avoidable_usd: number;
}

export interface GastoPaso {
  key: string;
  name: string;
  cost_usd: number;
  avoidable_usd: number;
}

/** Espejo de `insights.modelos.Grafico` (D-152). */
export interface Grafico {
  bucket_minutes: number;
  buckets: TramoGasto[];
  steps: GastoPaso[];
  other_steps_usd: number;
  unattributed_usd: number;
}

export interface Overview {
  project_id: string;
  days: number;
  currency: string;
  /**
   * Por qué no se puede poner precio, cuando ninguna llamada tiene tarifa. Si trae
   * texto, la pantalla enseña **esto** en lugar de la cifra grande: un «$0» enorme con
   * el aviso debajo se lee como «no cuesta nada» (D-073, D-107).
   */
  cost_unavailable: string;
  window_cost_usd: number;
  /** Evitable y necesario DENTRO de la ventana: dinero medido, existe siempre. */
  window_avoidable_usd: number;
  window_necessary_usd: number;
  /** Las tres son `null` a la vez cuando no hay base para proyectar (D-073). */
  monthly_cost_usd: number | null;
  monthly_avoidable_usd: number | null;
  monthly_necessary_usd: number | null;
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
  /** Hay días suficientes y los `monthly_*` traen cifra. */
  projected: boolean;
  /** Días de datos que hacen falta para poder proyectar. */
  min_days_for_projection: number;
  /** Qué parte del gasto observado es evitable, entre 0 y 1. */
  avoidable_ratio: number;
  /** El evitable pasa del umbral de cautela: presentarlo con reservas. */
  savings_needs_caution: boolean;
  findings: Finding[];
  /** Cuánto de este proyecto entendemos. Va delante del dinero si es baja. */
  coverage: Coverage | null;
  /** Dónde se va el dinero (D-152). `null` sin gasto que dibujar. */
  chart: Grafico | null;
  /** Arreglados o ignorados: no suman al evitable (D-123). */
  set_aside: Finding[];
}

// ---------------------------------------------------------------------------------
// Panel (Fase 4). Espejo de apps/backend/laplace_backend/panel.py.
// ---------------------------------------------------------------------------------

export type Verdict = "sin-base" | "estable" | "normal" | "revisar" | "mejora" | "mixto";

export interface Metric {
  label: string;
  /** `null` cuando no hay base para calcularla. Nunca cero por defecto (D-073). */
  value: number | null;
  previous: number | null;
  /** 0,25 = 25 % más. `null` si falta un lado o si el anterior era cero. */
  change_ratio: number | null;
  /** `ratio` es una proporción: 0,25 = 25 %. */
  unit: "money" | "tokens" | "count" | "duration" | "ratio" | string;
  /** Por qué no hay cifra. Vacío cuando la hay. */
  unavailable: string;
}

/** La lectura del panel en palabras: la pieza que justifica el panel entero. */
export interface Reading {
  verdict: Verdict;
  headline: string;
  detail: string;
}

export interface SpikeCause {
  /**
   * `version_prompt` era el hueco reservado desde que se escribió la atribución. Entra
   * por la misma puerta que las demás: una versión que aparece en las trazas del pico y
   * no aparecía antes, nunca la hora de un despliegue (D-092).
   */
  kind:
    | "version_prompt"
    | "modelo_nuevo"
    | "herramienta_nueva"
    | "paso_nuevo"
    | "paso_disparado"
    | "volumen";
  text: string;
  evidence: string;
  /** Adónde ir a mirar. Hoy sólo lo lleva la versión de prompt. */
  link: Record<string, string>;
}

export interface Spike {
  start: string;
  end: string;
  traces: number;
  cost_usd: number;
  cost_per_trace_usd: number;
  times_baseline: number;
  baseline_cost_per_trace_usd: number;
  excess_usd: number;
  /** El sobrecoste es un SUELO: hay llamadas sin tarifa en la ventana. */
  cost_is_floor: boolean;
  causes: SpikeCause[];
  /** Vacío cuando hay causas. Cuando no, dice justo eso y no otra cosa. */
  unattributed: string;
  /** Filtro listo para el explorador: las trazas responsables del pico. */
  traces_query: Record<string, string>;
}

export interface PanelBucket {
  start: string;
  traces: number;
  cost_usd: number;
  /** `null` en un tramo sin ejecuciones: no es cero, es que no hay nada que dividir. */
  cost_per_trace_usd: number | null;
  /** Por qué el coste de este punto no se puede afirmar. Vacío cuando sí se puede. */
  cost_unavailable: string;
  tokens_per_trace: number | null;
  steps_per_trace: number | null;
  duration_ms_per_trace: number | null;
  is_spike: boolean;
}

export interface Panel {
  project_id: string;
  days: number;
  currency: string;
  observed_days: number;
  bucket_minutes: number;
  /** Hay un periodo anterior utilizable con el que comparar. */
  has_previous: boolean;
  /** Por qué no lo hay. Se enseña en vez de pintar variaciones vacías. */
  comparison_unavailable: string;
  /** Las protagonistas, todas por ejecución. */
  per_execution: Metric[];
  /** Contexto secundario: un total que sube no es noticia por sí solo. */
  totals: Metric[];
  reading: Reading;
  buckets: PanelBucket[];
  spikes: Spike[];
  spikes_unavailable: string;
}

// ---------------------------------------------------------------------------------
// Evaluación (Fase 5). Espejo de laplace/schema.py y de laplace_backend/evals.py.
// ---------------------------------------------------------------------------------

/**
 * Lo que costó emitir un veredicto de máquina.
 *
 * Va DENTRO de la anotación y sólo lo lleva la de máquina: en una anotación humana
 * este campo es `null` y no puede ser otra cosa. La separación entre persona y modelo
 * es estructural, no una etiqueta que se pueda olvidar de pintar (D-083).
 */
export interface JudgeRun {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  /** El modelo del juez no está en la tabla de precios: su coste es «no lo sabemos». */
  cost_unknown: boolean;
  prompt_version: string;
}

export interface DatasetItem {
  id: string;
  dataset_id: string;
  /** La traza real de la que salió el caso. Sin esto sería un caso inventado. */
  trace_id: string;
  span_id: string | null;
  input: unknown;
  expected: unknown;
  created_at: string;
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  description: string;
  created_at: string;
  item_count: number;
  /** El filtro del explorador con el que se materializó. Se enseña en avanzado. */
  source_filter: Record<string, string>;
}

export interface EvalRunItem {
  case_id: string;
  trace_id: string;
  /** La ejecución del agente reventó. Cuenta como fallo, no se descarta. */
  failed: boolean;
  error: string;
}

export interface EvalRun {
  id: string;
  project_id: string;
  dataset_id: string;
  variant: string;
  created_at: string;
  items: EvalRunItem[];
  notes: string;
}

/** Una proporción de acierto con su guarda puesta (D-087). */
export interface Rate {
  source: AnnotationSource;
  judged: number;
  passed: number;
  failed: number;
  /** Casos sin veredicto de esta fuente. No cuentan como fallo. */
  unjudged: number;
  /** `null` mientras no haya casos suficientes. Nunca cero por defecto. */
  value: number | null;
  low: number | null;
  high: number | null;
  unavailable: string;
}

export interface VariantSide {
  run_id: string;
  variant: string;
  cases: number;
  crashed: number;
  /** Una entrada por fuente. Nunca se funden en una sola cifra. */
  rates: Rate[];
  cost_usd: number;
  cost_per_case_usd: number | null;
  input_tokens: number;
  output_tokens: number;
  duration_ms_per_case: number | null;
  cost_is_floor: boolean;
  /** Lo que costó juzgar. Va aparte del coste del agente, a propósito. */
  judge_cost_usd: number;
  judge_cost_unknown: boolean;
  /**
   * Con qué versiones de prompt corrió este lado. Sale de las trazas y no de la
   * etiqueta de la tirada: `variant` es lo que alguien tecleó, y se queda viejo (D-094).
   */
  prompt_versions: string[];
}

export type EvalVerdict = "sin-base" | "mejor" | "peor" | "empate";

export interface SourceComparison {
  source: AnnotationSource;
  verdict: EvalVerdict;
  headline: string;
  detail: string;
  a: Rate;
  b: Rate;
}

export interface Comparison {
  project_id: string;
  dataset_id: string;
  dataset_name: string;
  cases: number;
  a: VariantSide;
  b: VariantSide;
  by_source: SourceComparison[];
  /** Personas y juez no dicen lo mismo. Es información, no un fallo. */
  sources_disagree: boolean;
  cost_change: number | null;
  headline: string;
  detail: string;
}

export interface RunSummary {
  run_id: string;
  variant: string;
  dataset_id: string;
  dataset_name: string;
  created_at: string;
  cases: number;
  cost_usd: number;
  judge_cost_usd: number;
  /** Llamadas de la tirada sin tarifa conocida: el coste de arriba es un suelo. */
  unknown_cost_spans: number;
  rates: Rate[];
  prompt_versions: string[];
}

export interface JudgeStatus {
  enabled: boolean;
  system: string;
  model: string;
  max_batch: number;
  detail: string;
}

// ---------------------------------------------------------------------------------
// Prompts (Fase 6). Espejo de laplace_backend/prompts.py.
// ---------------------------------------------------------------------------------

export interface PromptDeploy {
  id: string;
  prompt_id: string;
  version: number;
  at: string;
  actor: string;
  note: string;
  /** Se volvió a una versión anterior a la que estaba. */
  rollback: boolean;
}

/**
 * Una versión con lo que costó y lo que acertó sobre el tráfico que la usó.
 *
 * `cost_per_execution_usd` es `null` —nunca cero— cuando no tuvo tráfico en el rango.
 * Cero significaría «no cuesta nada», que es otra afirmación y normalmente falsa.
 */
export interface VersionMetrics {
  version: number;
  /** `v8`, o `reserva` para el tráfico que corrió con el texto del código. */
  label: string;
  created_at: string | null;
  author: string;
  notes: string;
  in_production: boolean;
  /** Sólo viaja en la ficha de un prompt, no en la lista. */
  text: string;
  traces: number;
  calls: number;
  cost_usd: number;
  cost_per_execution_usd: number | null;
  tokens_per_execution: number | null;
  cost_unavailable: string;
  cost_is_floor: boolean;
  first_seen: string | null;
  last_seen: string | null;
  /** Una por fuente de veredicto. Nunca se funden (D-083). */
  rates: Rate[];
  headline: string;
}

export interface PromptComparison {
  a_version: number;
  b_version: number;
  cost_change: number | null;
  cost_unavailable: string;
  by_source: SourceComparison[];
  headline: string;
  detail: string;
}

export interface PromptCard {
  id: string;
  name: string;
  description: string;
  created_at: string;
  production_version: number | null;
  version_count: number;
  versions: VersionMetrics[];
  deploys: PromptDeploy[];
  comparison: PromptComparison | null;
  comparison_unavailable: string;
  /** Ejecuciones que corrieron con el texto de reserva porque Laplace no respondía. */
  fallback_traces: number;
}

export interface ObservedVariant {
  step_key: string;
  hint: string;
  traces: number;
  calls: number;
  cost_usd: number;
  cost_per_execution_usd: number | null;
  first_seen: string | null;
  last_seen: string | null;
  rates: Rate[];
}

/** Un paso y los juegos de instrucciones con los que se le ha visto en las trazas. */
export interface ObservedStep {
  label: string;
  variants: ObservedVariant[];
  traces: number;
  /** Las instrucciones cambian casi por ejecución: son plantilla, no versiones. */
  unstable: boolean;
  /** Las huellas corren juntas en la misma ejecución: llamadas distintas, no versiones. */
  concurrent: boolean;
  note: string;
}

export interface PromptsView {
  project_id: string;
  days: number;
  /** Hay prompts gestionados. Si no, la pestaña enseña lo inferido de las trazas. */
  managed: boolean;
  prompts: PromptCard[];
  observed: ObservedStep[];
  observed_unavailable: string;
}

export interface DiffLine {
  op: "=" | "+" | "-";
  text: string;
  left: number | null;
  right: number | null;
}

export interface Diff {
  lines: DiffLine[];
  added: number;
  removed: number;
  unchanged: number;
  summary: string;
}

// ---------------------------------------------------------------------------------
// Ajustes del proyecto (D-123)
// ---------------------------------------------------------------------------------

export interface Budget {
  project_id: string;
  monthly_usd: number | null;
  month: string;
  month_to_date_usd: number;
  projected_usd: number | null;
  ratio: number | null;
  status: "sin-presupuesto" | "bien" | "cerca" | "va-a-pasarse" | "pasado";
  headline: string;
  cost_is_floor: boolean;
  unknown_cost_spans: number;
}

export interface AlertSettings {
  project_id: string;
  enabled: boolean;
  /** Sólo el host: la URL entera es un secreto y no sale nunca por la API. */
  slack: string;
  webhook: string;
  email_to: string;
  email_ready: boolean;
  min_usd: number;
  quiet_hours: number;
  window_days: number;
  muted: boolean;
  muted_kinds: string[];
  env_enabled: boolean;
}

export interface CostGroup {
  key: string;
  traces: number;
  cost_usd: number;
  cost_per_trace_usd: number;
  tokens: number;
  unknown_cost_spans: number;
}

export interface Breakdown {
  project_id: string;
  by: "user" | "session";
  groups: CostGroup[];
  untagged_traces: number;
  untagged_cost_usd: number;
  total_cost_usd: number;
  unknown_cost_spans: number;
}

export interface CustomPrices {
  models: Record<string, { input: number; output: number; cached_input?: number }>;
  unpriced: string[];
  editable: boolean;
}

export interface Instance {
  local: boolean;
  retention_days: number;
  email_ready: boolean;
  alerts_env_enabled: boolean;
  operator: boolean;
}
