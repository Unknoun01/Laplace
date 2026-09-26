"""Los dos almacenes dicen lo mismo, **también cuando hay empates**.

Las pruebas de paridad que ya había comparan cifras: el mismo gasto, los mismos tokens,
el mismo hallazgo. Eso deja fuera una clase entera de divergencias que no son de cifras
sino de **elección**: cuál de las trazas empatadas se enseña como ejemplo, en qué orden
salen dos hallazgos que cuestan lo mismo, qué paso aparece primero en un árbol donde dos
llamadas se lanzaron en paralelo.

`any()` en ClickHouse escoge una fila cualquiera. `argMax(x, n)` escoge una cualquiera de
las que empatan en `n`. Un `ORDER BY` sin desempate deja el orden al azar del plan de
ejecución. Ninguna de las tres rompe una cifra, así que ninguna aparece en las pruebas de
cifras — y cuando diverjan lo harán en producción, en la nube, sobre las reglas de
detección, que es el peor sitio posible.

Por eso **todo lo que se siembra aquí está empatado a propósito**: mismos costes, mismos
instantes, mismos números de repeticiones. Sobre datos así, cualquier elección no
determinista se separa enseguida; sobre datos normales, estas pruebas pasarían por
casualidad y no probarían nada (D-099).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceFilter, Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _nube():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    store.migrate()
    return store


def _llm(
    project: str,
    trace: str,
    span_id: str,
    *,
    nombre: str,
    dedup: str,
    paso: str,
    instante: datetime,
    coste: float = 0.002,
    modelo: str = "gpt-5.6-luna",
    parent: str | None = None,
) -> Span:
    span = Span(
        span_id=span_id,
        trace_id=trace,
        parent_span_id=parent,
        project_id=project,
        name=nombre,
        type="llm",
        status="ok",
        start_time=instante,
        end_time=instante + timedelta(milliseconds=400),
        duration_ms=400.0,
        dedup_hash=dedup,
        step_key=paso,
        step_label=paso,
        step_hint=f"instrucciones de {paso}",
    )
    span.llm = LLMAttributes(
        request_model=modelo,
        usage=TokenUsage(input_tokens=500, output_tokens=20),
        cost=Cost(input_usd=coste, total_usd=coste),
    )
    return span


def _empatados(project: str) -> list[Span]:
    """Tráfico construido para que **todo** empate.

    Dos trazas que repiten el mismo paso el mismo número de veces (empate en `n`), dos
    pasos distintos que cuestan exactamente lo mismo (empate en el orden de hallazgos), y
    dentro de cada traza dos llamadas en el mismo instante (empate en `start_time`). Si
    alguna de las dos implementaciones escoge «una cualquiera», se ve aquí.
    """
    spans: list[Span] = []
    for traza in ("aaa", "zzz"):  # nombres a propósito en los dos extremos del orden
        trace_id = f"{project}-{traza}"
        # Tres repeticiones del mismo paso con la misma entrada, todas a la misma hora.
        for i in range(3):
            repeticion = _llm(
                project,
                trace_id,
                f"{traza}-rep-{i}",
                nombre="chat gpt-5.6-luna",
                dedup="hash-repetido",
                paso="clasificar",
                instante=AHORA,
            )
            # Una de las tres con etiqueta distinta dentro del MISMO grupo: es lo que
            # obliga a elegir un representante. Pasa de verdad cuando la misma llamada
            # se hace desde dos sitios, y sin esto `any()` y `max()` coinciden por
            # casualidad y la prueba no prueba nada.
            if i == 1:
                repeticion.step_label = "clasificar (desde el reintento)"
                repeticion.step_hint = "otras instrucciones para el mismo paso"
            spans.append(repeticion)
        # Y un segundo paso que cuesta exactamente lo mismo que el primero en total.
        for i in range(3):
            spans.append(
                _llm(
                    project,
                    trace_id,
                    f"{traza}-otro-{i}",
                    nombre="chat gpt-5.6-luna",
                    dedup="hash-repetido-2",
                    paso="resumir",
                    instante=AHORA,
                )
            )

    # Una traza con dos modelos, metidos en orden inverso al alfabético. ClickHouse junta
    # los modelos con `groupArray`, que conserva el orden de inserción, y SQLite con un
    # `GROUP_CONCAT(DISTINCT …)`, que ordena por dentro: si no se ordenara en el traductor
    # común, esta traza enseñaría los modelos al revés en cada almacén. Sus dedup_hash son
    # únicos para no crear un hallazgo de repetición que enturbie lo demás.
    for i, modelo in enumerate(("zzz-modelo", "aaa-modelo")):
        spans.append(
            _llm(
                project,
                f"{project}-modelos",
                f"modelos-{i}",
                nombre="chat",
                dedup=f"hash-suelto-{i}",
                paso="responder",
                instante=AHORA,
                modelo=modelo,
            )
        )
    # Y un bucle: seis vueltas del mismo paso en las que lo único que cambia es el
    # número de intento, con las dos trazas dando exactamente las mismas vueltas. Si la
    # consulta de bucles eligiera «una cualquiera» —de ejemplo, de etiqueta, de orden—,
    # aquí se separaría (D-109).
    for traza in ("aaa", "zzz"):
        trace_id = f"{project}-{traza}"
        for i in range(6):
            vuelta = _llm(
                project,
                trace_id,
                f"{traza}-bucle-{i}",
                nombre="chat gpt-5.6-luna",
                dedup=f"hash-vuelta-{i}",
                paso="esperar",
                instante=AHORA,
            )
            # Mismo hash de bucle para las seis —sólo cambia el número— y una única
            # salida distinta: es la definición de dar vueltas sin avanzar.
            vuelta.loop_hash = "bucle-esperar"
            vuelta.loop_out_hash = "siempre-lo-mismo"
            spans.append(vuelta)
    return spans


@pytest.fixture
def dos_almacenes(tmp_path):
    nube = _nube()
    project = f"paridad-{uuid.uuid4().hex[:8]}"
    local = SQLiteStore(tmp_path / "l.db")
    local.migrate()
    spans = _empatados(project)
    local.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        yield {"project": project, "local": local, "nube": nube}
    finally:
        nube.delete_project(project)


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(days=1), until=AHORA + timedelta(days=1), days=2)


# ---------------------------------------------------------------------------------
# Repeticiones: el hallazgo, su ejemplo y su orden
# ---------------------------------------------------------------------------------


def test_las_repeticiones_eligen_el_mismo_ejemplo_con_empates(dos_almacenes):
    """`argMax(x, n)` escoge una cualquiera de las que empatan en `n`. Dos trazas que
    repiten lo mismo tres veces empatan **siempre**, así que esto no es un caso raro:
    es el caso. Divergir aquí manda a dos personas a mirar ejecuciones distintas del
    mismo problema."""
    ventana = _ventana()
    aqui = dos_almacenes["local"].repeated_groups(dos_almacenes["project"], ventana)
    alli = dos_almacenes["nube"].repeated_groups(dos_almacenes["project"], ventana)

    assert [g.name for g in aqui] == [g.name for g in alli], "mismo orden de hallazgos"
    for a, b in zip(aqui, alli, strict=True):
        assert a.sample_trace_id == b.sample_trace_id, a.name
        assert a.dedup_hash == b.dedup_hash, a.name
        assert a.hint == b.hint, a.name
        assert a.step_key == b.step_key, a.name
        assert a.model == b.model, a.name
        assert a.span_type == b.span_type, a.name
        assert a.extra_cost_usd == pytest.approx(b.extra_cost_usd), a.name


def test_los_bucles_salen_iguales_en_los_dos_almacenes(dos_almacenes):
    """La consulta de bucles es nueva y tiene gemelo en ClickHouse: dos consultas
    escritas a mano que tienen que decir lo mismo (D-066, D-109).

    El tráfico está empatado a propósito: las dos trazas dan exactamente las mismas seis
    vueltas, así que cualquier elección arbitraria —el ejemplo, la etiqueta, el orden—
    se separa aquí y pasaría desapercibida sobre datos normales.
    """
    ventana = _ventana()
    aqui = dos_almacenes["local"].loop_groups(dos_almacenes["project"], ventana)
    alli = dos_almacenes["nube"].loop_groups(dos_almacenes["project"], ventana)

    assert aqui, "el tráfico sembrado tiene un bucle; si no sale, la prueba no mide nada"
    assert [g.loop_hash for g in aqui] == [g.loop_hash for g in alli], "mismo orden"
    for a, b in zip(aqui, alli, strict=True):
        assert a.name == b.name, a.loop_hash
        assert a.step_key == b.step_key, a.loop_hash
        assert a.traces == b.traces, a.loop_hash
        assert a.total_spans == b.total_spans, a.loop_hash
        assert a.extra_spans == b.extra_spans, a.loop_hash
        assert a.max_per_trace == b.max_per_trace, a.loop_hash
        assert a.distinct_inputs == b.distinct_inputs, a.loop_hash
        assert a.distinct_outputs == b.distinct_outputs, a.loop_hash
        assert a.extra_cost_usd == pytest.approx(b.extra_cost_usd), a.loop_hash
        assert a.extra_input_tokens == b.extra_input_tokens, a.loop_hash
        assert a.sample_trace_id == b.sample_trace_id, a.loop_hash


def test_un_bucle_justo_en_el_minimo_sale_en_los_dos(dos_almacenes):
    """El sembrado da seis vueltas por traza. Con el mínimo en seis, justo en el borde,
    los dos almacenes lo tienen que ver: la nube acota antes con un filtro barato (D-142),
    y un `>` donde va `>=` se comería los bucles del borde sin que nada más fallara."""
    ventana = _ventana()
    aqui = dos_almacenes["local"].loop_groups(dos_almacenes["project"], ventana, min_vueltas=6)
    alli = dos_almacenes["nube"].loop_groups(dos_almacenes["project"], ventana, min_vueltas=6)
    assert aqui, "con el mínimo en seis, el bucle sembrado tiene que salir"
    assert [g.loop_hash for g in aqui] == [g.loop_hash for g in alli]


def test_la_cobertura_agrupa_por_camino_igual_en_los_dos(dos_almacenes):
    """La agrupación por sitio de llamada también se escribió dos veces (D-106)."""
    ventana = _ventana()
    aqui = dos_almacenes["local"].coverage(dos_almacenes["project"], ventana)
    alli = dos_almacenes["nube"].coverage(dos_almacenes["project"], ventana)

    assert aqui.llm_calls == alli.llm_calls
    assert aqui.steps == alli.steps
    assert aqui.split_steps == alli.split_steps


def test_el_orden_de_dos_hallazgos_que_cuestan_lo_mismo_no_cambia(dos_almacenes):
    """Dos pasos con el mismo dinero desperdiciado salen en un orden, y el usuario lee
    de arriba abajo: el primero es el que va a arreglar."""
    ventana = _ventana()
    aqui = dos_almacenes["local"].repeated_groups(dos_almacenes["project"], ventana)
    alli = dos_almacenes["nube"].repeated_groups(dos_almacenes["project"], ventana)

    costes = {round(g.extra_cost_usd, 10) for g in aqui}
    assert len(costes) == 1, "el tráfico sembrado tiene que estar empatado de verdad"
    assert [g.step_key for g in aqui] == [g.step_key for g in alli]


def test_la_repeticion_de_ejemplo_es_la_misma_traza(dos_almacenes):
    """`sample_repetition` coge «la traza que más repite», y todas repiten igual."""
    ventana = _ventana()
    aqui = dos_almacenes["local"].sample_repetition(
        dos_almacenes["project"], ventana, "hash-repetido"
    )
    alli = dos_almacenes["nube"].sample_repetition(
        dos_almacenes["project"], ventana, "hash-repetido"
    )
    assert [s.trace_id for s in aqui] == [s.trace_id for s in alli]
    # Y los spans salen en el mismo orden, aunque compartan instante.
    assert [s.span_id for s in aqui] == [s.span_id for s in alli]


# ---------------------------------------------------------------------------------
# Uso por paso
# ---------------------------------------------------------------------------------


def test_el_uso_por_paso_elige_la_misma_traza_de_ejemplo(dos_almacenes):
    ventana = _ventana()
    aqui = {
        (u.key, u.model): u
        for u in dos_almacenes["local"].model_usage(dos_almacenes["project"], ventana, min_calls=1)
    }
    alli = {
        (u.key, u.model): u
        for u in dos_almacenes["nube"].model_usage(dos_almacenes["project"], ventana, min_calls=1)
    }
    assert set(aqui) == set(alli)
    for clave, uso in aqui.items():
        otro = alli[clave]
        assert uso.sample_trace_id == otro.sample_trace_id, clave
        assert uso.name == otro.name, clave
        assert uso.hint == otro.hint, clave


# ---------------------------------------------------------------------------------
# La traza: su nombre, sus modelos y el orden de sus pasos
# ---------------------------------------------------------------------------------


def test_el_arbol_de_una_traza_sale_en_el_mismo_orden(dos_almacenes):
    """Seis llamadas en el mismo instante. Sin desempate, el árbol se pinta en un orden
    distinto en cada almacén y el usuario que compara dos pantallas ve dos agentes."""
    trace_id = f"{dos_almacenes['project']}-aaa"
    aqui = dos_almacenes["local"].get_trace_spans(trace_id, dos_almacenes["project"])
    alli = dos_almacenes["nube"].get_trace_spans(trace_id, dos_almacenes["project"])
    assert [s.span_id for s in aqui] == [s.span_id for s in alli]


def test_el_resumen_de_una_traza_dice_lo_mismo(dos_almacenes):
    """El nombre de la traza sale del span raíz; sin raíz, del primero por instante. Con
    seis spans a la misma hora y sin raíz, «el primero» es una elección."""
    filtros = TraceFilter(project_id=dos_almacenes["project"], limit=50)
    aqui = dos_almacenes["local"].list_traces(filtros).traces
    alli = dos_almacenes["nube"].list_traces(filtros).traces

    assert [t.trace_id for t in aqui] == [t.trace_id for t in alli]
    for a, b in zip(aqui, alli, strict=True):
        assert a.root_name == b.root_name, a.trace_id
        assert a.project_id == b.project_id, a.trace_id
        # La lista de modelos se ordena en el traductor común: `groupArray` y
        # `GROUP_CONCAT` no prometen ningún orden, ninguno de los dos.
        assert a.models == b.models, a.trace_id


def test_los_modelos_de_una_traza_van_ordenados(dos_almacenes):
    """ClickHouse los devuelve en orden de inserción y SQLite ordenados.

    Comprobado contra el ClickHouse de verdad: `arrayDistinct(groupArray(...))` sobre
    dos spans insertados como `zzz` y luego `aaa` devuelve `['zzz', 'aaa']`. Lo único
    que hace que las dos pantallas digan lo mismo es el `sorted` del traductor común, y
    por eso esta prueba mira los dos almacenes: con SQLite solo pasaría igual aunque
    nadie ordenase nada, porque su `DISTINCT` ordena por dentro.
    """
    filtros = TraceFilter(project_id=dos_almacenes["project"], limit=50)
    aqui = {t.trace_id: t for t in dos_almacenes["local"].list_traces(filtros).traces}
    alli = {t.trace_id: t for t in dos_almacenes["nube"].list_traces(filtros).traces}

    trace_id = f"{dos_almacenes['project']}-modelos"
    assert aqui[trace_id].models == alli[trace_id].models
    assert aqui[trace_id].models == ["aaa-modelo", "zzz-modelo"]


# ---------------------------------------------------------------------------------
# La red: que no vuelva a colarse un `any()`
# ---------------------------------------------------------------------------------


def test_no_queda_ningun_any_en_el_sql_de_la_nube():
    """`any()` en ClickHouse significa «una fila cualquiera del grupo», y no hay ningún
    sitio de este producto donde eso sea lo que queremos decir. Ya apareció cuatro veces
    —dos en repeticiones, una en uso por paso, una en el resumen de traza—, así que en
    vez de confiar en encontrarlo la quinta, se prohíbe.

    Si algún día hiciera falta de verdad, el arreglo es escribir por qué en una línea y
    añadirlo a la excepción. Lo que no puede es entrar sin que nadie lo mire.
    """
    import re
    from pathlib import Path

    fuente = Path(__file__).resolve().parents[1] / "laplace_backend" / "storage"
    sospechosas: list[str] = []
    for fichero in fuente.glob("*.py"):
        for numero, linea in enumerate(fichero.read_text(encoding="utf-8").splitlines(), 1):
            codigo = linea.split("--")[0].split("#")[0]
            # `any(` de Python sobre un generador es legítimo; el de SQL, sobre una
            # columna suelta, no lo es en ningún sitio de este producto.
            if re.search(r"any\s*\([a-z_]+\s*\)", codigo) and "for " not in codigo:
                sospechosas.append(f"{fichero.name}:{numero}: {linea.strip()}")
    assert not sospechosas, "SQL con `any()`: " + "; ".join(sospechosas)


def test_los_argmax_llevan_su_desempate():
    """`argMax(x, n)` y `argMin(x, t)` escogen una cualquiera cuando hay empate en la
    clave, y aquí los empates son el caso normal. La clave tiene que ser una tupla."""
    import re
    from pathlib import Path

    fuente = Path(__file__).resolve().parents[1] / "laplace_backend" / "storage" / "clickhouse.py"
    sueltos = [
        linea.strip()
        for linea in fuente.read_text(encoding="utf-8").splitlines()
        # Los comentarios de Python (`#:`) y los de SQL (`--`) hablan de esto mismo y no
        # son código: si no se descartaran, esta prueba se quejaría de su propia
        # explicación, que es una forma tonta de que alguien la borre.
        if not linea.lstrip().startswith("#")
        and re.search(r"arg(Max|Min)\([^,]+,\s*[^(\s]", linea.split("--")[0])
    ]
    assert not sueltos, "argMax/argMin sin desempate: " + "; ".join(sueltos)
