"""Lo que la demo de un mes destapó en el motor, y que dieciséis trazas no podían.

Con tráfico de verdad —preguntas distintas, dos versiones de un prompt, experimentos—
la lista del inicio pasó de seis cosas que arreglar a veintisiete, y casi todas eran la
misma dicha varias veces. Cada prueba de aquí es una de esas formas de multiplicarse.
"""

from __future__ import annotations

import uuid

import pytest
from test_catalogo_hallazgos import _span, _ventana

from laplace_backend import insights
from laplace_backend.ingest.otlp import loop_hash
from laplace_backend.storage.sqlite import SQLiteStore


def _bucle(traza: str, pregunta: str, base: int) -> list:
    """Cinco vueltas del mismo paso sobre UNA pregunta, con la misma respuesta."""
    salida = [{"role": "assistant", "content": "Sección 4.2."}]
    spans = []
    for intento in range(1, 6):
        entrada = [{"role": "user", "content": f"{pregunta} (intento {intento})"}]
        spans.append(
            _span(
                traza,
                paso="consultar_manual",
                clave="k-consultar",
                entrada_tokens=6_000,
                salida_tokens=8,
                i=base + intento,
                dedup=f"{traza}-{intento}",
                entrada_msgs=entrada,
                salida_msgs=salida,
                bucle=(
                    loop_hash("llm", "chat", entrada),
                    loop_hash("llm", "chat", salida, ignorar_numeros=False),
                ),
            )
        )
    return spans


def test_un_bucle_con_preguntas_distintas_es_un_solo_hallazgo(tmp_path):
    """El mismo paso atascado con doce preguntas de usuario no son doce problemas.

    La vuelta se reconoce por la entrada sin números (`loop_hash`), y eso está bien
    DENTRO de una ejecución. Pero el hallazgo se agrupaba también por ese hash, así que
    cada pregunta distinta abría una tarjeta nueva: con tráfico real, donde no hay dos
    preguntas iguales, la lista se llenaba del mismo bucle. El problema es del paso.
    """
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    preguntas = ["¿Cuánto equipaje?", "¿Puedo cambiar el vuelo?", "¿Me devuelven el dinero?"]
    spans = []
    for n, pregunta in enumerate(preguntas):
        for t in range(2):
            spans += _bucle(f"t-{n}-{t}", pregunta, base=n * 100 + t * 20)
    store.insert_spans(spans)

    bucles = [f for f in insights.detect(store, "catalogo", _ventana()) if f.kind == "bucle"]
    assert len(bucles) == 1, [f.title for f in bucles]
    assert "6" in bucles[0].scope_label or "ejecuciones" in bucles[0].scope_label, (
        "y el hallazgo cuenta las seis ejecuciones, no las dos de una pregunta"
    )
    assert insights.detail(store, "catalogo", _ventana(), bucles[0].id) is not None


