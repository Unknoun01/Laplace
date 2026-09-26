"""Buscar dentro de lo que el agente dijo y le dijeron (Fase 4, D-144).

Hasta ahora la lista de trazas buscaba en el nombre de los pasos y en el id. Lo que se
busca de verdad es otra cosa: el número del pedido del que se quejó un cliente, la frase
que el modelo no debería haber dicho, el vuelo que la herramienta devolvió. Eso está en
el contenido —prompts, respuestas, argumentos y salidas de herramientas—, que es casi
todo el disco. Los dos almacenes tienen que encontrar lo mismo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import LLMAttributes, RetrievalAttributes, Span, ToolAttributes

from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceFilter
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)


def _nube():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    return store


@pytest.fixture(params=["sqlite", "clickhouse"])
def almacen(request, tmp_path):
    if request.param == "sqlite":
        store = SQLiteStore(tmp_path / "laplace.db")
        store.migrate()
        yield store, f"busqueda-{uuid.uuid4().hex[:8]}"
        return
    store = _nube()
    proyecto = f"busqueda-{uuid.uuid4().hex[:8]}"
    yield store, proyecto
    store.delete_project(proyecto)


def _buscar(store, proyecto: str, texto: str, **extra) -> set[str]:
    filtro = TraceFilter(
        project_id=proyecto,
        since=AHORA - timedelta(days=7),
        until=AHORA + timedelta(minutes=1),
        content=texto,
        **extra,
    )
    return {t.trace_id for t in store.list_traces(filtro).traces}


class Sembrado:
    """Crea trazas y las guarda de una vez."""

    def __init__(self, store, proyecto: str) -> None:
        self.store, self.proyecto, self._spans = store, proyecto, []

    def traza(self, cuando: datetime = AHORA, **partes) -> str:
        traza = uuid.uuid4().hex
        tipo = "tool" if "tool" in partes else "retrieval" if "retrieval" in partes else "llm"
        span = Span(
            span_id=uuid.uuid4().hex[:16],
            trace_id=traza,
            project_id=self.proyecto,
            name=partes.get("nombre", "paso"),
            type=tipo,
            start_time=cuando,
            end_time=cuando + timedelta(milliseconds=100),
            duration_ms=100.0,
        )
        if tipo == "llm":
            span.llm = LLMAttributes(
                request_model="gpt-5.6-luna",
                input_messages=partes.get("entrada", []),
                output_messages=partes.get("salida", []),
            )
        elif tipo == "tool":
            span.tool = ToolAttributes(name="buscar_pedido", **partes["tool"])
        else:
            span.retrieval = RetrievalAttributes(**partes["retrieval"])
        span.input = partes.get("input")
        self._spans.append(span)
        return traza

    def guardar(self) -> None:
        self.store.insert_spans(self._spans)
        self._spans = []


def _usuario(texto: str) -> list[dict]:
    return [{"role": "user", "content": texto}]


def _asistente(texto: str) -> list[dict]:
    return [{"role": "assistant", "content": texto}]


def test_encuentra_el_texto_en_el_prompt_la_respuesta_y_las_herramientas(almacen):
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    en_prompt = s.traza(entrada=_usuario("Mi pedido PD-48213 no ha llegado"))
    en_respuesta = s.traza(salida=_asistente("He cancelado el pedido PD-48213"))
    en_herramienta = s.traza(tool={"arguments": {"id": "x"}, "output": {"pedido": "PD-48213"}})
    en_argumentos = s.traza(tool={"arguments": {"pedido": "PD-48213"}, "output": None})
    en_recuperacion = s.traza(retrieval={"query": "estado de PD-48213"})
    en_entrada = s.traza(input={"pregunta": "¿dónde está PD-48213?"})
    s.traza(entrada=_usuario("Mi pedido PD-99999 no ha llegado"))
    s.guardar()
    assert _buscar(store, proyecto, "PD-48213") == {
        en_prompt,
        en_respuesta,
        en_herramienta,
        en_argumentos,
        en_recuperacion,
        en_entrada,
    }


def test_no_distingue_mayusculas_tampoco_con_tildes(almacen):
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    a = s.traza(entrada=_usuario("REEMBOLSO DEL ÁRBOL DE NAVIDAD"))
    b = s.traza(salida=_asistente("el reembolso del árbol ya está"))
    s.guardar()
    assert _buscar(store, proyecto, "Reembolso del Árbol") == {a, b}


def test_porcentaje_y_guion_bajo_se_buscan_tal_cual(almacen):
    """Sin escapar, «50%» sería «50 y lo que sea» y «id_1» casaría con «idX1»."""
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    con_porcentaje = s.traza(entrada=_usuario("descuento del 50% hoy"))
    s.traza(entrada=_usuario("descuento del 500 hoy"))
    con_guion = s.traza(entrada=_usuario("campo id_1 vacío"))
    s.traza(entrada=_usuario("campo idX1 vacío"))
    s.guardar()
    assert _buscar(store, proyecto, "del 50%") == {con_porcentaje}
    assert _buscar(store, proyecto, "id_1") == {con_guion}


def test_el_nombre_del_paso_no_es_contenido(almacen):
    """La búsqueda por nombre ya existe; mezclarlas haría que buscar «buscar» en el
    contenido devolviera todas las trazas con un paso llamado así."""
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    s.traza(nombre="reembolsar", entrada=_usuario("hola"))
    s.guardar()
    assert _buscar(store, proyecto, "reembolsar") == set()


def test_solo_busca_en_el_proyecto_y_en_la_ventana(almacen):
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    dentro = s.traza(entrada=_usuario("vuelo IB-6841"))
    s.traza(cuando=AHORA - timedelta(days=30), entrada=_usuario("vuelo IB-6841"))
    s.guardar()
    otro = Sembrado(store, proyecto + "-otro")
    otro.traza(entrada=_usuario("vuelo IB-6841"))
    otro.guardar()
    try:
        assert _buscar(store, proyecto, "IB-6841") == {dentro}
    finally:
        store.delete_project(proyecto + "-otro")


def test_se_combina_con_los_demas_filtros(almacen):
    store, proyecto = almacen
    s = Sembrado(store, proyecto)
    llm = s.traza(nombre="responder", entrada=_usuario("pedido PD-7777"))
    s.traza(tool={"arguments": {"pedido": "PD-7777"}, "output": None})
    s.guardar()
    assert _buscar(store, proyecto, "PD-7777", span_type="llm") == {llm}
    assert _buscar(store, proyecto, "PD-7777", search="responder") == {llm}


def test_la_traza_sale_entera_aunque_el_texto_este_en_un_span(almacen):
    """Se filtran trazas, no spans: el coste y los tokens de la fila son los de la traza
    entera, igual que con los demás filtros (D-123)."""
    store, proyecto = almacen
    traza = uuid.uuid4().hex
    raiz = Span(
        span_id="a" * 16,
        trace_id=traza,
        project_id=proyecto,
        name="agente",
        type="agent",
        start_time=AHORA,
        end_time=AHORA + timedelta(seconds=1),
        duration_ms=1000.0,
    )
    hijo = Span(
        span_id="b" * 16,
        parent_span_id="a" * 16,
        trace_id=traza,
        project_id=proyecto,
        name="chat",
        type="llm",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=500),
        duration_ms=500.0,
    )
    hijo.llm = LLMAttributes(request_model="gpt-5.6-luna", input_messages=_usuario("TK-31415"))
    store.insert_spans([raiz, hijo])
    [fila] = store.list_traces(
        TraceFilter(
            project_id=proyecto,
            since=AHORA - timedelta(days=1),
            until=AHORA + timedelta(minutes=1),
            content="tk-31415",
        )
    ).traces
    assert fila.span_count == 2


# ---------------------------------------------------------------------------------
# ClickHouse: la consulta usa el índice (D-144)
# ---------------------------------------------------------------------------------


def test_clickhouse_usa_el_indice_de_contenido():
    """El analizador nuevo de ClickHouse sólo aprovecha un índice sobre una expresión si
    la consulta escribe **exactamente** la misma expresión, y basta una constante —un
    separador entre columnas— para que deje de reconocerla. Sin índice, buscar un id
    recorre todo el contenido de la ventana: 1,2 s con dos millones de spans cortos."""
    from laplace_backend.storage.clickhouse import CONTENIDO

    store = _nube()
    plan = store._client.query(
        f"EXPLAIN indexes = 1 SELECT count() FROM spans WHERE {CONTENIDO} LIKE %(c)s",
        parameters={"c": "%pd-48213%"},
    ).result_rows
    texto = "\n".join(r[0] for r in plan)
    assert "idx_contenido" in texto, texto


# ---------------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------------


def test_la_api_pide_al_menos_tres_caracteres(tmp_path, monkeypatch):
    """Con uno o dos caracteres casa casi todo y ningún índice ayuda: es recorrer todo
    el contenido para devolver la lista sin filtrar."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    try:
        with TestClient(main.app) as cliente:
            assert cliente.get("/api/traces", params={"content": "ab"}).status_code == 422
            assert cliente.get("/api/traces", params={"content": "abc"}).status_code == 200
    finally:
        config.get_settings.cache_clear()


def test_solo_se_recorre_el_contenido_de_la_ventana(almacen):
    """El contenido es casi todo el disco: buscar en una semana no puede recorrer los
    meses de detrás. Una traza con un span en la ventana y el texto en otro de hace un
    mes no se encuentra, porque ese texto no se ha leído."""
    store, proyecto = almacen
    traza = uuid.uuid4().hex
    reciente = Span(
        span_id="c" * 16,
        trace_id=traza,
        project_id=proyecto,
        name="reciente",
        type="chain",
        start_time=AHORA,
        end_time=AHORA + timedelta(seconds=1),
        duration_ms=1000.0,
    )
    viejo = Span(
        span_id="d" * 16,
        trace_id=traza,
        project_id=proyecto,
        name="viejo",
        type="llm",
        start_time=AHORA - timedelta(days=30),
        end_time=AHORA - timedelta(days=30) + timedelta(seconds=1),
        duration_ms=1000.0,
    )
    viejo.llm = LLMAttributes(request_model="gpt-5.6-luna", input_messages=_usuario("TK-2718"))
    store.insert_spans([reciente, viejo])
    assert _buscar(store, proyecto, "TK-2718") == set()
