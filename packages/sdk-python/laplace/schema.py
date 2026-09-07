"""Contrato de datos de una traza.

Fuente única de verdad para las tres capas (SDK, backend, web). Documentado en
`docs/trace-contract.md`; si cambias algo aquí, cambia allí y en el esquema de
ClickHouse en el mismo commit.

Este módulo se importa en la ruta de lectura (backend, tests), no en el camino
caliente de emisión de spans, que sólo depende de `laplace.semconv`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SpanType = Literal["agent", "llm", "tool", "retrieval", "chain"]
SpanStatus = Literal["ok", "error", "unset"]
AnnotationSource = Literal["human", "llm_judge"]
AnnotationVerdict = Literal["pass", "fail", "unknown"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ---------------------------------------------------------------------------------
# Uso y coste
# ---------------------------------------------------------------------------------


class TokenUsage(_Model):
    """Tokens de una llamada a un modelo.

    Entrada y salida se guardan **siempre por separado**: sin eso no se puede
    calcular cuánto costaría el mismo paso con otro modelo (Norte B).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Cost(_Model):
    """Coste de un span, desglosado por entrada y salida.

    `estimated=True` significa que el modelo no estaba en la tabla de precios y el
    número es una aproximación (o cero). Nunca se presenta como exacto en la UI.
    """

    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    estimated: bool = False
    currency: Literal["USD"] = "USD"


# ---------------------------------------------------------------------------------
# Atributos por tipo de span
# ---------------------------------------------------------------------------------


class LLMAttributes(_Model):
    """Atributos de un span `llm`.

    Los mensajes se guardan en crudo (lista de dicts tal y como los envió o devolvió
    el proveedor). No se normalizan a un formato propio: perderíamos información que
    el diagnóstico automático necesita.
    """

    system: str | None = None
    request_model: str | None = None
    response_model: str | None = None
    response_id: str | None = None
    operation: str | None = None

    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: Cost = Field(default_factory=Cost)

    input_messages: list[dict[str, Any]] = Field(default_factory=list)
    output_messages: list[dict[str, Any]] = Field(default_factory=list)

    params: dict[str, Any] = Field(default_factory=dict)
    finish_reasons: list[str] = Field(default_factory=list)


class ToolAttributes(_Model):
    """Atributos de un span `tool`."""

    name: str | None = None
    call_id: str | None = None
    description: str | None = None
    arguments: Any = None
    output: Any = None


class RetrievalAttributes(_Model):
    """Atributos de un span `retrieval`."""

    query: str | None = None
    top_k: int | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)


class SpanEvent(_Model):
    """Evento OTel dentro de un span (típicamente una excepción)."""

    name: str
    timestamp: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------------


class Span(_Model):
    """Un paso dentro de una traza."""

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    project_id: str

    name: str
    type: SpanType = "chain"
    status: SpanStatus = "unset"
    status_message: str = ""

    start_time: datetime
    end_time: datetime
    duration_ms: float = 0.0

    llm: LLMAttributes | None = None
    tool: ToolAttributes | None = None
    retrieval: RetrievalAttributes | None = None

    # Entrada/salida genéricas para spans `agent` y `chain`.
    input: Any = None
    output: Any = None

    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    #: Hash estable de (tipo, nombre, modelo, entrada normalizada). Dos spans con el
    #: mismo valor dentro de una traza son la misma llamada repetida. Lo calcula la
    #: ingesta, no el SDK (D-007).
    dedup_hash: str = ""

    events: list[SpanEvent] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def cost(self) -> Cost:
        """Coste propio del span (0 para todo lo que no sea una llamada a un modelo)."""
        return self.llm.cost if self.llm else Cost()

    @property
    def usage(self) -> TokenUsage:
        return self.llm.usage if self.llm else TokenUsage()


# ---------------------------------------------------------------------------------
# Árbol y resumen
# ---------------------------------------------------------------------------------


class Rollup(_Model):
    """Totales acumulados de un subárbol. Se calcula al servir, no se almacena."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    span_count: int = 0
    error_count: int = 0


class TraceTreeNode(_Model):
    """Nodo del árbol de una traza."""

    span: Span
    subtree: Rollup = Field(default_factory=Rollup)
    children: list[TraceTreeNode] = Field(default_factory=list)

    #: Nº de spans de esta traza que comparten `dedup_hash` con éste. >1 = repetición.
    repeat_count: int = 1


class TraceSummary(_Model):
    """Una fila de la lista de trazas. Derivada de los spans (ver contrato §6)."""

    trace_id: str
    project_id: str
    root_name: str = ""
    status: SpanStatus = "ok"

    start_time: datetime
    end_time: datetime
    duration_ms: float = 0.0

    span_count: int = 0
    error_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0

    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: Cost = Field(default_factory=Cost)

    session_id: str | None = None
    user_id: str | None = None


# ---------------------------------------------------------------------------------
# Huecos reservados: evaluación (Fase 4) y diagnóstico (Fase 3)
# ---------------------------------------------------------------------------------


class Annotation(_Model):
    """Veredicto humano o de LLM-as-judge sobre una traza o un span concreto.

    Reservado para la Fase 4. Existe ya en el contrato y en el esquema de Postgres
    para no tener que migrar después.
    """

    id: str
    trace_id: str
    span_id: str | None = None
    source: AnnotationSource = "human"
    verdict: AnnotationVerdict = "unknown"
    score: float | None = None
    label: str | None = None
    comment: str | None = None
    author: str | None = None
    created_at: datetime


class Diagnosis(_Model):
    """Causa probable del fallo de una traza y sugerencia de arreglo (Norte A).

    Reservado para la Fase 3: lo rellenará un modelo al que se le pasa la traza
    completa. Un diagnóstico por traza.
    """

    trace_id: str
    project_id: str
    created_at: datetime
    model: str
    cause: str
    explanation: str = ""
    suggestion: str = ""
    categories: list[str] = Field(default_factory=list)
    confidence: float | None = None
    estimated_savings_usd: float | None = None


class Trace(_Model):
    """Una traza completa: resumen + árbol de spans (+ huecos reservados)."""

    summary: TraceSummary
    roots: list[TraceTreeNode] = Field(default_factory=list)

    #: Se rellena en la Fase 3. `None` mientras tanto.
    diagnosis: Diagnosis | None = None
    #: Se rellena en la Fase 4. Lista vacía mientras tanto.
    annotations: list[Annotation] = Field(default_factory=list)


class TraceListPage(_Model):
    """Página de la lista de trazas."""

    traces: list[TraceSummary] = Field(default_factory=list)
    #: Cursor opaco de la siguiente página. `None` cuando no hay más, o cuando el orden
    #: pedido no es paginable de forma estable (por coste, por duración).
    next_cursor: str | None = None
    #: De las trazas de esta página, las que tienen algún paso repetido con la misma
    #: entrada. Es una anotación de la página, no del contrato de la traza: se calcula
    #: al servir y sirve para marcar el bucle en la lista.
    with_repeats: list[str] = Field(default_factory=list)


class Project(_Model):
    """Metadato de proyecto (Postgres)."""

    id: str
    name: str
    created_at: datetime


TraceTreeNode.model_rebuild()
