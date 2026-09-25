"""`@observe` sobre funciones que hacen `yield`.

Un paso de agente que va devolviendo la respuesta a trozos es lo normal, no un caso
raro. Con el decorador de antes, el span se cerraba al CREAR el generador —antes de
ejecutar una sola línea—, así que duraba cero, no tenía salida y las llamadas al modelo
hechas mientras se iteraba colgaban de otro padre. El árbol y la identidad de paso salían
mal justo en los agentes con streaming.
"""

from __future__ import annotations

import asyncio

import laplace
from helpers import exporter


@laplace.observe(type="tool")
def _buscar(consulta: str) -> str:
    return f"resultado de {consulta}"


def _por_nombre():
    return {s.name: s for s in exporter.get_finished_spans()}


def test_un_generador_abarca_todo_lo_que_pasa_mientras_se_itera():
    @laplace.observe(type="agent")
    def responder(pregunta: str):
        yield "Buscando…"
        yield _buscar(pregunta)

    trozos = list(responder("equipaje"))

    assert trozos == ["Buscando…", "resultado de equipaje"]
    spans = _por_nombre()
    padre, hijo = spans["responder"], spans["_buscar"]
    assert hijo.parent is not None and hijo.parent.span_id == padre.context.span_id, (
        "lo que se llama mientras se itera cuelga del generador"
    )
    assert padre.end_time >= hijo.end_time, "el span del generador dura hasta agotarlo"
    assert "resultado de equipaje" in padre.attributes.get("laplace.output", ""), (
        "la salida son los trozos que produjo"
    )


def test_el_contexto_no_se_escapa_al_consumidor_entre_trozos():
    """Entre dos `yield` manda el que itera: lo que él llame no es hijo del generador."""

    @laplace.observe(type="agent")
    def responder():
        yield 1
        yield 2

    for _ in responder():
        _buscar("fuera")

    spans = exporter.get_finished_spans()
    fuera = [s for s in spans if s.name == "_buscar"]
    assert fuera and all(s.parent is None for s in fuera), [s.parent for s in fuera]


def test_cortar_la_iteracion_a_medias_cierra_el_span_sin_error():
    @laplace.observe(type="agent")
    def infinito():
        n = 0
        while True:
            yield n
            n += 1

    for n in infinito():
        if n == 2:
            break

    (span,) = [s for s in exporter.get_finished_spans() if s.name == "infinito"]
    assert span.status.status_code.name != "ERROR", "dejar de leer no es un fallo"


def test_un_error_dentro_del_generador_queda_en_su_span():
    @laplace.observe(type="agent")
    def roto():
        yield 1
        raise ValueError("se rompió")

    try:
        list(roto())
    except ValueError:
        pass

    (span,) = [s for s in exporter.get_finished_spans() if s.name == "roto"]
    assert span.status.status_code.name == "ERROR"


def test_un_generador_asincrono_tambien():
    @laplace.observe(type="agent")
    async def responder(pregunta: str):
        yield "Buscando…"
        await asyncio.sleep(0)
        yield _buscar(pregunta)

    async def consumir():
        return [t async for t in responder("maletas")]

    assert asyncio.run(consumir()) == ["Buscando…", "resultado de maletas"]
    spans = _por_nombre()
    padre, hijo = spans["responder"], spans["_buscar"]
    assert hijo.parent is not None and hijo.parent.span_id == padre.context.span_id
    assert padre.end_time >= hijo.end_time
