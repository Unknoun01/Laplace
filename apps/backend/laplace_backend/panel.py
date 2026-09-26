"""El panel (Fase 4, §4): coste por unidad de trabajo y atribución de picos.

Un panel de líneas con tokens y latencia no aporta nada frente a Grafana. Este existe
sólo por dos ideas, y si alguna de las dos se cae, el panel deja de tener sentido:

1. **Coste por unidad de trabajo, no totales.** Si los tokens suben un 40 % pero hay un
   40 % más de ejecuciones, no pasa nada. Si suben con las mismas ejecuciones, hay
   degradación. Por eso las métricas protagonistas son todas *por ejecución*, los
   totales van en letra pequeña, y **la lectura se dice con palabras**: nadie tiene por
   qué deducir de dos líneas que su agente ha empeorado.
2. **Atribución de picos.** Un pico sin causa es una alarma sin acción. Al pinchar uno
   se dice cuándo empezó, qué cambió alrededor y a qué trazas ir. Todo sale de las
   trazas: un modelo que aparece por primera vez, una herramienta nueva, un paso que se
   lleva el exceso. **Si no se puede atribuir con datos reales, se dice que no se sabe.**
   Nunca se insinúa una correlación.

Y el criterio de D-073, aquí también: si no hay base suficiente para una métrica, vale
`None` y se explica por qué. Nunca cero —que se lee como «no pasa nada»— ni una
extrapolación que no se sostiene.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import cifras
from .dinero import motivo_sin_dinero
from .insights import observed_days, span_label, window_label
from .storage.base import Bucket, Latency, Window, WindowFacts

logger = logging.getLogger("laplace.panel")

# ---------------------------------------------------------------------------------
# Umbrales. Explícitos y con nombre: son los que decide si el panel dice «revisar».
# ---------------------------------------------------------------------------------

#: Por debajo de esto, un cambio es ruido y se dice que está estable. Con menos, el
#: panel gritaría «degradación» cada vez que alguien despliega un prompt más largo.
MATERIAL_CHANGE = 0.10

#: Un tramo es un pico si su coste por ejecución pasa de este múltiplo de la mediana.
#: La mediana y no la media: una media contaminada por el propio pico sube con él y
#: acaba escondiéndolo, que es exactamente el fallo que hay que evitar.
SPIKE_FACTOR = 2.5

#: Tramos con datos que hacen falta para que la mediana signifique algo. Con tres, un
#: pico es la mitad de la muestra y la mediana no es una línea base de nada.
MIN_BUCKETS_FOR_SPIKES = 6

#: Cuántos picos se investigan. Atribuir cuesta dos consultas por pico, y una lista de
#: veinte picos no se lee: los tres más caros son los que alguien va a mirar.
MAX_SPIKES = 3

#: Del exceso de coste de un pico, cuánto tiene que llevarse un paso para nombrarlo.
#: Por debajo no es una causa, es reparto.
STEP_ATTRIBUTION_SHARE = 0.40

#: Ejecuciones que tiene que tener el periodo anterior para poder comparar con él.
#: Con dos, la variación es la diferencia entre dos anécdotas.
MIN_TRACES_FOR_COMPARISON = 5

#: Qué parte del periodo anterior tiene que estar cubierta por datos. Es el mismo
#: problema que la proyección mensual sobre una hora (D-073) con otra cara: si el
#: proyecto empezó a enviar trazas a mitad del periodo anterior, compararse con él da
#: «el gasto sube un 193.100 %», un número que quema la confianza en toda la pantalla.
MIN_PREVIOUS_COVERAGE = 0.5


Verdict = Literal["sin-base", "estable", "normal", "revisar", "mejora", "mixto"]


# ---------------------------------------------------------------------------------
# Modelos de salida
# ---------------------------------------------------------------------------------


class Metric(BaseModel):
    """Una métrica con su comparación contra el periodo anterior.

    `value` es `None` cuando no hay base para calcularla —un tramo sin ejecuciones no
    tiene coste por ejecución— y entonces `unavailable` dice por qué. Cero significaría
    «cuesta cero», que es una afirmación distinta y normalmente falsa (D-073).
    """

    label: str
    value: float | None = None
    #: El mismo cálculo sobre el periodo inmediatamente anterior, de la misma duración.
    previous: float | None = None
    #: Variación relativa: 0,25 = 25 % más. `None` si falta alguno de los dos lados.
    change_ratio: float | None = None
    #: Formato para la interfaz: `money`, `tokens`, `count`, `duration`, `ratio` (0,25
    #: = 25 %).
    unit: str = "count"
    #: Por qué no hay cifra. Vacío cuando la hay.
    unavailable: str = ""


class Reading(BaseModel):
    """La lectura del panel, en palabras.

    Es la pieza que justifica el panel entero. Un usuario no tiene por qué mirar dos
    series y deducir si el gasto ha subido por más trabajo o por trabajo más caro: eso
    lo dice aquí una frase, y debajo va el porqué con las cifras que la sostienen.
    """

    verdict: Verdict
    headline: str
    detail: str


class SpikeCause(BaseModel):
    """Una causa concreta, con lo que la respalda. Nunca una insinuación.

    `version_prompt` era el hueco que la atribución tenía reservado desde que se
    escribió, esperando a que existiera la pestaña de Prompts. Entra por la misma puerta
    que las demás —algo que aparece en las trazas del pico y no aparecía antes— y no por
    la hora de un despliegue, que sería precisamente la correlación insinuada que este
    módulo existe para no contar (D-092).
    """

    kind: Literal[
        "version_prompt",
        "modelo_nuevo",
        "herramienta_nueva",
        "paso_nuevo",
        "paso_disparado",
        "volumen",
    ]
    text: str
    evidence: str = ""
    #: Adónde ir a mirar, cuando la causa tiene una pantalla propia. Hoy sólo la versión
    #: de prompt la tiene: `{"prompt": "resumen", "version": "8"}`.
    link: dict[str, str] = Field(default_factory=dict)


class Spike(BaseModel):
    """Un tramo cuyo coste por ejecución se dispara, con su atribución.

    Un pico se define por dinero, así que sin tarifas no hay picos que dar: no se
    enseñan a cero, se dice por qué no los hay (`Panel.spikes_unavailable`, D-107).
    """

    start: datetime
    end: datetime
    traces: int
    cost_usd: float
    cost_per_trace_usd: float
    #: Cuántas veces la línea base (la mediana de los tramos con datos).
    times_baseline: float
    baseline_cost_per_trace_usd: float
    #: El sobrecoste frente a lo que habrían costado esas ejecuciones a precio normal.
    excess_usd: float
    #: True cuando en la ventana hay llamadas sin tarifa: entonces el sobrecoste de
    #: arriba es un SUELO y quien lo afirme tiene que decirlo (D-051, D-107).
    cost_is_floor: bool = False
    causes: list[SpikeCause] = Field(default_factory=list)
    #: Vacío cuando hay causas. Cuando no, dice exactamente eso y no otra cosa.
    unattributed: str = ""
    #: Filtro para el explorador de trazas: las ejecuciones responsables del pico.
    traces_query: dict[str, str] = Field(default_factory=dict)


class PanelBucket(BaseModel):
    """Un punto de la serie. Lo por-ejecución es `None` si no hubo ejecuciones."""

    start: datetime
    traces: int = 0
    cost_usd: float = 0.0
    cost_per_trace_usd: float | None = None
    tokens_per_trace: float | None = None
    steps_per_trace: float | None = None
    duration_ms_per_trace: float | None = None
    is_spike: bool = False
    #: Por qué el coste de este punto no se puede afirmar, cuando no se puede. Sin esto,
    #: la serie pinta una línea plana en cero que se lee como «no gasta» (D-107).
    cost_unavailable: str = ""


class Panel(BaseModel):
    """Todo lo que pinta la pestaña Panel."""

    project_id: str
    days: int
    currency: str = "USD"

    #: Días de datos reales. La misma cifra que gobierna la proyección del inicio.
    observed_days: float = 0.0
    bucket_minutes: int = 60
    #: True cuando hay un periodo anterior **utilizable** con el que comparar.
    has_previous: bool = False
    #: Por qué no lo hay. La interfaz lo enseña en lugar de pintar variaciones vacías.
    comparison_unavailable: str = ""

    #: Las protagonistas, todas por ejecución.
    per_execution: list[Metric] = Field(default_factory=list)
    #: Contexto secundario. Van aparte a propósito: un total que sube no es noticia.
    totals: list[Metric] = Field(default_factory=list)

    reading: Reading
    buckets: list[PanelBucket] = Field(default_factory=list)
    spikes: list[Spike] = Field(default_factory=list)
    #: Por qué no hay picos que enseñar, cuando no los hay.
    spikes_unavailable: str = ""


# ---------------------------------------------------------------------------------
# Troceado
# ---------------------------------------------------------------------------------


def bucket_minutes_for(days: int) -> int:
    """Un ancho de tramo que deje entre 24 y 48 puntos, y que sea una hora redonda.

    Los tramos son horas, no fracciones raras: «las 14:00» se entiende y «el punto 37»
    no. Con menos de 24 puntos la gráfica no tiene forma; con más de 60 no se lee.
    """
    if days <= 1:
        return 60
    if days <= 2:
        return 120
    if days <= 7:
        return 360
    if days <= 14:
        return 720
    return 1440


# ---------------------------------------------------------------------------------
# Métricas por unidad de trabajo
# ---------------------------------------------------------------------------------


@dataclass
class _Aggregate:
    """Los totales crudos de un periodo. De aquí sale todo lo demás."""

    traces: int = 0
    spans: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    duration_ms: float = 0.0
    error_traces: int = 0

    @classmethod
    def of(cls, buckets: list[Bucket]) -> _Aggregate:
        return cls(
            traces=sum(b.traces for b in buckets),
            spans=sum(b.spans for b in buckets),
            cost_usd=sum(b.cost_usd for b in buckets),
            tokens=sum(b.input_tokens + b.output_tokens for b in buckets),
            duration_ms=sum(b.duration_ms_sum for b in buckets),
            error_traces=sum(b.error_traces for b in buckets),
        )

    def per_trace(self, total: float) -> float | None:
        """Por ejecución, o `None` si no hubo ejecuciones que dividir."""
        return (total / self.traces) if self.traces else None


def _change(actual: float | None, anterior: float | None) -> float | None:
    """Variación relativa. `None` si falta un lado o si el anterior era cero.

    Dividir entre cero daría «infinito por ciento», que en una pantalla se lee como un
    error del producto y no como «antes no había nada».
    """
    if actual is None or anterior is None or anterior == 0:
        return None
    return (actual - anterior) / anterior


def _metric(
    label: str,
    unit: str,
    actual: float | None,
    anterior: float | None,
    *,
    unavailable: str = "",
) -> Metric:
    return Metric(
        label=label,
        unit=unit,
        value=actual,
        previous=anterior,
        change_ratio=_change(actual, anterior),
        unavailable=unavailable if actual is None else "",
    )


def _per_execution_metrics(
    actual: _Aggregate,
    anterior: _Aggregate | None,
    sin_dinero: str = "",
    latencia: Latency | None = None,
    latencia_anterior: Latency | None = None,
) -> list[Metric]:
    sin_datos = "no hay ejecuciones en este rango"
    prev = anterior or _Aggregate()
    lat = latencia or Latency()
    lat_b = latencia_anterior if anterior else None

    def par(campo: str) -> tuple[float | None, float | None]:
        return (
            actual.per_trace(getattr(actual, campo)),
            prev.per_trace(getattr(prev, campo)) if anterior else None,
        )

    coste_a, coste_b = par("cost_usd")
    tok_a, tok_b = par("tokens")
    pasos_a, pasos_b = par("spans")
    err_a, err_b = par("error_traces")

    return [
        # Sin una sola tarifa conocida no hay cifra que dar: un 0 aquí se lee como
        # «no cuesta nada» y es la misma mentira que proyectar sobre una hora (D-107).
        _metric(
            "Coste por ejecución",
            "money",
            None if sin_dinero else coste_a,
            None if sin_dinero else coste_b,
            unavailable=sin_dinero or sin_datos,
        ),
        _metric("Tokens por ejecución", "tokens", tok_a, tok_b, unavailable=sin_datos),
        _metric("Pasos por ejecución", "count", pasos_a, pasos_b, unavailable=sin_datos),
        # Mediana y p95, no media (D-145): tres ejecuciones colgadas entre cien llevan
        # la media a una cifra que no es la espera de nadie.
        _metric(
            "Duración, mediana",
            "duration",
            lat.p50_ms,
            lat_b.p50_ms if lat_b else None,
            unavailable=sin_datos,
        ),
        _metric(
            "Duración, p95",
            "duration",
            lat.p95_ms,
            lat_b.p95_ms if lat_b else None,
            unavailable=sin_datos,
        ),
        _metric("Ejecuciones con error", "ratio", err_a, err_b, unavailable=sin_datos),
    ]


def _total_metrics(
    actual: _Aggregate, anterior: _Aggregate | None, sin_dinero: str = ""
) -> list[Metric]:
    """Contexto, no titular. Un total que sube porque hay más trabajo no es noticia."""
    prev = anterior or _Aggregate()
    hay = anterior is not None
    return [
        _metric(
            "Gasto total",
            "money",
            None if sin_dinero else actual.cost_usd,
            (prev.cost_usd if hay else None) if not sin_dinero else None,
            unavailable=sin_dinero,
        ),
        _metric("Ejecuciones", "count", float(actual.traces), float(prev.traces) if hay else None),
        _metric("Tokens", "tokens", float(actual.tokens), float(prev.tokens) if hay else None),
    ]


# ---------------------------------------------------------------------------------
# La lectura, en palabras
# ---------------------------------------------------------------------------------


def _pct(ratio: float) -> str:
    return f"{abs(ratio) * 100:.0f} %"


def read_out(
    actual: _Aggregate,
    anterior: _Aggregate | None,
    ventana: str,
    sin_comparacion: str = "",
) -> Reading:
    """Traduce las dos variaciones —volumen y coste unitario— a una frase.

    La tabla de casos completa, porque es el corazón del panel. `total = unitario ×
    ejecuciones`, así que toda subida del gasto es una de estas tres cosas: más trabajo,
    trabajo más caro, o las dos. Decir cuál es el trabajo del panel.

    El caso que justifica todo esto es el cuarto: **menos ejecuciones y aun así el mismo
    gasto**. En un panel de totales eso es una línea plana y nadie mira; aquí es una
    degradación con nombre.
    """
    if anterior is None or anterior.traces == 0 or actual.traces == 0 or sin_comparacion:
        motivo = sin_comparacion or (
            "todavía no hay dos periodos con ejecuciones que contrastar"
        )
        return Reading(
            verdict="sin-base",
            headline="Todavía no hay con qué comparar.",
            detail=(
                f"Para decir si algo ha cambiado hace falta un periodo anterior "
                f"utilizable, y {motivo}. Llevas {ventana} de datos. Las cifras de abajo "
                f"son lo observado, medido, sin comparación: no son cero, es que "
                f"todavía no hay contra qué medirlas."
            ),
        )

    unitario = _change(actual.per_trace(actual.cost_usd), anterior.per_trace(anterior.cost_usd))
    volumen = _change(float(actual.traces), float(anterior.traces))
    total = _change(actual.cost_usd, anterior.cost_usd)
    if unitario is None or volumen is None or total is None:
        return Reading(
            verdict="sin-base",
            headline="Todavía no hay con qué comparar.",
            detail="El periodo anterior no tiene gasto con el que contrastar éste.",
        )

    sube_unitario = unitario > MATERIAL_CHANGE
    baja_unitario = unitario < -MATERIAL_CHANGE
    sube_volumen = volumen > MATERIAL_CHANGE
    baja_volumen = volumen < -MATERIAL_CHANGE

    cifras = (
        _clausula("El gasto", total, "sube", "baja", "se mantiene")
        + ", "
        + _clausula("las ejecuciones", volumen, "suben", "bajan", "se mantienen")
        + " y "
        + _clausula("el coste de cada una", unitario, "sube", "baja", "se mantiene")
        + "."
    )

    if sube_unitario and sube_volumen:
        return Reading(
            verdict="mixto",
            headline="Suben las dos cosas: hay más ejecuciones y además cada una cuesta más.",
            detail=(
                f"{cifras} La parte del volumen es trabajo real; la del coste por "
                f"ejecución no, y es la que hay que mirar."
            ),
        )
    if sube_unitario:
        return Reading(
            verdict="revisar",
            headline="Subida sin más ejecuciones: revisar.",
            detail=(
                f"{cifras} El mismo trabajo está costando más que antes, así que esto no "
                f"lo explica la demanda. Mira los picos de abajo y la lista de problemas."
            ),
        )
    if sube_volumen and not baja_unitario:
        return Reading(
            verdict="normal",
            headline="Subida acompañada de más ejecuciones: normal.",
            detail=(
                f"{cifras} Cada ejecución cuesta prácticamente lo mismo: el gasto sube "
                f"porque tu agente está trabajando más, no peor."
            ),
        )
    if baja_unitario:
        return Reading(
            verdict="mejora",
            headline="Cada ejecución cuesta menos que antes.",
            detail=f"{cifras} Es la dirección buena: el trabajo se está abaratando.",
        )
    if baja_volumen:
        return Reading(
            verdict="estable",
            headline="Menos ejecuciones, y cada una cuesta lo mismo.",
            detail=(
                f"{cifras} La bajada del gasto es menos trabajo, no una mejora: si el "
                f"tráfico vuelve, el gasto vuelve."
            ),
        )
    return Reading(
        verdict="estable",
        headline="Estable: el coste por ejecución no se mueve.",
        detail=cifras,
    )


def _clausula(sujeto: str, ratio: float, sube: str, baja: str, quieto: str) -> str:
    """«el gasto sube un 40 %» / «las ejecuciones se mantienen».

    Cada sujeto trae sus tres formas verbales porque «las ejecuciones sube un 40 %» es
    justo el tipo de frase que hace dudar de todo lo demás que dice la pantalla. Y por
    debajo del umbral no se dice el porcentaje: un 3 % no «sube», está quieto.
    """
    if ratio > MATERIAL_CHANGE:
        return f"{sujeto} {sube} un {_pct(ratio)}"
    if ratio < -MATERIAL_CHANGE:
        return f"{sujeto} {baja} un {_pct(ratio)}"
    return f"{sujeto} {quieto}"


# ---------------------------------------------------------------------------------
# Picos y su atribución
# ---------------------------------------------------------------------------------


def _share(parte: float) -> str:
    """«el 62 % del sobrecoste» / «prácticamente todo el sobrecoste».

    Devuelve el complemento entero y no sólo la cifra: con «prácticamente todo» la
    preposición cambia, y «se lleva prácticamente todo del sobrecoste» es una frase mal
    escrita en una pantalla que presume de decir las cosas claras.

    Lo de no decir el porcentaje por encima del 95 % tiene su motivo: la parte de un
    paso puede pasar del 100 % del sobrecoste neto del tramo, porque a la vez otro paso
    puede haberse abaratado. Un «101 %» se lee como un error de cálculo aunque sea
    aritméticamente cierto.
    """
    if parte >= 0.95:
        return "prácticamente todo el sobrecoste"
    return f"el {parte * 100:.0f} % del sobrecoste"


def _median(valores: list[float]) -> float:
    ordenados = sorted(valores)
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return ordenados[mitad]
    return (ordenados[mitad - 1] + ordenados[mitad]) / 2


def find_spikes(buckets: list[Bucket]) -> tuple[list[int], float, str]:
    """Índices de los tramos que se disparan, la línea base, y por qué no los hay.

    El criterio es sobre el **coste por ejecución**, no sobre el gasto del tramo: una
    hora punta con el triple de tráfico no es un pico, es un martes por la mañana.
    """
    con_datos = [b for b in buckets if b.traces > 0]
    if len(con_datos) < MIN_BUCKETS_FOR_SPIKES:
        return (
            [],
            0.0,
            f"Hacen falta al menos {MIN_BUCKETS_FOR_SPIKES} tramos con ejecuciones para "
            f"tener una línea base con la que comparar, y de momento hay "
            f"{len(con_datos)}. Sin línea base, cualquier tramo parece un pico.",
        )

    unitarios = [b.cost_usd / b.traces for b in con_datos]
    base = _median(unitarios)
    if base <= 0:
        return [], 0.0, "El coste habitual por ejecución es cero, así que no hay pico posible."

    picos = [
        i
        for i, b in enumerate(buckets)
        if b.traces > 0 and (b.cost_usd / b.traces) > base * SPIKE_FACTOR
    ]
    picos.sort(key=lambda i: buckets[i].cost_usd / buckets[i].traces, reverse=True)
    return picos[:MAX_SPIKES], base, ""


def attribute(
    dentro: WindowFacts, antes: WindowFacts, excess_usd: float, volumen_ratio: float | None
) -> tuple[list[SpikeCause], str]:
    """Qué cambió alrededor del pico, **con datos o con nada**.

    Cinco señales, todas comprobables en las trazas y todas de la forma «esto aparece
    aquí y no aparecía antes». Ninguna es una correlación estadística: si no hay nada
    nuevo y ningún paso concentra el exceso, se dice que no se identifica la causa, que
    es una respuesta honesta y accionable —«ve a mirar estas trazas»— y no un relleno.

    La quinta es la versión de prompt, y merece una nota porque es la que más se podía
    haber hecho mal. La tentación era cruzar el historial de despliegues con la hora del
    pico: «desplegaste v8 a las 14:02 y el pico empieza a las 14:00». Eso es una
    coincidencia temporal, no una causa, y habría acabado señalando cualquier despliegue
    que cayera cerca de cualquier pico. Lo que se usa es la versión que **aparece en las
    trazas del tramo** y no aparecía en las de antes, que es un hecho medido igual que
    un modelo nuevo. El historial de despliegues sirve para contar la historia en la
    pestaña de Prompts, no para atribuir aquí (D-092).
    """
    causas: list[SpikeCause] = []

    # El prompt va primero a propósito. De todo lo que puede cambiar alrededor de un
    # pico, una versión de prompt nueva es lo más accionable —hay un botón para
    # deshacerla— y lo más frecuente: un modelo nuevo se despliega una vez al trimestre
    # y un prompt se toca los martes.
    for etiqueta in sorted(dentro.prompts - antes.prompts):
        nombre, _, version = etiqueta.rpartition("@")
        if not nombre:
            continue
        if version == "0":
            causas.append(
                SpikeCause(
                    kind="version_prompt",
                    text=(
                        f"Las trazas de este tramo corrieron con el texto de reserva de "
                        f"«{nombre}», no con la versión de producción."
                    ),
                    evidence=(
                        "Significa que el SDK no pudo pedirle el prompt a Laplace y usó "
                        "el del código. No figura en ninguna traza anterior de la ventana."
                    ),
                    link={"prompt": nombre},
                )
            )
            continue
        causas.append(
            SpikeCause(
                kind="version_prompt",
                text=(
                    f"Las trazas de este tramo usan una versión de «{nombre}» que no "
                    f"estaba antes: la v{version}."
                ),
                evidence=(
                    "No figura en ninguna traza anterior de esta ventana. Lo dicen las "
                    "propias trazas, no la hora de un despliegue."
                ),
                link={"prompt": nombre, "version": version},
            )
        )

    for modelo in sorted(dentro.models - antes.models):
        causas.append(
            SpikeCause(
                kind="modelo_nuevo",
                text=f"Aparece un modelo que no se había visto antes: {modelo}.",
                evidence="No figura en ninguna traza anterior de esta ventana.",
            )
        )
    for herramienta in sorted(dentro.tools - antes.tools):
        causas.append(
            SpikeCause(
                kind="herramienta_nueva",
                text=f"Aparece una herramienta nueva: «{herramienta}».",
                evidence="No figura en ninguna traza anterior de esta ventana.",
            )
        )

    # Un paso que se lleva la mayor parte del sobrecoste. Se compara por ejecución, no
    # en bruto: si no, el paso más caro del agente sale señalado en todos los picos.
    if excess_usd > 0 and dentro.traces:
        base_por_traza = {
            nombre: hechos.cost_usd / antes.traces for nombre, hechos in antes.steps.items()
        } if antes.traces else {}
        exceso_por_paso: dict[str, float] = {}
        for nombre, hechos in dentro.steps.items():
            esperado = base_por_traza.get(nombre, 0.0) * dentro.traces
            de_mas = hechos.cost_usd - esperado
            if de_mas > 0:
                exceso_por_paso[nombre] = de_mas

        for nombre, de_mas in sorted(exceso_por_paso.items(), key=lambda kv: -kv[1])[:2]:
            parte = de_mas / excess_usd
            if parte < STEP_ATTRIBUTION_SHARE:
                continue
            nuevo = nombre not in antes.steps
            # `nombre` es el sitio de llamada («atender_ticket > redactar»), que es lo
            # que agrupa bien; en pantalla va la etiqueta, que es lo que el usuario
            # reconoce (D-106).
            etiqueta = dentro.steps[nombre].label or nombre
            causas.append(
                SpikeCause(
                    kind="paso_nuevo" if nuevo else "paso_disparado",
                    text=(
                        f"Un paso nuevo, «{etiqueta}», se lleva {_share(parte)}."
                        if nuevo
                        else f"El paso «{etiqueta}» se lleva {_share(parte)}: cuesta más "
                        f"por ejecución que en el resto del rango."
                    ),
                    evidence=f"{cifras.dinero(de_mas)} por encima de lo que costaba antes.",
                )
            )

    if volumen_ratio is not None and volumen_ratio > 1 + MATERIAL_CHANGE and not causas:
        # Sólo si no hay nada mejor: más ejecuciones explica el gasto del tramo, pero
        # el pico se mide por ejecución, así que casi nunca es la respuesta.
        de_mas = (volumen_ratio - 1) * 100
        causas.append(
            SpikeCause(
                kind="volumen",
                text=f"Hubo un {de_mas:.0f} % más de ejecuciones que de costumbre.",
                evidence="El resto del sobrecoste no se explica con nada nuevo en las trazas.",
            )
        )

    if causas:
        return causas, ""
    return [], (
        "No identificamos la causa. En este tramo no aparece ningún modelo, ninguna "
        "herramienta ni ninguna versión de prompt que no estuviera antes, y ningún paso "
        "concentra el sobrecoste. Las trazas del tramo están un clic más abajo."
    )


# ---------------------------------------------------------------------------------
# Montaje
# ---------------------------------------------------------------------------------


def comparable(buckets: list[Bucket]) -> str:
    """Por qué el periodo anterior NO sirve para comparar, o cadena vacía si sirve.

    Dos guardas, las dos por la misma razón que D-073: una variación porcentual contra
    un periodo casi vacío no es una medida, es un artefacto. «El gasto sube un
    193.100 %» sale de dividir entre casi nada, y quien lo lea dejará de creerse el
    resto de la pantalla —que sí es cierto—.
    """
    trazas = sum(b.traces for b in buckets)
    if trazas == 0:
        return "el periodo anterior no tiene ninguna ejecución"
    if trazas < MIN_TRACES_FOR_COMPARISON:
        return (
            f"el periodo anterior sólo tiene {trazas} "
            f"{'ejecución' if trazas == 1 else 'ejecuciones'}, muy pocas para comparar"
        )

    con_datos = [i for i, b in enumerate(buckets) if b.traces > 0]
    cobertura = (con_datos[-1] - con_datos[0] + 1) / len(buckets) if buckets else 0.0
    if cobertura < MIN_PREVIOUS_COVERAGE:
        return (
            "el periodo anterior está casi vacío: tu agente todavía no enviaba trazas "
            "durante la mayor parte de él, así que compararse con él no dice nada"
        )
    return ""


def _panel_bucket(bucket: Bucket, es_pico: bool, sin_dinero: str = "") -> PanelBucket:
    def por_traza(valor: float) -> float | None:
        return (valor / bucket.traces) if bucket.traces else None

    return PanelBucket(
        start=bucket.start,
        traces=bucket.traces,
        # Sin tarifas, el coste del punto no es cero: no existe. `None` y el motivo, que
        # es lo que la serie sabe pintar como hueco (D-107).
        cost_usd=0.0 if sin_dinero else bucket.cost_usd,
        cost_per_trace_usd=None if sin_dinero else por_traza(bucket.cost_usd),
        cost_unavailable=sin_dinero,
        tokens_per_trace=por_traza(bucket.input_tokens + bucket.output_tokens),
        steps_per_trace=por_traza(bucket.spans),
        duration_ms_per_trace=por_traza(bucket.duration_ms_sum),
        is_spike=es_pico,
    )


def _spike(
    store: Any,
    project_id: str,
    buckets: list[Bucket],
    indice: int,
    base_unitaria: float,
    ancho: timedelta,
    window: Window,
    suelo: bool = False,
) -> Spike:
    bucket = buckets[indice]
    inicio, fin = bucket.start, bucket.start + ancho
    unitario = bucket.cost_usd / bucket.traces
    exceso = max(bucket.cost_usd - base_unitaria * bucket.traces, 0.0)

    dentro = store.window_facts(project_id, inicio, fin)
    # «Antes» es todo lo que va del principio de la ventana hasta el pico. Si el pico
    # es el primer tramo con datos no hay antes, y eso se dice en vez de comparar
    # contra el vacío y anunciar que todo es nuevo.
    antes = (
        store.window_facts(project_id, window.since, inicio)
        if inicio > window.since
        else WindowFacts()
    )

    if antes.traces == 0:
        causas, sin_atribuir = [], (
            "No identificamos la causa: es el primer tramo con datos del rango, así que "
            "no hay nada anterior con lo que compararlo."
        )
    else:
        tramos_previos = [b for b in buckets[:indice] if b.traces > 0]
        media_trazas = (
            sum(b.traces for b in tramos_previos) / len(tramos_previos)
            if tramos_previos
            else None
        )
        causas, sin_atribuir = attribute(
            dentro,
            antes,
            exceso,
            (bucket.traces / media_trazas) if media_trazas else None,
        )

    return Spike(
        start=inicio,
        end=fin,
        traces=bucket.traces,
        cost_usd=bucket.cost_usd,
        cost_per_trace_usd=unitario,
        times_baseline=unitario / base_unitaria if base_unitaria else 0.0,
        baseline_cost_per_trace_usd=base_unitaria,
        excess_usd=exceso,
        cost_is_floor=suelo,
        causes=causas,
        unattributed=sin_atribuir,
        # El explorador acepta estos filtros tal cual: es el mismo rango del pico.
        traces_query={
            "project_id": project_id,
            "since": inicio.isoformat(),
            "until": fin.isoformat(),
            "sort": "cost",
        },
    )


def _aligned(window: Window, bucket_minutes: int) -> Window:
    """La misma ventana, empezando en una frontera de reloj.

    Sin esto los tramos empiezan en el minuto en que se cargó la página y la gráfica
    dice «de 04:32 a 10:32», que nadie relaciona con nada. Alineando, dice «de 06:00 a
    12:00». Se extiende hacia atrás menos de un tramo, y como las métricas que importan
    son cocientes por ejecución, que el primer tramo esté a medias no las sesga.
    """
    ancho = bucket_minutes * 60
    epoch = window.since.timestamp()
    desplazamiento = epoch % ancho
    if desplazamiento == 0:
        return window
    return Window(
        since=window.since - timedelta(seconds=desplazamiento),
        until=window.until,
        days=window.days,
    )


def build(store: Any, project_id: str, window: Window) -> Panel:
    """El panel entero. Dos consultas de serie, una o dos de latencia y dos por pico."""
    ancho_min = bucket_minutes_for(window.days)
    ancho = timedelta(minutes=ancho_min)
    window = _aligned(window, ancho_min)
    buckets = store.timeseries(project_id, window, ancho_min)

    # El periodo anterior es de la misma duración y va justo antes: comparar siete días
    # contra treinta diría cualquier cosa.
    largo = window.until - window.since
    previa = Window(since=window.since - largo, until=window.since, days=window.days)
    anteriores = store.timeseries(project_id, previa, ancho_min)

    actual = _Aggregate.of(buckets)
    anterior = _Aggregate.of(anteriores)
    sin_comparacion = comparable(anteriores)
    hay_anterior = anterior.traces > 0 and not sin_comparacion
    latencia = store.trace_latency(project_id, window)
    latencia_anterior = store.trace_latency(project_id, previa) if hay_anterior else None

    indices, base_unitaria, sin_picos = find_spikes(buckets)
    # `resumen` hace falta antes que los picos: el sobrecoste de un pico es un suelo si
    # en la ventana hay llamadas sin tarifa.
    resumen = store.summarize_window(project_id, window)
    picos = [
        _spike(
            store, project_id, buckets, i, base_unitaria, ancho, window,
            suelo=bool(resumen.unknown_cost_spans or resumen.assumed_rate_spans),
        )
        for i in indices
    ]

    # Los días observados salen del mismo sitio que en el inicio y con el mismo
    # cálculo: si el panel dijera «6 horas» —el ancho de un tramo— y el inicio «menos
    # de un minuto», una de las dos pantallas estaría mintiendo sobre los mismos datos.
    observados = observed_days(resumen, window)
    # Una sola decisión para todas las cifras en dólares de esta pantalla (D-107).
    sin_dinero = motivo_sin_dinero(
        llm_calls=resumen.llm_calls, unknown_cost_calls=resumen.unknown_cost_spans
    )
    return Panel(
        project_id=project_id,
        days=window.days,
        observed_days=observados,
        bucket_minutes=ancho_min,
        has_previous=hay_anterior,
        per_execution=_per_execution_metrics(
            actual,
            anterior if hay_anterior else None,
            sin_dinero,
            latencia,
            latencia_anterior,
        ),
        totals=_total_metrics(actual, anterior if hay_anterior else None, sin_dinero),
        reading=read_out(
            actual,
            anterior if hay_anterior else None,
            span_label(observados),
            sin_comparacion,
        ),
        comparison_unavailable="" if hay_anterior else sin_comparacion,
        buckets=[
            _panel_bucket(b, i in set(indices), sin_dinero) for i, b in enumerate(buckets)
        ],
        # Un pico es «cuesta más por ejecución que lo normal». Sin una sola tarifa
        # conocida, eso no se puede decir: se dice por qué, y no se pinta ninguno.
        spikes=[] if sin_dinero else picos,
        spikes_unavailable=sin_dinero or sin_picos,
    )


__all__ = [
    "Metric",
    "Panel",
    "PanelBucket",
    "Reading",
    "Spike",
    "SpikeCause",
    "attribute",
    "bucket_minutes_for",
    "build",
    "comparable",
    "find_spikes",
    "read_out",
    "window_label",
]
