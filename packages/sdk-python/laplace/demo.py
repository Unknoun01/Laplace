"""Trazas de ejemplo para `laplace demo`: un mes de un agente de atención al cliente.

Son datos **inventados**, en un proyecto llamado `demo`, y tanto el comando como la
propia interfaz lo dicen. Existen porque quien acaba de instalar Laplace todavía no tiene
un agente instrumentado, y una pantalla vacía no enseña qué hace el producto.

La demo anterior eran dieciséis trazas idénticas de 2,7 días: el Panel decía «todavía no
hay con qué comparar», no había picos y Evaluaciones y Prompts salían vacías. Ésta cuenta
un mes de «Vuelos Laplace» con una historia que recorre todas las pantallas:

* tráfico que crece de unas 14 ejecuciones al día a unas 40, con horas punta;
* las cuatro patologías que detecta el motor: modelo caro para clasificar, la misma
  extracción repetida, un manual de 20.000 tokens mandado dos veces por ejecución sin
  caché y un bucle que consulta el manual una y otra vez sin avanzar;
* el prompt gestionado `atencion` pasa de v1 a v2 hace nueve días, más largo y más caro
  por ejecución: una regresión que el Panel atribuye a la versión;
* hace tres días, durante seis horas, alguien prueba `gpt-5.5-pro` en la respuesta: un
  pico con causa;
* la repetición se arregla hace cinco días, y a partir de ahí desaparece;
* un 3 % de ejecuciones falla por un tiempo de espera de la herramienta de tarifas.

Se emiten con el mismo SDK y llegan por la misma ingesta que cualquier traza real. Lo
único distinto es el reloj: OpenTelemetry toma la hora de `time_ns`, y aquí se sustituye
por uno simulado mientras dura la generación, para fechar cada llamada en el pasado y con
una latencia creíble. Lo que no son trazas —versiones de prompt con su fecha,
anotaciones, la comparación A/B, el arreglo marcado— lo escribe el backend, que es quien
tiene los metadatos (`laplace_backend.demo`).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import decorators, manual, semconv
from ._tracer import flush, init
from .decorators import get_current_trace_id, set_context

MODELO_CARO = "gpt-5.6-terra"
MODELO_BARATO = "gpt-5.6-luna"
#: El que alguien prueba durante el pico.
MODELO_PICO = "gpt-5.5-pro"

DIAS = 30
#: Cuándo pasa cada cosa, en días hacia atrás desde ahora.
DIA_PROMPT_V2 = 9
DIA_ARREGLO_REPETICION = 5
DIA_PICO = 3
HORAS_PICO = (14, 20)

PROMPT = "atencion"
PROMPT_V1 = (
    "Eres el asistente de Vuelos Laplace. Responde usando exclusivamente el manual de "
    "condiciones que va a continuación. Si la respuesta no está en el manual, dilo."
)
PROMPT_V2 = (
    PROMPT_V1
    + " Antes de responder, repasa estos ejemplos de conversaciones bien resueltas y "
    "copia su tono. Cita siempre el apartado del manual del que sale la respuesta."
)
#: Lo que el prompt pesa en tokens con el manual dentro. v2 lleva ejemplos: más cara.
TOKENS_V1 = 20_000
TOKENS_V2 = 26_000
MANUAL = "Condiciones de la tarifa. " * 40

PREGUNTAS = [
    "¿Cuánto equipaje puedo llevar?",
    "¿Puedo facturar una maleta grande?",
    "¿Cabe una mochila debajo del asiento?",
    "¿Qué pasa si me paso de peso?",
    "¿El carrito del bebé cuenta como equipaje?",
    "¿Puedo llevar una guitarra en cabina?",
    "¿Cuánto cuesta una maleta extra?",
    "¿Puedo llevar líquidos?",
    "¿Puedo cambiar la fecha de mi vuelo?",
    "¿Qué pasa si pierdo la conexión?",
    "¿Me devuelven el dinero si cancelan?",
    "¿Puedo elegir asiento gratis?",
]

INTENCIONES = ["equipaje", "cambios", "reembolso", "asientos"]


# ---------------------------------------------------------------------------------
# El reloj
# ---------------------------------------------------------------------------------


class RelojSimulado:
    """La hora que ven los spans mientras se genera la demo."""

    def __init__(self, inicio: datetime) -> None:
        self.ahora_ns = int(inicio.timestamp() * 1e9)

    def __call__(self) -> int:
        return self.ahora_ns

    def ir_a(self, momento: datetime) -> None:
        self.ahora_ns = int(momento.timestamp() * 1e9)

    def avanzar(self, segundos: float) -> None:
        self.ahora_ns += int(segundos * 1e9)


@contextmanager
def reloj_de_otel(reloj: Callable[[], int]) -> Iterator[None]:
    """Sustituye la hora con la que OpenTelemetry fecha los spans.

    `opentelemetry.sdk.trace` importa `time_ns` en su propio espacio de nombres y fecha
    con él el principio y el final de cada span. Si una versión futura deja de hacerlo,
    esto falla en voz alta en vez de fechar la demo en el presente sin decir nada
    (`test_demo.py` lo comprueba).

    Afecta a todo el proceso mientras dura: por eso sólo se usa aquí, en una generación
    corta y con un proyecto propio.
    """
    import opentelemetry.sdk.trace as sdk_trace

    if not hasattr(sdk_trace, "time_ns"):
        raise RuntimeError(
            "esta versión de opentelemetry-sdk no toma la hora de `time_ns`: la demo "
            "no puede fechar sus trazas en el pasado"
        )
    original = sdk_trace.time_ns
    sdk_trace.time_ns = reloj  # type: ignore[assignment]
    try:
        yield
    finally:
        sdk_trace.time_ns = original  # type: ignore[assignment]


_reloj: RelojSimulado | None = None


@contextmanager
def en_el_pasado(momento: datetime) -> Iterator[RelojSimulado]:
    """Todo lo que se traza dentro ocurre a partir de `momento`, con su latencia."""
    global _reloj
    reloj = RelojSimulado(momento)
    _reloj = reloj
    try:
        with reloj_de_otel(reloj):
            yield reloj
    finally:
        _reloj = None


#: La traza de la última ejecución de `responder`, para que el backend pueda anotarla.
_ultima_traza: str | None = None


def _pasa(segundos: float) -> None:
    if _reloj is not None:
        _reloj.avanzar(segundos)


# ---------------------------------------------------------------------------------
# El agente
# ---------------------------------------------------------------------------------


@dataclass
class Escenario:
    """Lo que cambia de una ejecución a otra según el día y la hora."""

    version_prompt: int = 1
    modelo_respuesta: str = MODELO_CARO
    repeticion: bool = True
    bucle: bool = False
    falla: bool = False


def _llamada(
    instrucciones: str,
    pregunta: str,
    respuesta: str,
    *,
    entrada: int,
    segundos: float,
    modelo: str = MODELO_CARO,
    prompt: tuple[str, int] | None = None,
) -> None:
    with manual.llm_span(
        model=modelo,
        system="openai",
        input_messages=[
            {"role": "system", "content": instrucciones},
            {"role": "user", "content": pregunta},
        ],
        temperature=0,
    ) as llm:
        if prompt is not None:
            llm.span.set_attribute(semconv.LAPLACE_PROMPT_NAME, prompt[0])
            llm.span.set_attribute(semconv.LAPLACE_PROMPT_VERSION, prompt[1])
        _pasa(segundos)
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=entrada,
            output_tokens=len(respuesta) // 4 + 4,
            response_model=modelo,
            finish_reasons=["stop"],
        )


@decorators.observe(type="tool")
def buscar_tarifa(pregunta: str, falla: bool = False) -> str:
    if falla:
        _pasa(10.0)
        raise TimeoutError("la API de tarifas no respondió en 10 s")
    _pasa(random.uniform(0.15, 0.45))
    return "tarifa-basica"


@decorators.observe(type="chain")
def consultar_manual(pregunta: str, intento: int) -> str:
    """Relee el manual por si esta vez dice otra cosa. No la dice nunca."""
    _llamada(
        f"Busca en el manual la sección que responde a la pregunta.\n{MANUAL}",
        f"{pregunta} (intento {intento})",
        "Sección 4.2: equipaje de mano.",
        entrada=6_000 + intento,
        segundos=random.uniform(0.9, 1.6),
    )
    return "Sección 4.2: equipaje de mano."


RESPUESTA = (
    "Puedes llevar una maleta de mano de hasta 10 kg y un artículo personal que quepa "
    "debajo del asiento, incluidos en todas las tarifas salvo la básica. En la básica "
    "sólo entra el artículo personal; la maleta de mano se añade desde «Mis reservas» "
    "por 18 € por trayecto, o por 25 € en el aeropuerto. Las medidas máximas son "
    "55 × 40 × 20 cm. (Condiciones de la tarifa, apartado 4.2)."
)
CATEGORIAS = "Categorías posibles: " + ", ".join(INTENCIONES) + ". " + "Definición. " * 300


@decorators.observe(type="agent")
def responder(pregunta: str, escenario: Escenario | None = None) -> str:
    """El agente de atención de Vuelos Laplace, con sus cuatro patologías."""
    global _ultima_traza
    _ultima_traza = get_current_trace_id()
    esc = escenario or Escenario()
    version = esc.version_prompt
    sistema = f"{PROMPT_V2 if version == 2 else PROMPT_V1}\n{MANUAL}"
    tokens_sistema = TOKENS_V2 if version == 2 else TOKENS_V1

    # 1. Modelo caro para una respuesta de una palabra, con el catálogo de categorías.
    _llamada(
        f"Clasifica la intención del usuario en una sola palabra.\n{CATEGORIAS}",
        pregunta,
        random.choice(INTENCIONES),
        entrada=1_500,
        segundos=random.uniform(0.5, 1.1),
    )
    # 2. La misma extracción sobre el historial de la conversación, repetida con la
    #    misma entrada hasta que se arregla.
    for _ in range(3 if esc.repeticion else 1):
        _llamada(
            "Devuelve origen y destino en JSON. Sólo JSON.",
            f"Historial de la conversación:\n{pregunta}",
            '{"origen": "MAD", "destino": "BCN"}',
            entrada=2_500,
            segundos=random.uniform(0.6, 1.0),
        )
    # 3. Planificar con el manual entero delante, y otra vez para responder: dos veces
    #    el mismo prefijo de 20.000 tokens por ejecución, sin caché.
    _llamada(
        sistema,
        f"{pregunta}\n¿Qué herramienta necesitas? Responde en JSON.",
        '{"herramienta": "buscar_tarifa"}',
        entrada=tokens_sistema + 40,
        segundos=random.uniform(1.2, 2.4),
        prompt=(PROMPT, version),
    )
    buscar_tarifa(pregunta, falla=esc.falla)
    # 4. Un bucle: relee el manual cinco veces sin que cambie la respuesta.
    if esc.bucle:
        for intento in range(1, 6):
            consultar_manual(pregunta, intento)
    _llamada(
        sistema,
        f"{pregunta}\nTarifa: básica.",
        RESPUESTA,
        entrada=tokens_sistema + 80,
        segundos=random.uniform(2.4, 5.8) * (2.5 if esc.modelo_respuesta == MODELO_PICO else 1),
        modelo=esc.modelo_respuesta,
        prompt=(PROMPT, version),
    )
    return RESPUESTA


# ---------------------------------------------------------------------------------
# El mes
# ---------------------------------------------------------------------------------


@dataclass
class TrazaDemo:
    trace_id: str
    cuando: datetime
    pregunta: str
    version_prompt: int
    falla: bool


@dataclass
class ResultadoDemo:
    """Lo que el backend necesita para escribir los metadatos que van con las trazas."""

    project: str
    ahora: datetime
    trazas: list[TrazaDemo] = field(default_factory=list)

    def momento(self, dias_atras: float) -> datetime:
        return self.ahora - timedelta(days=dias_atras)


#: Peso de cada hora del día: poco de madrugada, punta a media mañana y a media tarde.
_PESO_HORA = [1, 1, 1, 1, 1, 2, 3, 5, 7, 9, 10, 10, 9, 8, 8, 9, 10, 9, 7, 6, 5, 3, 2, 1]


def _ejecuciones_del_dia(dias_atras: int, rng: random.Random) -> int:
    """De unas 14 al día hace un mes a unas 40 hoy, con ruido."""
    base = 14 + (DIAS - dias_atras) * 26 / DIAS
    return max(3, round(base * rng.uniform(0.85, 1.15)))


def _escenario(momento: datetime, ahora: datetime, rng: random.Random) -> Escenario:
    dias = (ahora - momento).total_seconds() / 86_400
    pico = (
        DIA_PICO - 1 < dias <= DIA_PICO
        and HORAS_PICO[0] <= momento.astimezone(timezone.utc).hour < HORAS_PICO[1]
    )
    return Escenario(
        version_prompt=2 if dias <= DIA_PROMPT_V2 else 1,
        modelo_respuesta=MODELO_PICO if pico else MODELO_CARO,
        repeticion=dias > DIA_ARREGLO_REPETICION,
        bucle=rng.random() < 0.22,
        falla=rng.random() < 0.03,
    )


#: A qué empresa cliente pertenece cada usuario de la demo (D-161). La primera
#: concentra el uso —sus usuarios son los que más escriben—, que es el caso que el margen
#: por cliente tiene que enseñar: el cliente que más trabajo da no siempre es el que más
#: deja.
CLIENTES_DEMO = ("iberviajes", "hoteles-mar", "agencia-sol", "particulares")


def cliente_de(usuario: str) -> str:
    n = int(usuario.rsplit("-", 1)[-1])
    if n <= 3:
        return CLIENTES_DEMO[0]
    if n <= 10:
        return CLIENTES_DEMO[1]
    if n <= 20:
        return CLIENTES_DEMO[2]
    return CLIENTES_DEMO[3]


def generar_mes(
    endpoint: str,
    project: str = "demo",
    *,
    ahora: datetime | None = None,
    dias: int = DIAS,
    semilla: int = 7,
) -> ResultadoDemo:
    """Emite un mes de tráfico fechado en el pasado y espera a que salga."""
    rng = random.Random(semilla)
    random.seed(semilla)
    fin = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    resultado = ResultadoDemo(project=project, ahora=fin)
    usuarios = [f"u-{n:02d}" for n in range(1, 41)]
    pesos = [1 / n for n in range(1, 41)]  # unos pocos clientes concentran el uso

    init(project=project, endpoint=endpoint, service_name="vuelos-laplace-atencion")
    with en_el_pasado(fin - timedelta(days=dias)) as reloj:
        # Hasta hoy incluido, sin pasar de ahora (el `continue` de abajo). Terminar en
        # ayer a medianoche dejaba un hueco que, por la tarde, pasaba del día que el
        # Diagnóstico espera antes de dar algo por resuelto (D-135, D-146).
        for dias_atras in range(dias, -1, -1):
            dia = (fin - timedelta(days=dias_atras)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            total = _ejecuciones_del_dia(dias_atras, rng)
            horas = rng.choices(range(24), weights=_PESO_HORA, k=total)
            for hora in sorted(horas):
                momento = dia + timedelta(hours=hora, seconds=rng.uniform(0, 3_540))
                if momento >= fin:
                    continue
                reloj.ir_a(momento)
                esc = _escenario(momento, fin, rng)
                usuario = rng.choices(usuarios, weights=pesos)[0]
                set_context(
                    session_id=f"{usuario}-{momento:%m%d}",
                    user_id=usuario,
                    customer_id=cliente_de(usuario),
                )
                pregunta = rng.choice(PREGUNTAS)
                trace_id = _una_ejecucion(pregunta, esc)
                if trace_id:
                    resultado.trazas.append(
                        TrazaDemo(trace_id, momento, pregunta, esc.version_prompt, esc.falla)
                    )
            # Un lote por día: el procesador por lotes tiene una cola finita y un
            # mes entero de golpe la desbordaría y perdería spans sin avisar.
            flush()
    flush()
    return resultado


def _una_ejecucion(pregunta: str, esc: Escenario) -> str | None:
    """Una ejecución del agente, que puede fallar como falla en producción."""
    global _ultima_traza
    _ultima_traza = None
    try:
        responder(pregunta, esc)
    except TimeoutError:
        pass
    return _ultima_traza


def enviar_trazas_de_ejemplo(endpoint: str, project: str = "demo") -> int:
    """Emite el mes de tráfico. Devuelve cuántas ejecuciones ha mandado.

    Sólo las trazas: para la demo entera, con prompts, evaluaciones y el arreglo
    marcado, está `laplace_backend.demo.cargar_demo`, que es lo que llama `laplace demo`.
    """
    return len(generar_mes(endpoint, project).trazas)
