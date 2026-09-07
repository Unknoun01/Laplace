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

    `input_tokens` es el **total facturable de entrada**: los tokens leídos de caché y
    los escritos en caché van dentro de esa cifra, y los campos de caché dicen qué
    parte del total fue cada cosa. Los proveedores no coinciden en esto (OpenAI incluye
    los cacheados en `prompt_tokens`, Anthropic los devuelve aparte), así que la
    normalización la hace cada integración del SDK y aquí llega ya en un solo criterio
    (D-050). Sin eso, el mismo agente costaría distinto según el proveedor.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    #: Tokens de entrada servidos desde caché (cache hit). Subconjunto de `input_tokens`.
    cached_input_tokens: int = 0
    #: Tokens escritos en caché de duración corta. Subconjunto de `input_tokens`.
    cache_write_tokens: int = 0
    #: Tokens escritos en caché de larga duración. Subconjunto de `input_tokens`.
    cache_write_1h_tokens: int = 0
    reasoning_tokens: int = 0
    #: True cuando los tokens los ha contado el SDK porque el proveedor no los dio
    #: (pasa en streaming sin `include_usage`). El coste derivado es una aproximación
    #: y la interfaz lo dice: no es lo mismo que un recuento del proveedor.
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int:
        """La entrada que se paga a tarifa completa."""
        return max(
            0,
            self.input_tokens
            - self.cached_input_tokens
            - self.cache_write_tokens
            - self.cache_write_1h_tokens,
        )


class Cost(_Model):
    """Coste de un span, desglosado por entrada y salida.

    `unknown=True` significa que **no sabemos** cuánto cuesta: el modelo no está en la
    tabla de precios. No es cero. Un cero silencioso se suma a los totales y los
    corrompe sin que nadie se entere, así que la interfaz tiene que decirlo.
    """

    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    #: Parte de `input_usd` que se fue en leer y en escribir caché.
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    #: Lo que la caché ya ha ahorrado en este span frente a pagar esa entrada entera a
    #: tarifa entera. Es dinero medido: no es una promesa ni una proyección.
    cache_saving_usd: float = 0.0
    unknown: bool = False
    #: True cuando no se ha podido saber qué metro de facturación aplicó (contexto
    #: largo, residencia de datos, modo rápido) y se ha cobrado el estándar. El coste
    #: podría ser mayor; `rate_note` dice por qué.
    rate_assumed: bool = False
    rate_note: str = ""
    #: Tarifa aplicada, para auditarla: `<modelo de la tabla> @ <versión de la tabla>`.
    rate: str = ""
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

    #: Metro de facturación pedido por quien hizo la llamada: `standard`, `batch` o
    #: `fast`. Es texto libre a propósito: los proveedores tienen niveles propios
    #: (`flex`, `scale`…) y guardarlos tal cual permite decir «no tenemos tarifa para
    #: esto» en vez de fingir que era el estándar. Ausente = estándar, que es el
    #: defecto real de los dos proveedores.
    billing_tier: str = "standard"
    #: `regional` cuando la petición pide residencia de datos, que lleva recargo.
    billing_region: str = "global"

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
    #: Pasos cuyo modelo no está en la tabla de precios. Si es > 0, el coste de esta
    #: traza está incompleto y hay que decirlo, no redondear a la baja en silencio.
    unknown_cost_spans: int = 0
    #: Pasos cobrados a tarifa estándar sin poder confirmar qué metro aplicó. El coste
    #: real podría ser mayor; se dice en modo avanzado.
    assumed_rate_spans: int = 0
    #: Modelos distintos usados en la traza. La lista sólo se pinta en modo avanzado,
    #: pero se sirve siempre: es una propiedad de la traza, no de la pantalla.
    models: list[str] = Field(default_factory=list)

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
