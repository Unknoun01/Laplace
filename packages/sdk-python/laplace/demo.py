"""Trazas de ejemplo para `laplace demo`.

Son datos **inventados**, en un proyecto llamado `demo`, y tanto el comando como la
propia interfaz lo dicen. Existen por una razón concreta: quien acaba de instalar
Laplace todavía no tiene un agente instrumentado, y una pantalla vacía no enseña qué
hace el producto. Con esto ve el panel de ahorro funcionando en el primer minuto y
luego instrumenta el suyo.

Se emiten con el mismo SDK y llegan por la misma ingesta que cualquier traza real: no
hay una vía especial que pudiera darles un trato distinto.
"""

from __future__ import annotations

from . import decorators, manual
from ._tracer import flush, init
from .decorators import set_context

MODELO_CARO = "gpt-5.6-terra"
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
]


def llamada(instrucciones: str, pregunta: str, respuesta: str, entrada: int) -> None:
    with manual.llm_span(
        model=MODELO_CARO,
        system="openai",
        input_messages=[
            {"role": "system", "content": instrucciones},
            {"role": "user", "content": pregunta},
        ],
        temperature=0,
    ) as llm:
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=entrada,
            output_tokens=len(respuesta) // 4 + 4,
            response_model=MODELO_CARO,
            finish_reasons=["stop"],
        )


@decorators.observe(type="tool")
def buscar_tarifa(pregunta: str) -> str:
    return "tarifa-basica"


@decorators.observe(type="agent")
def responder(pregunta: str) -> str:
    """Un agente con las tres patologías que Laplace sabe detectar."""
    # 1. Modelo caro para una respuesta de tres palabras.
    llamada("Clasifica la intención del usuario en una sola palabra.", pregunta, "equipaje", 45)
    # 2. El mismo paso repetido con la misma entrada.
    for _ in range(3):
        llamada("Devuelve origen y destino en JSON. Sólo JSON.", pregunta, '{"o": "MAD"', 60)
        buscar_tarifa(pregunta)
    # 3. Un manual entero reenviado sin caché en cada consulta.
    for _ in range(2):
        llamada(
            f"Responde usando exclusivamente este manual:\n{MANUAL}",
            pregunta,
            "Equipaje de mano incluido en todas las tarifas menos la básica.",
            20_000,
        )
    return "Equipaje de mano incluido."


def enviar_trazas_de_ejemplo(endpoint: str, project: str = "demo") -> int:
    """Emite las trazas y espera a que salgan. Devuelve cuántas ha mandado."""
    init(project=project, endpoint=endpoint, service_name="agente-de-ejemplo")
    for i, pregunta in enumerate(PREGUNTAS, start=1):
        set_context(session_id=f"demo-{i}", user_id=f"u-{i % 3}")
        responder(pregunta)
    flush()
    return len(PREGUNTAS)
