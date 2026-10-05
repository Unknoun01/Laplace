"""La prueba de carga mide lo que habrá de verdad, no una tabla en vacío (D-197).

Dos cosas que la carga hacía mal y que hacían salir medidas falsas:

* **Una lectura que no encuentra nada mide menos de lo que costará.** El generador no
  traía ni salidas cortadas, ni JSON roto, ni conversaciones que crecen, y la truncada y
  la del JSON salían a 0,11 s en lugar de 0,37 s. Ahora cada regla tiene lo suyo en el
  proyecto grande, que es el que se mide.
* **Los spans entran con SQL, sin pasar por la ingesta**, y nadie apuntaba sus horas para
  los preagregados (D-177): el Diagnóstico se leía entero en crudo, como no se lee en un
  servidor de verdad.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.storage import clickhouse
from laplace_backend.storage.base import Window

_RUTA = Path(__file__).resolve().parents[3] / "scripts" / "carga.py"


def _carga():
    spec = importlib.util.spec_from_file_location("carga", _RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def almacen():
    """Un ClickHouse en una base propia: la carga escribe en sus proyectos fijos."""
    general = clickhouse.ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d197_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    store = clickhouse.ClickHouseStore(Settings(clickhouse_database=base, preagregados=True))
    store.migrate()
    try:
        yield store
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def test_la_carga_da_a_cada_regla_nueva_algo_que_encontrar_y_calcula_sus_horas(almacen):
    carga = _carga()
    carga.generar(almacen, 40_000, 40_000)

    ahora = datetime.now(timezone.utc)
    ventana = Window(since=ahora - timedelta(days=1), until=ahora, days=1)
    tipos = {f.kind for f in insights.detect(almacen, carga.GRANDE, ventana)}
    assert {"salida_truncada", "json_roto", "historial"} <= tipos, tipos

    horas = almacen._client.query(
        "SELECT uniqExact(hora) FROM pre_horas WHERE project_id = %(p)s",
        parameters={"p": carga.GRANDE},
    ).result_rows[0][0]
    assert horas >= 24, "las horas del proyecto grande tienen que quedar calculadas"
