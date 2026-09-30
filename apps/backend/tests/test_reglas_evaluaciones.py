"""Las reglas dejan fuera las tiradas de evaluación igual por los tres caminos (D-177).

En ClickHouse, las trazas de evaluación se buscan antes y se pasan como lista, y sin
ninguna el filtro es el de la ventana a secas; con muchas se vuelve a la subconsulta de
`RULES_WHERE`. Los tres caminos tienen que contar lo mismo que un proyecto en el que esas
trazas no existieran.
"""

from __future__ import annotations

import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from laplace.semconv import EVAL_TAG

from laplace_backend.config import Settings
from laplace_backend.storage import clickhouse
from laplace_backend.storage.base import Window

sys.path.insert(0, str(Path(__file__).parent))
from test_sqlite_store import BASE, _agente  # noqa: E402


@pytest.fixture
def nube():
    general = clickhouse.ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d177_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    store = clickhouse.ClickHouseStore(Settings(clickhouse_database=base))
    store.migrate()
    try:
        yield store
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _lecturas(store, proyecto: str, ventana: Window) -> dict:
    return {
        "repeticiones": [
            (g.step_key, g.traces, g.extra_spans) for g in store.repeated_groups(proyecto, ventana)
        ],
        "uso": [(u.key, u.model, u.calls, u.traces) for u in store.model_usage(proyecto, ventana)],
        "prompts": [
            (o.step_key, o.calls) for o in store.observed_prompts(proyecto, ventana, rules=True)
        ],
    }


def test_los_tres_caminos_dejan_fuera_lo_mismo(nube, monkeypatch):
    ventana = Window(since=BASE - timedelta(hours=1), until=BASE + timedelta(hours=1), days=1)
    limpio, mezclado = f"limpio-{uuid.uuid4().hex[:6]}", f"mezcla-{uuid.uuid4().hex[:6]}"
    nube.insert_spans(_agente(limpio))
    # El mismo agente, más dos ejecuciones de evaluación con la etiqueta en la raíz.
    tiradas = _agente(mezclado)
    trazas = sorted({s.trace_id for s in tiradas})
    de_evaluacion = set(trazas[:2])
    for s in tiradas:
        if s.trace_id in de_evaluacion and s.parent_span_id is None:
            s.tags = [EVAL_TAG]
    nube.insert_spans(tiradas)
    sin_ellas = [s for s in tiradas if s.trace_id not in de_evaluacion]
    esperado_proyecto = f"esperado-{uuid.uuid4().hex[:6]}"
    nube.insert_spans([s.model_copy(update={"project_id": esperado_proyecto}) for s in sin_ellas])

    esperado = _lecturas(nube, esperado_proyecto, ventana)
    assert esperado["repeticiones"], "el agente de prueba tiene que repetir algo"
    assert _lecturas(nube, mezclado, ventana) == esperado, "con la lista"
    monkeypatch.setattr(clickhouse, "_MAX_EVALUACIONES", 1)
    assert _lecturas(nube, mezclado, ventana) == esperado, "con la subconsulta"
    # Sin ninguna evaluación, el filtro de la ventana a secas: cuentan las seis.
    assert {g[1] for g in _lecturas(nube, limpio, ventana)["repeticiones"]} == {6}
    assert {g[1] for g in esperado["repeticiones"]} == {4}
