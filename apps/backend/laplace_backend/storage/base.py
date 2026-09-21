"""Interfaz de almacenamiento.

La implementación de esta fase es ClickHouse. El modo local con SQLite (Fase 1,
punto 7) entrará por esta misma interfaz sin tocar la API ni el frontend (D-015).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
    #: De las ocurrencias sobrantes, cuántas tienen el coste incompleto (modelo sin
    #: tarifa) o cobrado a tarifa asumida. Mientras alguna de las dos no sea cero, el
    #: dinero del hallazgo es un suelo y hay que decirlo antes de afirmar la cifra.
    extra_unknown_cost_spans: int = 0
    extra_assumed_rate_spans: int = 0
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
    #: Llamadas con el coste incompleto o cobrado a tarifa asumida. Igual que en
    #: `RepeatedGroup`: mientras no sean cero, la cifra del hallazgo es un suelo.
    unknown_cost_spans: int = 0
    assumed_rate_spans: int = 0
    avg_output_tokens: float = 0.0
    avg_input_tokens: float = 0.0
    #: Suelo de tokens de entrada: aproxima la parte fija del prompt que se reenvía.
    min_input_tokens: int = 0
    sample_trace_id: str = ""


@dataclass
class Bucket:
    """Un tramo del panel, agregado **por ejecución y no por span**.

    Es la unidad que hace que el panel diga algo: los totales de un tramo suben cuando
    hay más trabajo, y eso no es una noticia. Lo que importa es lo que cuesta cada
    ejecución, y para eso hay que agrupar antes por traza. Por eso `duration_ms_sum` es
    suma de duraciones de traza —de principio a fin de la ejecución— y no de spans, que
    se solapan entre sí y sumarían un número sin significado.
    """

    start: datetime
    traces: int = 0
    spans: int = 0
    llm_calls: int = 0
    error_traces: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms_sum: float = 0.0


@dataclass
class StepFacts:
    """Lo que un paso hizo y costó en un tramo.

    La clave del diccionario que los contiene es el **sitio de llamada** —el camino de
    pasos—, y `label` es cómo se llama en pantalla. Con el nombre como clave, dos
    agentes con una función homónima compartían fila y el pico de uno se atribuía al
    otro (D-106).
    """

    cost_usd: float = 0.0
    calls: int = 0
    label: str = ""


@dataclass
class WindowFacts:
    """Qué se vio en un tramo: modelos, herramientas, pasos y volumen.

    Es la materia prima de la atribución de picos. Se pide dos veces —dentro del pico y
    en lo que va antes— y la causa sale de la diferencia. Nada de correlaciones: o el
    modelo aparece por primera vez en las trazas del pico, o no se dice nada.
    """

    traces: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    steps: dict[str, StepFacts] = field(default_factory=dict)
    #: Versiones de prompt gestionado vistas en las trazas, como `nombre@versión`. Es
    #: un hecho medido —esa versión aparece en estas trazas y no en las de antes—, no
    #: la hora de un despliegue, que sólo sería una coincidencia temporal (D-092).
    prompts: set[str] = field(default_factory=set)


@dataclass
class PromptUsage:
    """Lo que una versión de prompt gestionado costó sobre el tráfico que la usó.

    El coste es el de **las llamadas hechas con esa versión**, y el denominador, las
    ejecuciones en las que aparece. Así «0,004 $ por ejecución» quiere decir lo que
    cuesta este prompt cada vez que el agente trabaja, no lo que cuesta el agente
    entero: una traza puede usar tres prompts y repartirlo entre los tres sería
    inventarse un reparto.
    """

    name: str
    version: int
    traces: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    #: Las marcas de siempre: mientras no sean cero, el coste es un suelo.
    unknown_cost_spans: int = 0
    assumed_rate_spans: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class ObservedPrompt:
    """Un juego de instrucciones visto en las trazas, sin gestión de prompts de por medio.

    Es lo que sostiene la pestaña de Prompts para quien no ha adoptado nada: la
    identidad de un paso ya incluye la huella de sus instrucciones (D-060), así que dos
    `step_key` bajo la misma etiqueta son dos versiones del mismo prompt, con sus fechas
    y su coste. No se puede enseñar el texto entero —sólo se guarda la pista—, pero sí
    cuándo cambió y qué pasó con el coste, que es la mitad de la pregunta.
    """

    step_key: str
    step_label: str
    hint: str = ""
    traces: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class CoverageFacts:
    """Cuántas de las llamadas de un proyecto entiende Laplace, y cuántas no.

    Es la respuesta a la pregunta que ninguna otra pantalla contesta: **¿el silencio de
    Laplace significa que tu agente está sano, o que no lo entendemos?** Cuando la
    identidad de un paso se parte o los tokens no llegan, el producto no falla: se
    calla, y «no estás tirando dinero» se lee como una buena noticia.

    Todo son cuentas sobre las llamadas a modelos de la ventana. Las proporciones y las
    palabras las pone `coverage.py`; aquí sólo se cuenta.
    """

    llm_calls: int = 0
    #: Llamadas cuyo paso se distingue de los demás: tienen sitio de llamada o huella de
    #: instrucciones. Las que no, caen en el nombre del span —`chat gpt-5.6-luna` para
    #: y las reglas las mezclan en un solo montón (D-060).
    identified_steps: int = 0
    #: Llamadas cuyo modelo está en la tabla de precios. El resto cuestan «no lo
    #: sabemos», y el total del proyecto está incompleto.
    priced: int = 0
    #: Llamadas con tokens del proveedor, no contados por nosotros ni ausentes.
    measured_tokens: int = 0
    #: Llamadas marcadas con una versión de prompt gestionado. Cero es lo normal para
    #: quien no ha adoptado la gestión, y por eso no se lee como un defecto.
    with_prompt_version: int = 0
    #: Pasos distintos vistos, y cuántos de ellos se parten en más identidades que
    #: ejecuciones. Es la forma concreta que toma la fragilidad: un prompt de sistema
    #: con una fecha dentro genera una huella por llamada.
    steps: int = 0
    split_steps: list[str] = field(default_factory=list)


@dataclass
class TraceCost:
    """Lo que costó una traza concreta, para comparar dos tiradas de evaluación.

    Existe porque la comparación A vs B necesita el coste de unas decenas de trazas
    sueltas identificadas por su id, que no es ninguna de las formas en las que el
    almacén ya sabe agregar: ni una ventana, ni un paso, ni un tramo.
    """

    trace_id: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    spans: int = 0
    error: bool = False
    #: Las mismas marcas de siempre: mientras no sean cero, el coste es un suelo.
    unknown_cost_spans: int = 0
    assumed_rate_spans: int = 0


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


def bucket_count(window: Window, bucket_minutes: int) -> int:
    """Cuántos tramos caben en la ventana. Al menos uno."""
    minutos = (window.until - window.since).total_seconds() / 60
    return max(int(minutos // bucket_minutes) + 1, 1)


def densify(
    filas: list[tuple[int, Bucket]], window: Window, bucket_minutes: int
) -> list[Bucket]:
    """Rellena los tramos vacíos y les pone su instante de inicio.

    Vive aquí y no en un almacén concreto porque un hueco es información —una hora sin
    ejecuciones es una hora sin ejecuciones— y las dos series tienen que tener la misma
    forma. Si un almacén devolviera sólo los tramos con datos y el otro todos, la misma
    gráfica se leería distinta según dónde estén las filas.
    """
    total = bucket_count(window, bucket_minutes)
    por_indice = {i: b for i, b in filas if 0 <= i < total}
    salida: list[Bucket] = []
    for i in range(total):
        inicio = window.since + timedelta(minutes=bucket_minutes * i)
        bucket = por_indice.get(i)
        salida.append(replace(bucket, start=inicio) if bucket else Bucket(start=inicio))
    return salida


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

    def timeseries(
        self, project_id: str, window: Window, bucket_minutes: int
    ) -> list[Bucket]:
        """La ventana troceada en tramos, agregada por ejecución (panel, Fase 4)."""

    def window_facts(
        self, project_id: str, since: datetime, until: datetime
    ) -> WindowFacts:
        """Modelos, herramientas y pasos vistos en un tramo. Atribuye los picos."""

    def costs_for_traces(
        self, project_id: str, trace_ids: list[str]
    ) -> dict[str, TraceCost]:
        """Coste, tokens y duración de unas trazas concretas (evaluación, Fase 5)."""

    def prompt_usage(self, project_id: str, window: Window) -> list[PromptUsage]:
        """Coste y volumen por versión de prompt gestionado (Fase 6)."""

    def prompt_versions_by_trace(
        self, project_id: str, trace_ids: list[str]
    ) -> dict[str, list[tuple[str, int]]]:
        """Qué versiones de prompt usó cada una de esas trazas.

        Se pide por trazas y no por versión porque lo que hay que cruzar son las trazas
        **anotadas**, que son unas decenas, y no todo el tráfico de la versión, que
        pueden ser millones. El acierto de una versión sale de ahí.
        """

    def observed_prompts(self, project_id: str, window: Window) -> list[ObservedPrompt]:
        """Juegos de instrucciones vistos en las trazas, sin gestión de prompts."""

    def coverage(self, project_id: str, window: Window) -> CoverageFacts:
        """Cuántas llamadas de la ventana entiende Laplace, y cuántas no."""

    def health(self) -> bool:
        """True si el almacén responde."""