@pytest.mark.parametrize("almacen", ["local", "nube"])
def test_una_tirada_de_evaluacion_no_es_algo_que_arreglar(tmp_path, almacen):
    """Probar el modelo caro contra el barato a propósito no es «usas el modelo caro».

    Las tiradas de `run_dataset` llegan por la ingesta normal, que es lo correcto: son
    gasto de verdad y su coste se cuenta. Pero el inicio las enseñaba como problemas
    —«usas el modelo caro para respuestas cortas: eval:gpt-5.6-terra → responder»—, y un
    experimento que alguien lanzó para decidir no es un derroche que alguien no vio.
    """
    from datetime import timedelta

    from laplace.schema import Span
    from test_catalogo_hallazgos import AHORA

    if almacen == "nube":
        from test_prompts import _nube

        store = _nube()
        proyecto = f"eval-{uuid.uuid4().hex[:8]}"
    else:
        store = SQLiteStore(tmp_path / "l.db")
        store.migrate()
        proyecto = "catalogo"
    spans = []
    for t in range(12):
        raiz = Span(
            span_id=f"raiz{t:012d}",
            trace_id=f"{proyecto}-{t}",
            project_id=proyecto,
            name="eval:gpt-5.6-terra",
            type="agent",
            status="ok",
            start_time=AHORA + timedelta(seconds=t * 20),
            end_time=AHORA + timedelta(seconds=t * 20 + 2),
            duration_ms=2000.0,
            tags=["laplace-eval", "variant:gpt-5.6-terra"],
        )
        hijo = _span(
            f"{proyecto}-{t}",
            paso="responder",
            clave="k-eval-responder",
            entrada_tokens=3_200,
            salida_tokens=8,
            i=t * 20 + 1,
            dedup=f"eval-{t}",
        )
        hijo.parent_span_id = raiz.span_id
        hijo.project_id = proyecto
        spans += [raiz, hijo]
    store.insert_spans(spans)

    try:
        hallazgos = insights.detect(store, proyecto, _ventana())
        assert hallazgos == [], [f.title for f in hallazgos]
        assert store.summarize_window(proyecto, _ventana()).total_cost_usd > 0, (
            "y su gasto sí se cuenta: es dinero que se ha ido de verdad"
        )
    finally:
        if almacen == "nube":
            store.delete_project(proyecto)


def test_dos_versiones_de_un_prompt_no_se_titulan_igual():
    """v1 y v2 del prompt de `responder` empiezan igual: la pista recortada a 32
    caracteres era la misma y el inicio enseñaba dos tarjetas idénticas con cifras
    distintas. Se lee como un duplicado, que es el fallo de D-115 por otro camino."""
    from laplace_backend.storage.base import ModelUsage, disambiguate

    comun = "Eres el asistente de Vuelos Laplace. Responde usando el manual."
    filas = [
        ModelUsage(key="k1", name="responder", model="gpt-5.6-terra", site="responder",
                   hint=comun),
        ModelUsage(key="k2", name="responder", model="gpt-5.6-terra", site="responder",
                   hint=comun + " Antes de responder, repasa estos ejemplos."),
        ModelUsage(key="k2", name="responder", model="gpt-5.5-pro", site="responder",
                   hint=comun + " Antes de responder, repasa estos ejemplos."),
    ]
    nombres = [f.name for f in disambiguate(filas)]
    assert len(set(nombres)) == 3, nombres
    assert "gpt-5.5-pro" in nombres[2], "lo que distingue al tercero es el modelo"
    assert "Antes de responder" in nombres[1], "y al segundo, dónde cambia su prompt"


def test_el_contexto_fijo_no_dice_que_falta_una_tarifa_que_existe():
    """«gpt-5.6-terra no está en la tabla de precios», de un modelo que está.

    Un paso que manda el manual una sola vez por ejecución no se ahorra nada cacheando
    —la caché no sobrevive de una ejecución a otra, que es la suposición prudente—, y la
    regla llegaba a ahorro cero y lo explicaba con el único motivo que conocía: que no
    había tarifa. Es D-114 otra vez, por otra regla. Sin dinero que recuperar y con
    tarifa, no hay hallazgo; y si el modelo no ofrece caché, se dice eso.
    """
    from laplace_backend.insights import contexto_fijo
    from laplace_backend.storage.base import ModelUsage, WindowSummary

    def uso(modelo: str, llamadas: int, trazas: int) -> ModelUsage:
        return ModelUsage(
            key="k", name="responder", model=modelo, calls=llamadas, traces=trazas,
            input_tokens=20_000 * llamadas, output_tokens=150 * llamadas,
            avg_input_tokens=20_000.0, avg_output_tokens=150.0, min_input_tokens=20_000,
            p50_output_tokens=150.0,
        )

    resumen = WindowSummary(traces=40, llm_calls=80, total_cost_usd=5.0)
    una_por_ejecucion = contexto_fijo._fixed_context_finding(
        uso("gpt-5.6-terra", 40, 40), resumen, 7.0, 7.0
    )
    assert una_por_ejecucion is None, una_por_ejecucion and una_por_ejecucion.summary

    dos_por_ejecucion = contexto_fijo._fixed_context_finding(
        uso("gpt-5.6-terra", 80, 40), resumen, 7.0, 7.0
    )
    assert dos_por_ejecucion is not None and dos_por_ejecucion.costs_money

    sin_cache = contexto_fijo._fixed_context_finding(
        uso("gpt-5.5-pro", 80, 40), resumen, 7.0, 7.0
    )
    if sin_cache is not None:
        assert "no está en la tabla" not in sin_cache.cost_unavailable, sin_cache.cost_unavailable
        assert "no está en la tabla" not in sin_cache.summary, sin_cache.summary


