"""Comparar dos versiones del agente antes de desplegar.

    python examples/evaluar_dos_versiones.py

Es el ciclo entero de la pestaña de Evaluaciones en veinte líneas útiles: coger tráfico
real, pasarlo por dos versiones y mirar acierto y coste a la vez.

**Laplace no ejecuta tu agente.** La tirada corre aquí, en este proceso, con tus claves
y tus dependencias; lo que llega al backend son las trazas por la vía normal más el
parte de qué caso produjo qué traza (D-086). Por eso este fichero es un script tuyo y no
un botón de la interfaz.

Antes de correrlo necesitas un conjunto de casos. Se crea desde la pestaña Evaluaciones
con un filtro del explorador, o con una llamada a la API:

    curl -X POST http://127.0.0.1:8100/api/datasets -H 'content-type: application/json' \\
      -d '{"project_id":"mi-agente","name":"regresiones","filter":{"sort":"recent"},"limit":50}'
"""

from __future__ import annotations

import os

import laplace
from laplace import manual

ENDPOINT = os.getenv("LAPLACE_ENDPOINT", "http://127.0.0.1:8100")
PROJECT = os.getenv("LAPLACE_PROJECT", "mi-agente")
CONJUNTO = os.getenv("LAPLACE_DATASET", "regresiones")

INSTRUCCIONES = "Responde a la consulta del cliente usando solo la información dada."


def responder(entrada, *, modelo: str) -> str:
    """El agente, parametrizado por el modelo. Aquí llamarías a tu proveedor de verdad.

    Lo único que Laplace necesita es que la llamada al modelo quede registrada: con las
    integraciones automáticas no tienes que hacer nada, y con `manual.llm_span` —como
    aquí— le dices tú los tokens.
    """
    pregunta = entrada.get("pregunta") if isinstance(entrada, dict) else str(entrada)
    with manual.llm_span(
        model=modelo,
        system="openai",
        input_messages=[
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": pregunta},
        ],
    ) as llm:
        respuesta = f"[{modelo}] respuesta a: {pregunta}"
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=1800,
            output_tokens=120,
        )
    return respuesta


def main() -> None:
    laplace.init(project=PROJECT, endpoint=ENDPOINT, service_name="evaluacion")

    # Una tirada por versión. El `variant` es como la llamarás en la comparación, así
    # que ponle algo que signifique algo dentro de tres semanas.
    actual = laplace.run_dataset(
        CONJUNTO,
        lambda entrada: responder(entrada, modelo="gpt-5.6-terra"),
        variant="terra (actual)",
        on_case=lambda i, total, _c: print(f"  A {i}/{total}", end="\r"),
    )
    print(f"\nA: {actual.cases} casos, {actual.failed} reventados — {actual.run_id}")

    candidata = laplace.run_dataset(
        CONJUNTO,
        lambda entrada: responder(entrada, modelo="gpt-5.6-luna"),
        variant="luna (candidata)",
        on_case=lambda i, total, _c: print(f"  B {i}/{total}", end="\r"),
    )
    print(f"\nB: {candidata.cases} casos, {candidata.failed} reventados — {candidata.run_id}")

    print(
        f"\nCompáralas en {ENDPOINT}/evaluaciones?project={PROJECT}\n"
        "\nUn aviso que la propia pantalla te dará: el acierto sólo sale en porcentaje\n"
        "cuando hay casos anotados suficientes. Hasta entonces verás los casos en bruto,\n"
        "porque un «94 %» sacado de cuatro casos se recuerda igual que uno de verdad y no\n"
        "informa de nada. El coste, en cambio, sale desde el primer caso: no es una\n"
        "muestra, es la factura."
    )


if __name__ == "__main__":
    main()
