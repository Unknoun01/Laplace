"""Latencia y errores en el Panel (Fase 4, D-145).

El Panel decía la duración **media** de una ejecución. Con agentes, la media no es la
espera de nadie: tres ejecuciones colgadas de dos minutos entre cien de dos segundos la
llevan a cinco segundos, y lo que siente casi todo el mundo son dos. Lo que se mira es
la mediana —lo normal— y el p95 —lo que sufre uno de cada veinte—, y al lado cuántas
ejecuciones fallan.

Los percentiles son de **rango más cercano**: el valor de una ejecución que existió, no
una interpolación entre dos. Así los dos almacenes dan exactamente lo mismo y la cifra
se puede buscar en la lista de trazas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Span

from laplace_backend import panel
from laplace_backend.config import Settings
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)
VENTANA = Window(since=AHORA - timedelta(days=3), until=AHORA, days=3)


@pytest.fixture(params=["sqlite", "clickhouse"])
def almacen(request, tmp_path):
    proyecto = f"latencia-{uuid.uuid4().hex[:8]}"
    if request.param == "sqlite":
        store = SQLiteStore(tmp_path / "laplace.db")
        store.migrate()
        yield store, proyecto
        return
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    yield store, proyecto
    store.delete_project(proyecto)


def _ejecucion(
    proyecto: str, cuando: datetime, ms: int, *, error: bool = False, hijos: int = 0
) -> list[Span]:
    """Una ejecución que dura `ms` de principio a fin, con `hijos` spans solapados."""
    traza = uuid.uuid4().hex
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=traza,
        project_id=proyecto,
        name="agente",
        type="agent",
        status="error" if error else "ok",
        start_time=cuando,
        end_time=cuando + timedelta(milliseconds=ms),
        duration_ms=float(ms),
    )
    spans = [raiz]
    for _ in range(hijos):
        spans.append(
            Span(
                span_id=uuid.uuid4().hex[:16],
                trace_id=traza,
                parent_span_id=raiz.span_id,
                project_id=proyecto,
                name="paso",
                type="chain",
                start_time=cuando,
                end_time=cuando + timedelta(milliseconds=ms),
                duration_ms=float(ms),
            )
        )
    return spans


def _metrica(vista: panel.Panel, etiqueta: str) -> panel.Metric:
    return next(m for m in vista.per_execution if m.label == etiqueta)


def test_mediana_y_p95_por_rango_mas_cercano(almacen):
    """Veinte ejecuciones de 100 ms a 2 s: la mediana es la décima (1 s) y el p95 la
    decimonovena (1,9 s). Con interpolación saldrían 1,05 s y 1,905 s, cifras que no
    son de ninguna ejecución."""
    store, proyecto = almacen
    spans = []
    for i in range(1, 21):
        spans += _ejecucion(proyecto, AHORA - timedelta(hours=1, minutes=i), 100 * i)
    store.insert_spans(spans)
    latencia = store.trace_latency(proyecto, VENTANA)
    assert (latencia.traces, latencia.p50_ms, latencia.p95_ms) == (20, 1000, 1900)


def test_cuando_el_rango_no_es_exacto_se_redondea_hacia_arriba(almacen):
    """Con cinco ejecuciones, la mediana es la tercera y el p95 la quinta: redondear
    hacia abajo daría la segunda y la cuarta, que dejan fuera justo la cola."""
    store, proyecto = almacen
    spans = []
    for i in range(1, 6):
        spans += _ejecucion(proyecto, AHORA - timedelta(hours=1, minutes=i), 100 * i)
    store.insert_spans(spans)
    latencia = store.trace_latency(proyecto, VENTANA)
    assert (latencia.p50_ms, latencia.p95_ms) == (300, 500)


def test_es_la_duracion_de_la_ejecucion_no_la_suma_de_sus_spans(almacen):
    store, proyecto = almacen
    store.insert_spans(_ejecucion(proyecto, AHORA - timedelta(hours=1), 800, hijos=3))
    latencia = store.trace_latency(proyecto, VENTANA)
    assert (latencia.p50_ms, latencia.p95_ms) == (800, 800)


def test_sin_ejecuciones_no_hay_cifra(almacen):
    store, proyecto = almacen
    latencia = store.trace_latency(proyecto, VENTANA)
    assert (latencia.traces, latencia.p50_ms, latencia.p95_ms) == (0, None, None)


def test_el_panel_dice_mediana_p95_y_errores(almacen):
    """Una ejecución colgada no mueve la mediana, y es exactamente lo que el p95 está
    para enseñar."""
    store, proyecto = almacen
    spans = []
    for i in range(19):
        spans += _ejecucion(
            proyecto, AHORA - timedelta(hours=2, minutes=i), 2000, error=i < 4
        )
    spans += _ejecucion(proyecto, AHORA - timedelta(hours=3), 120_000)
    store.insert_spans(spans)

    vista = panel.build(store, proyecto, VENTANA)
    etiquetas = [m.label for m in vista.per_execution]
    assert "Duración por ejecución" not in etiquetas, "la media no es la espera de nadie"
    assert _metrica(vista, "Duración, mediana").value == 2000
    assert _metrica(vista, "Duración, p95").value == 2000
    errores = _metrica(vista, "Ejecuciones con error")
    assert errores.unit == "ratio"
    assert errores.value == pytest.approx(4 / 20)


def test_el_p95_ve_la_cola(almacen):
    store, proyecto = almacen
    spans = []
    for i in range(18):
        spans += _ejecucion(proyecto, AHORA - timedelta(hours=2, minutes=i), 2000)
    for i in range(2):
        spans += _ejecucion(proyecto, AHORA - timedelta(hours=3, minutes=i), 90_000)
    store.insert_spans(spans)
    vista = panel.build(store, proyecto, VENTANA)
    assert _metrica(vista, "Duración, mediana").value == 2000
    assert _metrica(vista, "Duración, p95").value == 90_000


def test_se_compara_con_el_periodo_anterior(almacen):
    store, proyecto = almacen
    spans = []
    for i in range(10):
        # Periodo anterior: 1 s y ningún error, repartido por todo él (si no, no se
        # compara, D-107). Éste: 3 s y la mitad con error.
        antes = AHORA - timedelta(days=3, hours=8 + 5 * i)
        spans += _ejecucion(proyecto, antes, 1000)
        spans += _ejecucion(
            proyecto, AHORA - timedelta(hours=1, minutes=i), 3000, error=i % 2 == 0
        )
    store.insert_spans(spans)
    vista = panel.build(store, proyecto, VENTANA)
    mediana = _metrica(vista, "Duración, mediana")
    assert (mediana.value, mediana.previous) == (3000, 1000)
    assert mediana.change_ratio == pytest.approx(2.0)
    errores = _metrica(vista, "Ejecuciones con error")
    assert (errores.value, errores.previous) == (pytest.approx(0.5), 0.0)
    # De cero a algo no es «infinito por ciento»: no hay variación que dar.
    assert errores.change_ratio is None


def test_sin_ejecuciones_la_latencia_y_los_errores_dicen_por_que(almacen):
    store, proyecto = almacen
    vista = panel.build(store, proyecto, VENTANA)
    for etiqueta in ("Duración, mediana", "Duración, p95", "Ejecuciones con error"):
        m = _metrica(vista, etiqueta)
        assert m.value is None and m.unavailable, etiqueta