@pytest.mark.parametrize("almacen", ["local", "nube"])
def test_lo_que_dejo_de_ocurrir_no_se_promete_como_ahorro(tmp_path, almacen):
    """La v1 de un prompt, sustituida hace nueve días, salía con «34 $ al mes».

    El paso caro ocurre sólo al principio de la ventana. Proyectarlo a futuro es
    prometer dinero por arreglar lo que ya no pasa: se aparta como `desaparecido`, con
    su fecha, y no cuenta en el evitable.
    """
    from datetime import timedelta

    from laplace_backend.storage.base import Window
    from test_catalogo_hallazgos import AHORA

    if almacen == "nube":
        from test_prompts import _nube

        store = _nube()
    else:
        store = SQLiteStore(tmp_path / "l.db")
        store.migrate()
    proyecto = f"ido-{uuid.uuid4().hex[:8]}"
    ventana = Window(since=AHORA - timedelta(days=7), until=AHORA + timedelta(hours=1), days=7)
    spans = []
    for t in range(12):
        s = _span(
            f"{proyecto}-{t}",
            paso="clasificar",
            clave="k-clasificar",
            entrada_tokens=900,
            salida_tokens=3,
            i=t,
            dedup=f"{proyecto}-{t}",
        )
        # Todo hace seis días: después, nada.
        s.start_time -= timedelta(days=6)
        s.end_time -= timedelta(days=6)
        s.project_id = proyecto
        spans.append(s)
    store.insert_spans(spans)
    try:
        vista = insights.overview(store, proyecto, ventana)
        assert vista.findings == [], [f.title for f in vista.findings]
        assert [f.state for f in vista.set_aside] == ["desaparecido"]
        assert vista.set_aside[0].last_seen is not None
        assert vista.window_avoidable_usd == 0
    finally:
        if almacen == "nube":
            store.delete_project(proyecto)


def test_una_repeticion_dice_que_paso_se_repite_cuando_el_nombre_es_compartido(tmp_path):
    """«Tu agente repite «responder»», cuando lo repetido era la extracción: todas las
    llamadas dentro de `responder` se llaman así. Si el nombre lo comparten varios pasos
    del proyecto, el título lleva la pista de sus instrucciones, en la lista y en la
    ficha por igual."""
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    spans = []
    for t in range(4):
        for copia in range(3):
            s = _span(f"r-{t}", paso="responder", clave="k-extraer", entrada_tokens=2_500,
                      salida_tokens=10, i=t * 50 + copia, dedup=f"extraer-{t}")
            s.step_hint = "Devuelve origen y destino en JSON."
            spans.append(s)
        s = _span(f"r-{t}", paso="responder", clave="k-clasificar", entrada_tokens=900,
                  salida_tokens=2, i=t * 50 + 10, dedup=f"clasificar-{t}")
        s.step_hint = "Clasifica la intención del usuario."
        spans.append(s)
    store.insert_spans(spans)

    (rep,) = [f for f in insights.detect(store, "catalogo", _ventana()) if f.kind == "repeticion"]
    assert "Devuelve origen" in rep.title, rep.title
    ficha = insights.detail(store, "catalogo", _ventana(), rep.id)
    assert ficha is not None and ficha.title == rep.title
