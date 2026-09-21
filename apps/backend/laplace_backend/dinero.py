"""El único sitio que decide si se puede afirmar una cifra de dinero.

Tres veces ha salido el mismo fallo, en tres pantallas distintas: un coste desconocido
llegando a la pantalla como un **cero**. El hallazgo de repetición diciendo «no gasta
tokens de más» porque el coste de las copias sobrantes salía a 0; el Panel enseñando
«Gasto total: 0 $» con el 100 % de las llamadas sin tarifa; y el inicio con un «$0»
enorme y el aviso debajo, que es el patrón que D-073 prohibió para la proyección.

No son tres fallos: es uno, repetido allí donde alguien vuelve a escribir «si el coste
es 0…». Por eso la decisión vive aquí y no en cada pantalla, y por eso hay un guardia
—`test_dinero_desconocido.py`— que recorre los modelos de la API y exige que toda cifra
en dólares venga con su compañera que dice si se puede afirmar (D-107).

La regla, en una frase: **cero sólo significa cero cuando sabemos los precios.** Si no
los sabemos, no hay cifra; hay un motivo y las cosas que sí se miden —tokens, llamadas,
tiempo—, que son datos y no estimaciones.
"""

from __future__ import annotations

#: Nombres que valen como «compañera» de una cifra en dólares dentro de un modelo de la
#: API: alguna de ellas tiene que estar para que la interfaz pueda saber si el número se
#: puede afirmar. La lista es cerrada a propósito: si hace falta una nueva, que se añada
#: aquí y se vea en el diff.
COMPANERAS = (
    "unknown",
    "cost_unknown",
    "cost_is_floor",
    "unknown_cost_spans",
    "unavailable",
    "cost_unavailable",
    "costs_money",
)


def motivo_sin_dinero(*, llm_calls: int, unknown_cost_calls: int) -> str:
    """Por qué no se puede poner precio, o cadena vacía si sí se puede.

    Se devuelve motivo **sólo cuando no hay ni una llamada con tarifa**: con tarifa
    parcial sí hay una cifra que afirmar —es un suelo—, y eso ya lo dicen los avisos de
    coste incompleto. Aquí se trata el caso en el que no hay nada que sumar, que es el
    de cualquiera que use modelos locales o un modelo recién salido.
    """
    if llm_calls <= 0 or unknown_cost_calls < llm_calls:
        return ""
    return (
        f"No podemos poner precio a esto: ninguna de las {llm_calls} llamadas al modelo "
        "tiene tarifa conocida. Lo que sí está medido —tokens, llamadas y tiempo— sale "
        "abajo."
    )


def se_puede_afirmar(*, llm_calls: int, unknown_cost_calls: int) -> bool:
    """Atajo legible para el caso contrario."""
    return not motivo_sin_dinero(llm_calls=llm_calls, unknown_cost_calls=unknown_cost_calls)
