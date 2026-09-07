"""Un agente escrito como lo escribe alguien que acaba de instalar Laplace.

El otro ejemplo, `agente_ejemplo.py`, está instrumentado con cuidado: cada paso lleva su
decorador y su tipo. Éste no. Aquí hay lo que sale de leer las diez primeras líneas del
README y aplicarlas con prisa:

  * `laplace.init(...)` y un `@observe` en la función de entrada. Nada más.
  * Las funciones internas **no** están decoradas, así que todas las llamadas al modelo
    cuelgan del mismo span y no hay ningún nombre que las distinga.
  * Ningún `name=` en ninguna parte. El span de una llamada al modelo se llama
    `chat <modelo>`, igual para todas.

Existe para responder a una pregunta concreta: **¿sirven las tres reglas de detección
cuando nadie ha nombrado nada?** Si el panel de ahorro de este agente sale vacío o dice
tonterías, el producto no funciona para su primer usuario, que es el único que importa
antes de que haya un segundo.

Lo que lo hace posible es que la identidad de un paso no es su nombre, sino desde dónde
se llama y con qué instrucciones (D-060). Aquí el «desde dónde» es el mismo para todo,
así que el trabajo lo hace entero la huella de las instrucciones.

    python examples/agente_sin_instrumentar.py

Variables:
    LAPLACE_ENDPOINT   por defecto http://localhost:8000
    LAPLACE_PROJECT    por defecto demo-crudo
"""

from __future__ import annotations

import os
import random
import sys
import time

import laplace

MODELO = "gpt-5.6-terra"

# Un manual de producto que va pegado a cada consulta. Nadie lo ha marcado como
# cacheable porque nadie sabe todavía que se puede.
MANUAL = "Condiciones de la tarifa. " * 40


def preguntar(instrucciones: str, pregunta: str, respuesta: str, entrada: int) -> str:
    """Lo que en un agente de verdad sería `client.chat.completions.create(...)`.

    Se usa `llm_span` en vez de la instrumentación automática sólo para no necesitar
    una clave de API: emite exactamente los mismos atributos, incluido el nombre por
    defecto `chat <modelo>` y el paso que la envuelve.
    """
    with laplace.llm_span(
        model=MODELO,
        system="openai",
        input_messages=[
            {"role": "system", "content": instrucciones},
            {"role": "user", "content": pregunta},
        ],
        temperature=0,
    ) as llm:
        time.sleep(random.uniform(0.02, 0.1))
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=entrada,
            output_tokens=len(respuesta) // 4 + 4,
            response_model=MODELO,
            finish_reasons=["stop"],
        )
    return respuesta


# --- Funciones internas, sin decorar. Así es como quedan la primera semana. ---------


def clasificar(pregunta: str) -> str:
    """Salida de tres palabras con el modelo grande: el derroche más común de todos."""
    return preguntar(
        "Clasifica la intención del usuario en una sola palabra.",
        pregunta,
        "equipaje",
        entrada=45,
    )


def consultar_manual(pregunta: str) -> str:
    """El manual entero en cada llamada, a tarifa completa."""
    return preguntar(
        f"Responde usando exclusivamente este manual:\n{MANUAL}",
        pregunta,
        "Equipaje de mano incluido en todas las tarifas menos la básica.",
        entrada=20_000,
    )


def extraer_json(pregunta: str) -> dict:
    """Reintenta con el mismo prompt cuando la respuesta no parsea. Cada vuelta se paga."""
    for _ in range(3):
        preguntar(
            "Devuelve origen y destino en JSON. Sólo JSON.",
            pregunta,
            '{"origen": "MAD"',
            entrada=60,
        )
    return {"origen": "MAD", "destino": "BCN"}


@laplace.observe(type="agent")
def responder(pregunta: str) -> str:
    """Lo único decorado en todo el archivo, tal cual sale del README."""
    clasificar(pregunta)
    extraer_json(pregunta)
    for _ in range(4):
        consultar_manual(pregunta)
    return "Equipaje de mano incluido en todas las tarifas menos la básica."


def main() -> int:
    endpoint = os.getenv("LAPLACE_ENDPOINT", "http://localhost:8000")
    laplace.init(
        project=os.getenv("LAPLACE_PROJECT", "demo-crudo"),
        endpoint=endpoint,
        service_name="agente-de-equipaje",
    )
    print(f"enviando trazas a {endpoint}")

    # Doce ejecuciones, no tres: las reglas exigen un mínimo de llamadas antes de
    # afirmar nada, y con tres conversaciones un panel vacío sería el resultado
    # correcto. Lo que hay que comprobar aquí es que con tráfico normal salgan
    # diagnósticos útiles sin haber nombrado un solo paso.
    preguntas = [
        "¿Cuánto equipaje puedo llevar?",
        "¿Puedo facturar una maleta grande?",
        "¿Cabe una mochila debajo del asiento?",
        "¿Qué pasa si me paso de peso?",
        "¿El carrito del bebé cuenta como equipaje?",
        "¿Puedo llevar una guitarra en cabina?",
        "¿Cuánto cuesta una maleta extra?",
        "¿Hay límite de tamaño para el equipaje de mano?",
        "¿Puedo llevar líquidos?",
        "¿Qué pasa si mi maleta se pierde?",
        "¿Puedo cambiar el equipaje después de comprar?",
        "¿Los bebés tienen franquicia de equipaje?",
    ]
    for i, pregunta in enumerate(preguntas, start=1):
        laplace.set_context(session_id=f"conv-{i}", user_id=f"u-{i % 3}")
        responder(pregunta)
        print(f"  [{i:2d}] {pregunta}")

    laplace.flush()
    print("listo. Ninguna llamada lleva nombre propio; el panel tiene que salir igual.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
