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

from . import cifras
from .textos import t

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


#: Trozos de texto con los que el producto afirma **no tener tarifa** para algo. La
#: lista existe porque el guardia de D-107 comprueba que haya un motivo y no que el
#: motivo sea cierto, y por ese hueco se coló una regla diciendo «gpt-5.6-luna no esta
#: en la tabla de precios» de un modelo que si estaba: era el mas barato y por eso no
#: tenia alternativa, que es otra cosa (D-114).
#:
#: `test_un_motivo_de_no_saber_el_dinero_tiene_que_ser_cierto` la usa para barrer los
#: hallazgos sobre trafico donde **todos** los modelos tienen tarifa: ahi ninguna de
#: estas frases puede aparecer. Cerrada a proposito: una forma nueva de decirlo se
#: anade aqui y se ve en el diff.
AFIRMACIONES_DE_SIN_TARIFA = (
    "no esta en la tabla de precios",
    "no tiene tarifa conocida",
    "sin tarifa conocida",
    "ninguna de las",  # «…llamadas al modelo tiene tarifa conocida»
)


def afirma_no_tener_tarifa(texto: str) -> bool:
    """Si un texto del producto dice que no hay tarifa para algo.

    Compara sin tildes para que una frase nueva no se escape por un acento.
    """
    plano = (
        texto.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u")
    )
    return any(frase in plano for frase in AFIRMACIONES_DE_SIN_TARIFA)


def motivo_sin_dinero(*, llm_calls: int, unknown_cost_calls: int) -> str:
    """Por qué no se puede poner precio, o cadena vacía si sí se puede.

    Se devuelve motivo **sólo cuando no hay ni una llamada con tarifa**: con tarifa
    parcial sí hay una cifra que afirmar —es un suelo—, y eso ya lo dicen los avisos de
    coste incompleto. Aquí se trata el caso en el que no hay nada que sumar, que es el
    de cualquiera que use modelos locales o un modelo recién salido.
    """
    if llm_calls <= 0 or unknown_cost_calls < llm_calls:
        return ""
    return t("dinero.sin_precio", llamadas=cifras.miles(llm_calls))
