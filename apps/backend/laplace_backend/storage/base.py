"""Interfaz de almacenamiento.

La implementación de esta fase es ClickHouse. El modo local con SQLite (Fase 1,
punto 7) entrará por esta misma interfaz sin tocar la API ni el frontend (D-015).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from laplace.schema import Span, TraceSummary

from ..pasos import con_pista, nombre_de_paso


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
    #: Identidad exacta de un paso (`step_key`, o el nombre si no la tiene). Es lo que
    #: usa «Ver las trazas afectadas» de un hallazgo: buscar por texto confundía
    #: `consultar_manual` con `consultar_manual_cacheado`, y en los hallazgos cuyo
    #: título lleva el llamante no encontraba nada.
    step_key: str | None = None
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
class CostGroup:
    """El gasto de un usuario o de una sesión en la ventana (D-123).

    `key` vacío agrupa las ejecuciones que no dicen de quién son: se devuelve igual,
    porque esconderlas haría que los grupos sumaran menos que el total sin explicarlo.
    """

    key: str
    traces: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    #: Llamadas sin tarifa dentro del grupo: si no es cero, `cost_usd` es un suelo.
    unknown_cost_spans: int = 0


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
    #: Camino de llamada. Es lo que deja titular dos pasos homónimos sin que las dos
    #: tarjetas del inicio se lean como un duplicado (D-115).
    site: str = ""
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
class LoopGroup:
    """Un paso que se repite muchas veces en una traza **sin repetirse exacto**.

    Es la diferencia con `RepeatedGroup`: aquel agrupa llamadas idénticas, y un bucle
    de verdad casi nunca lo es —lleva un contador de intentos, una página, una hora—.
    `distinct_inputs > 1` es lo que dice que esto no es una repetición exacta ya
    contada por la otra regla, y `distinct_outputs` bajo con muchas vueltas es lo que
    dice que el bucle no avanza (D-109).
    """

    loop_hash: str
    name: str
    span_type: str
    model: str = ""
    step_key: str = ""
    #: Camino de llamada, por lo mismo que en `RepeatedGroup` (D-115).
    site: str = ""
    hint: str = ""
    traces: int = 0
    total_spans: int = 0
    #: Vueltas de más: todo menos la primera de cada traza.
    extra_spans: int = 0
    max_per_trace: int = 0
    distinct_inputs: int = 0
    distinct_outputs: int = 0
    extra_cost_usd: float = 0.0
    extra_duration_ms: float = 0.0
    extra_input_tokens: int = 0
    extra_output_tokens: int = 0
    extra_unknown_cost_spans: int = 0
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
    #: Se calcula **sólo sobre llamadas que respondieron**. Una que falló no tiene
    #: tokens, y con el mínimo sobre todas, una sola caída del proveedor en la ventana
    #: dejaba este suelo en cero y apagaba la regla del contexto fijo (D-108).
    min_input_tokens: int = 0
    #: Camino de llamada («atender_ticket > redactar_respuesta»). Sirve para titular
    #: dos pasos homónimos de forma legible.
    site: str = ""
    #: Tiempo total del paso, para sumar lo que se recupera arreglándolo.
    #: Es lo que permite decir algo útil sobre «modelo caro» cuando no hay tarifa: la
    #: latencia se mide siempre (D-108).
    duration_ms: float = 0.0
    #: **Mediana** por llamada, y no la media. Con la media, un portátil que se suspende
    #: a mitad de una tanda deja un span de dos horas y el paso entero parece lentísimo:
    #: pasó de verdad y señaló a un paso inocente. Una mediana no se mueve por un valor
    #: extremo, y lo que se compara entre modelos es justo eso, lo típico (D-108).
    p50_duration_ms: float = 0.0
    #: Y la mediana de tokens de salida, por lo mismo: una generación desbocada —un
    #: modelo que se pone a repetir hasta agotar `max_tokens`— mueve la media de un paso
    #: que normalmente contesta tres palabras, y la regla del modelo caro decide con ese
    #: número. Las medias siguen existiendo, pero para enseñarlas, no para decidir.
    p50_output_tokens: float = 0.0
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
    identidad de un paso incluye la huella de sus instrucciones (D-060), así que dos
    `step_key` **bajo el mismo camino de llamada** son dos versiones del mismo prompt,
    con sus fechas y su coste. No se puede enseñar el texto entero —sólo se guarda la
    pista—, pero sí cuándo cambió y qué pasó con el coste, que es la mitad de la
    pregunta.

    Lo de «bajo el mismo camino» no es un matiz: esta clase decía «bajo la misma
    etiqueta» y dejó de ser cierto el día que D-106 metió el camino de llamada dentro de
    `step_key`. Desde entonces, un prompt que no había cambiado nunca salía en pantalla
    como tres versiones porque se llamaba desde tres sitios (D-115).
    """

    step_key: str
    step_label: str
    #: El camino de llamada. Es lo que separa «otro llamante» de «otro prompt».
    site: str = ""
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
    Se les añade **de dónde se llaman** cuando se sabe —`atender_ticket` frente a
    `responder_consulta`, que es lo que distingue a dos agentes con una función
    homónima— y, si no, el principio de sus instrucciones. El camino primero porque se
    lee: un título con sesenta caracteres de prompt dentro no lo lee nadie (D-106).

    Cómo se escribe el nombre lo decide `pasos.nombre_de_paso`, que es el único sitio
    del producto que lo sabe. Aquí se decide **cuándo** hace falta. Tenerlo en dos
    sitios era el fallo que esto mismo arregla, un nivel más arriba (D-115).
    """
    repetidas = {f.name for f in filas if sum(1 for g in filas if g.name == f.name) > 1}
    if not repetidas:
        return filas
    for fila in filas:
        if fila.name not in repetidas:
            continue
        nombre = nombre_de_paso(fila.name, getattr(fila, "site", ""))
        if nombre != fila.name:
            fila.name = nombre
            continue
        # Sin camino —tráfico anterior a D-106, o un agente sin decorar— lo único que
        # queda para separarlos es el principio de sus instrucciones, recortado.
        fila.name = con_pista(fila.name, getattr(fila, "hint", ""))
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
    def loop_groups_sql(self) -> str:
        """La consulta que detecta bucles sin avance, tal cual se ejecuta.

        Misma razón que `repeated_groups_sql`: la ficha del hallazgo enseña la consulta
        que se ha ejecutado de verdad, y cada almacén tiene la suya.
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

    def sample_loop(
        self, project_id: str, window: Window, loop_hash: str, limit: int = 40
    ) -> list[Span]:
        """Las vueltas de un bucle en una traza concreta, como evidencia.

        Gemela de `sample_repetition` y separada de ella a propósito: un bucle se agrupa
        por `loop_hash` —que ignora los números de la entrada— y una repetición por
        `dedup_hash`, que no. Mezclarlas devolvería una vuelta suelta en vez del bucle.
        """

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

    def co_occurring_step_keys(self, project_id: str, window: Window) -> set[str]:
        """Claves de paso que conviven con otra del mismo camino en una ejecución.

        Es lo que separa «el prompt cambió» de «este sitio hace varias llamadas con
        prompts distintos»: una versión nueva no corre junto a la anterior dentro de la
        misma traza, y dos llamadas distintas sí.
        """

    def coverage(self, project_id: str, window: Window) -> CoverageFacts:
        """Cuántas llamadas de la ventana entiende Laplace, y cuántas no."""

    # Lo que añadieron D-117 y D-123 sin pasar por aquí. Las rutas lo llaman en los dos
    # almacenes, así que es parte del contrato; `test_auditoria_p2` compara las firmas.

    def loop_groups(
        self,
        project_id: str,
        window: Window,
        *,
        min_vueltas: int = 4,
        max_salidas: int = 2,
        limit: int = 20,
    ) -> list[LoopGroup]:
        """Pasos que se repiten con la misma entrada sin avanzar (D-117)."""

    def cost_by(
        self, project_id: str, window: Window, dimension: str, limit: int = 20
    ) -> list[CostGroup]:
        """Gasto agrupado por usuario o por sesión."""

    def unpriced_models(self, project_ids: list[str] | None, window: Window) -> list[str]:
        """Modelos con llamadas sin tarifa en la ventana; `None` son todos los proyectos."""

    def spans_by_model(self, model: str) -> list[Span]:
        """Todas las llamadas a un modelo, de todos los proyectos. Para recalcular coste."""

    def delete_project(self, project_id: str) -> None:
        """Borra todos los spans de un proyecto."""

    def delete_before(self, cutoff: datetime) -> int:
        """Borra los spans que empezaron antes de `cutoff`. La retención en SQLite."""

    def health(self) -> bool:
        """True si el almacén responde."""
