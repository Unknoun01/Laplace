"""Sacar un prompt del código y dejar que Laplace mida cada versión.

    python examples/prompt_gestionado.py

Lo que demuestra este fichero, en orden:

1. **Pedirle el prompt a Laplace** con `laplace.get_prompt(...)`, pasándole siempre un
   `fallback=` con el texto que lleva el código. Laplace sirve texto, no ejecuta nada
   tuyo; y si Laplace no responde, tu agente **sigue funcionando** con la copia guardada
   o con la reserva (D-091). Un observatorio que pueda tumbar lo observado no vale.
2. **Que la versión usada queda escrita en la traza**, y sin que tú hagas nada: el SDK
   comprueba que el texto de esa versión va de verdad en los mensajes que mandas y sólo
   entonces la marca. Si coges el prompt y lo reescribes, no se marca, porque una
   versión mal atribuida ensucia las métricas de todas las demás.
3. **Que a partir de ahí la pestaña Prompts puede decir lo que cuesta y lo que acierta
   cada versión**, medido sobre el tráfico que la usó y no sobre una estimación.

Antes de correrlo, crea el prompt en la pestaña Prompts, o con una llamada a la API:

    curl -X POST http://127.0.0.1:8100/api/prompts -H 'content-type: application/json' \\
      -d '{"project_id":"mi-agente","name":"atencion","text":"Eres un asistente de {{empresa}}. Responde en menos de tres frases."}'
"""

from __future__ import annotations

import os

import laplace
from laplace import manual

ENDPOINT = os.getenv("LAPLACE_ENDPOINT", "http://127.0.0.1:8100")
PROJECT = os.getenv("LAPLACE_PROJECT", "mi-agente")
NOMBRE = os.getenv("LAPLACE_PROMPT", "atencion")

#: El texto que lleva el código. No sobra por tener el prompt en Laplace: es lo que
#: hace que un Laplace caído sea un aviso en el log y no una incidencia en producción.
RESERVA = "Eres un asistente de {{empresa}}. Responde en menos de tres frases."

PREGUNTAS = [
    "¿Cuánto equipaje puedo llevar?",
    "¿Puedo cambiar el vuelo?",
    "¿Devolvéis el dinero si cancelo?",
]


@laplace.observe(type="agent")
def responder(pregunta: str) -> str:
    # El prompt se pide dentro del paso, que es donde se va a usar. La caché del SDK
    # evita que esto sea una petición por llamada: dura un minuto, que es lo que tarda
    # como mucho un rollback en llegar a un proceso ya arrancado.
    sistema = laplace.get_prompt(NOMBRE, fallback=RESERVA)
    # `cache` es el estado normal y no se dice: la caché es lo que evita una petición
    # por llamada. `fallback` sí, porque significa que Laplace no respondía y esto
    # corrió con el texto del código; en la pestaña ese tráfico aparece contado aparte
    # y no como la versión de producción.
    if sistema.source == "fallback":
        print("  (Laplace no respondió: esto va con el prompt de reserva del código)")

    with manual.llm_span(
        model="gpt-5.6-luna",
        system="openai",
        input_messages=[
            # `render` sustituye `{{empresa}}` y **registra** el texto resultante: es lo
            # que permite al SDK comprobar después que ese texto iba de verdad aquí.
            {"role": "system", "content": sistema.render(empresa="Vuelos Laplace")},
            {"role": "user", "content": pregunta},
        ],
    ) as llm:
        respuesta = "Una maleta de mano y un bulto pequeño."
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=900,
            output_tokens=40,
            response_model="gpt-5.6-luna",
        )
    return respuesta


def main() -> None:
    laplace.init(project=PROJECT, endpoint=ENDPOINT)

    print(f"Pidiendo «{NOMBRE}» a Laplace en {ENDPOINT} …")
    for pregunta in PREGUNTAS:
        print(f"- {pregunta}")
        responder(pregunta)

    laplace.flush()
    servido = laplace.get_prompt(NOMBRE, fallback=RESERVA)
    print(
        f"\nListo. Esas {len(PREGUNTAS)} ejecuciones quedan marcadas con «{NOMBRE}» "
        f"v{servido.version}."
    )
    print(f"Míralas en {ENDPOINT}/prompts?project={PROJECT}")
    print(
        "Guarda otra versión, ponla en producción, vuelve a correr esto y la pestaña te "
        "dirá cuál cuesta menos por ejecución —y, en cuanto anotes unas cuantas, cuál "
        "acierta más—."
    )


if __name__ == "__main__":
    main()
