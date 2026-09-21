"""La pila de pasos abiertos, que es lo que dice **desde dónde** se llama al modelo.

La identidad de un paso son dos mitades: desde dónde se llama y con qué instrucciones
(D-060). La primera mitad se resolvía mirando el nombre del span que envuelve la
llamada, y eso resultó ser demasiado poco: dos agentes distintos con una función que se
llama igual —`resumir_para_crm` en los dos— quedaban mezclados bajo el mismo sitio, y
con ellos sus poblaciones de llamadas. Uno sano compensaba a uno roto y la señal de
cobertura se callaba justo cuando había algo que decir (D-106).

Aquí se guarda el **camino entero**: `atender_ticket > resumir_para_crm`. Dos agentes
que comparten el nombre de un paso ya no comparten su camino, y un mismo ayudante
llamado desde dos sitios del mismo programa tampoco.

Vive en su propio módulo porque lo escriben los decoradores y lo leen las
integraciones, y hacer que uno importe al otro daría un ciclo.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

#: Separador del camino. Con espacios porque se enseña en pantalla tal cual.
SEPARADOR = " > "

#: Tope de profundidad. Un agente recursivo podría crecer sin límite y el camino acaba
#: en un atributo de span y en una columna: se queda con los últimos tramos, que son
#: los que distinguen la llamada.
MAX_TRAMOS = 6

_pila: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "laplace_pila_pasos", default=()
)


@contextmanager
def entrar(nombre: str) -> Iterator[None]:
    """Apila un paso mientras dure su bloque.

    Un `ContextVar` y no una lista global: así funciona con hilos y con `asyncio`, que
    es como corren los agentes de verdad. `reset` con el token en vez de un `pop` por
    la misma razón: en concurrencia, quitar el último no tiene por qué quitar el tuyo.
    """
    token = _pila.set((*_pila.get(), nombre))
    try:
        yield
    finally:
        _pila.reset(token)


def camino() -> str:
    """El camino de pasos abiertos, o cadena vacía si no hay ninguno."""
    pila = _pila.get()
    return SEPARADOR.join(pila[-MAX_TRAMOS:]) if pila else ""


def hoja() -> str | None:
    """El paso más cercano, que es el que envuelve la llamada."""
    pila = _pila.get()
    return pila[-1] if pila else None
