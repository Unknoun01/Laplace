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


@pytest.fixture(scope="module")
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


@pytest.fixture(scope="module")
def cargado(almacen):
    carga = _carga()
    carga.generar(almacen, 40_000, 40_000)
    return almacen, carga


def _ventana() -> Window:
    ahora = datetime.now(timezone.utc)
    return Window(since=ahora - timedelta(days=1), until=ahora, days=1)


def test_la_carga_da_a_cada_regla_nueva_algo_que_encontrar_y_calcula_sus_horas(cargado):
    almacen, carga = cargado
    ventana = _ventana()
    tipos = {f.kind for f in insights.detect(almacen, carga.GRANDE, ventana)}
    assert {"salida_truncada", "json_roto", "historial", "cache_parecida"} <= tipos, tipos

    horas = almacen._client.query(
        "SELECT uniqExact(hora) FROM pre_horas WHERE project_id = %(p)s",
        parameters={"p": carga.GRANDE},
    ).result_rows[0][0]
    assert horas >= 24, "las horas del proyecto grande tienen que quedar calculadas"


def test_el_historial_solo_abre_las_conversaciones_que_pueden_contar(cargado):
    """Con siete días de carga el filtro previo del historial dejaba pasar 112.641 de
    112.644 conversaciones, y las ventanas de detrás pedían 1 GiB (D-198). En la carga
    las entradas varían al azar y sólo crecen las conversaciones elegidas: el filtro
    tiene que dejar fuera casi todas las demás."""
    from laplace_backend.insights import historial
    from laplace_backend.insights.modelos import MIN_REPEATS, MIN_VUELTAS_BUCLE
    from laplace_backend.storage.clickhouse import HISTORY_CANDIDATES_SQL

    almacen, carga = cargado
    params = almacen._window_params(carga.GRANDE, _ventana())
    params.update(
        min_turnos=historial.MIN_TURNOS_HISTORIAL,
        min_crece=historial.MIN_CRECIMIENTO_POR_TURNO,
        min_repeats=MIN_REPEATS,
        min_vueltas=MIN_VUELTAS_BUCLE,
    )
    (candidatas,) = almacen._consulta(
        f"SELECT count() FROM ({HISTORY_CANDIDATES_SQL})", parameters=params
    ).result_rows[0]
    (todas,) = almacen._client.query(
        "SELECT uniqExact(session_id, step_key) FROM spans "
        "WHERE project_id = %(p)s AND span_type = 'llm'",
        parameters={"p": carga.GRANDE},
    ).result_rows[0]
    contadas = sum(
        g.conversations
        for g in almacen.history_groups(carga.GRANDE, _ventana())
    )
    assert contadas > 0, "la carga tiene conversaciones que crecen"
    assert contadas <= candidatas, "el filtro no puede dejar fuera ninguna que cuente"
    assert candidatas < todas * 0.2, (candidatas, todas)


def test_el_historial_solo_compone_las_conversaciones_que_pueden_contar(cargado, monkeypatch):
    """Componer `pre_historial` guardaba los estados de todas sus conversaciones para
    descartar casi todas al final: con diez millones de spans al día, 3,8 GiB (D-212). El
    filtro previo (D-222) tiene que dejar fuera casi todas las filas de la tabla, y el
    historial salir igual."""
    from laplace_backend.storage import preagregados

    almacen, carga = cargado
    ventana = _ventana()
    (filas,) = almacen._client.query(
        "SELECT count() FROM pre_historial FINAL WHERE project_id = %(p)s",
        parameters={"p": carga.GRANDE},
    ).result_rows[0]
    monkeypatch.setattr(
        preagregados, "_CONVERSACIONES", "SELECT count() FROM ({partes})"
    )
    sql, params, externas = preagregados.historial(
        almacen._client, carga.GRANDE, ventana.since, ventana.until,
        dict(preagregados.UMBRALES_HISTORIAL),
    )
    (compuestas,) = almacen._client.query(
        sql, parameters=params, external_data=externas
    ).result_rows[0]
    assert 0 < compuestas < filas * 0.2, (compuestas, filas)

