"""Interfaz de almacenamiento.

La implementación de esta fase es ClickHouse. El modo local con SQLite (Fase 1,
punto 7) entrará por esta misma interfaz sin tocar la API ni el frontend (D-015).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from laplace.schema import Span, TraceSummary


@dataclass
class TraceFilter:
    """Filtros de la lista de trazas."""

    project_id: str | None = None
    limit: int = 50
    #: Cursor de paginación, en dos partes. Sólo por fecha no basta: dos agentes
    #: lanzados a la vez empiezan en el mismo instante y una de las trazas se perdería
    #: entre páginas. El desempate es el `trace_id`.
    before: datetime | None = None
    before_trace_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    #: `error` para ver sólo lo que falló.
    status: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    #: Busca en el nombre de los spans y en el id de la traza.
    search: str | None = None
    span_type: str | None = None
    #: Filtros que sólo ofrece el modo avanzado del explorador.
    model: str | None = None
    min_cost_usd: float | None = None
    #: `recent` (por defecto), `cost` o `duration`. El cursor sólo tiene sentido con
    #: `recent`: es el único orden estable frente a datos que siguen llegando.
    sort: str = "recent"


def encode_cursor(summary: TraceSummary) -> str:
    """Cursor opaco para el cliente: instante de inicio + `trace_id` de desempate."""
    return f"{summary.start_time.isoformat()}|{summary.trace_id}"


def decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    """Inverso de `encode_cursor`. Un cursor ilegible se ignora, no rompe la página."""
    if not cursor:
        return None, None
    timestamp, _, trace_id = cursor.partition("|")
    try:
        return datetime.fromisoformat(timestamp), trace_id or None
    except ValueError:
        return None, None


@dataclass
class Window:
    """Ventana temporal de análisis. Todas las pantallas comparten una."""

    since: datetime
    until: datetime
    days: int


@dataclass
class WindowSummary:
    """Lo que ha pasado en el proyecto durante la ventana."""

    traces: int = 0
    spans: int = 0
    error_traces: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    #: Latencia de traza, no de span: es la que sufre el usuario final.
    p95_duration_ms: float = 0.0
    #: Pasos cuyo modelo no está en la tabla de precios, y los modelos implicados.
    #: Mientras esto no sea cero, el coste del proyecto está incompleto.
    unknown_cost_spans: int = 0
    #: Pasos cobrados a tarifa estandar sin poder confirmar que metro aplico.
    assumed_rate_spans: int = 0
    models_without_price: list[str] = field(default_factory=list)
    #: Lo que la cache ya ha ahorrado en la ventana. Dinero medido, no proyectado.
    cache_saving_usd: float = 0.0
    #: Primer y último span vistos en la ventana. Sirven para saber sobre cuántos días
    #: de datos reales se está proyectando, que no son los que pida el selector.
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class RepeatedGroup:
    """Un mismo paso repetido dentro de una misma traza (contrato §5).

    `extra_*` es lo que sobra: todo menos la primera ocurrencia de cada traza.
    """

    #: Entrada repetida representativa del paso: la que más veces se repite. Sirve
    #: para enseñar evidencia; el hallazgo es del paso entero, no de esta entrada.
    dedup_hash: str
    name: str
    span_type: str
    model: str
    #: Paso al que pertenece la repetición. Sin él, el descuento que impide contar dos
    #: veces el mismo ahorro no encuentra su pareja en `ModelUsage` (D-061).
    step_key: str = ""
    #: Trozo de las instrucciones, para distinguir dos pasos con el mismo título.
    hint: str = ""
    traces: int = 0
    total_spans: int = 0
    extra_spans: int = 0
    extra_cost_usd: float = 0.0
    extra_duration_ms: float = 0.0
    #: Tokens de las ocurrencias sobrantes. Sirven para que la regla del modelo caro
    #: no vuelva a contar lo que ya cuenta la regla de repetición.
    extra_input_tokens: int = 0
    extra_output_tokens: int = 0
    max_per_trace: int = 0
    sample_trace_id: str = ""


@dataclass
class ModelUsage:
    """Uso agregado de un modelo por paso, para razonar sobre alternativas.

    El paso es `key`, no `name`: `name` es sólo cómo se llama en la pantalla (D-060).
    """

    key: str
    name: str
    model: str
    #: Trozo de las instrucciones fijas, para distinguir dos pasos homónimos.
    hint: str = ""
    calls: int = 0
    traces: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    #: Tokens escritos en cache (las dos duraciones juntas). Si esto es cero y la
    #: entrada minima es alta, el paso esta reenviando su contexto fijo sin cachearlo.
    cache_write_tokens: int = 0
    #: Lo que la cache ya ha ahorrado en este (paso, modelo).
    cache_saving_usd: float = 0.0
    cost_usd: float = 0.0
    avg_output_tokens: float = 0.0
    avg_input_tokens: float = 0.0
    #: Suelo de tokens de entrada: aproxima la parte fija del prompt que se reenvía.
    min_input_tokens: int = 0
    sample_trace_id: str = ""


@dataclass
class ProjectStats:
    project_id: str
    trace_count: int = 0
    span_count: int = 0
    total_cost_usd: float = 0.0
    last_seen: datetime | None = None


@dataclass
class TracePage:
    traces: list[TraceSummary] = field(default_factory=list)
    next_cursor: str | None = None


def disambiguate(filas: list[Any]) -> list[Any]:
    """Dos pasos llamados desde el mismo sitio necesitan títulos distintos.

    Vive aquí, no en un almacén concreto, porque el criterio tiene que ser el mismo en
    los dos: un hallazgo no puede titularse distinto según dónde estén las filas. Pasa
    siempre que alguien no decora sus funciones internas, que es el caso normal
    (D-060): todas sus llamadas cuelgan del mismo span, así que comparten la etiqueta.
    Se les añade el principio de sus instrucciones, que es lo que de verdad las separa.
    """
    repetidas = {f.name for f in filas if sum(1 for g in filas if g.name == f.name) > 1}
    if not repetidas:
        return filas
    for fila in filas:
        pista = getattr(fila, "hint", "")
        if fila.name in repetidas and pista and pista != fila.name:
            fila.name = f"{fila.name} — «{pista}»"
    return filas


@runtime_checkable
class SpanStore(Protocol):
    """Lo que el backend necesita de un almacén de trazas."""

    @property
    def repeated_groups_sql(self) -> str:
        """La consulta que detecta repeticiones, tal cual se ejecuta.

        La interfaz enseña al usuario **la consulta que de verdad se ha ejecutado** en
        «cómo lo hemos detectado». Como cada almacén tiene la suya —SQLite no tiene
        `uniqExact` ni `argMax`—, la pide aquí en vez de importar la de ClickHouse, que
        es lo que hacía antes y habría mentido en modo local (D-067).
        """

    @property
    def model_usage_sql(self) -> str:
        """La consulta que agrega el uso por paso, tal cual se ejecuta."""

    def migrate(self) -> None:
        """Crea el esquema si no existe. Idempotente."""

    def insert_spans(self, spans: list[Span]) -> int:
        """Escribe spans. Reescribir el mismo `span_id` no debe duplicar coste."""

    def list_traces(self, filters: TraceFilter) -> TracePage:
        """Una página de la lista de trazas, más reciente primero."""

    def get_trace_spans(self, trace_id: str, project_id: str | None = None) -> list[Span]:
        """Todos los spans de una traza, ordenados por inicio."""

    def list_projects(self) -> list[ProjectStats]:
        """Proyectos con datos."""

    def summarize_window(self, project_id: str, window: Window) -> WindowSummary:
        """Totales del proyecto en la ventana. Alimenta el héroe."""

    def repeated_groups(
        self, project_id: str, window: Window, *, min_repeats: int = 3, limit: int = 20
    ) -> list[RepeatedGroup]:
        """Pasos repetidos con la misma entrada dentro de una traza."""

    def model_usage(
        self, project_id: str, window: Window, *, min_calls: int = 5, limit: int = 50
    ) -> list[ModelUsage]:
        """Uso por (paso, modelo), para las reglas de modelo caro y contexto fijo."""

    def traces_with_repeats(
        self, project_id: str | None, trace_ids: list[str], *, min_repeats: int = 3
    ) -> set[str]:
        """De esas trazas, cuáles tienen algún paso repetido con la misma entrada."""

    def sample_repetition(
        self, project_id: str, window: Window, dedup_hash: str, limit: int = 40
    ) -> list[Span]:
        """Las ocurrencias repetidas de una traza concreta, como evidencia."""

    def health(self) -> bool:
        """True si el almacén responde."""
