"""La migración a la clave por hora (D-168, D-177), contra ClickHouse de verdad.

En una base propia de cada ejecución, no en la de desarrollo: se crea la tabla como la
dejaba la versión anterior, con datos dentro, y se migra por encima. Una migración que
sólo se prueba sobre una base vacía no prueba nada (lo mismo que en
`test_migracion_nube.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceFilter, Window
from laplace_backend.storage.migrar_orden import (
    ANTES,
    CLAVE_DIA,
    CLAVE_NUEVA,
    CLAVE_VIEJA,
    NUEVA,
    clave,
    migrar,
    necesita,
    plan,
)

AHORA = datetime.now(timezone.utc) - timedelta(hours=1)
PROYECTO = "migrar"


def _span(trace: str, i: int, dias: int, coste: float = 0.001) -> Span:
    inicio = AHORA - timedelta(days=dias, seconds=i)
    span = Span(
        span_id=f"{trace[:10]}{i:06d}",
        trace_id=trace,
        project_id=PROYECTO,
        name="chat",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=300),
        duration_ms=300.0,
    )
    span.llm = LLMAttributes(
        system="openai",
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        cost=Cost(total_usd=coste),
    )
    return span


@pytest.fixture
def nube():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    base = f"prueba_d168_{uuid.uuid4().hex[:8]}"
    general = ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando; la migración no se prueba")
    general._client.command(f"CREATE DATABASE {base}")
    store = ClickHouseStore(Settings(clickhouse_database=base))
    try:
        yield store
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _sembrar_con_clave_vieja(store, vieja: str = CLAVE_VIEJA) -> list[Span]:
    """La tabla con la clave de antes, con datos que llegaron **ayer**: si llevaran el
    `ingested_at` de ahora, la puesta al día del final los recogería todos y taparía
    cualquier fallo de la copia."""
    store.migrate()
    client = store._client
    spans = [
        _span(uuid.uuid4().hex, i, dias)
        for dias in (0, 3, 20, 45)  # dos particiones mensuales, varios días
        for i in range(25)
    ]
    store.insert_spans(spans)
    client.command(
        "CREATE TABLE spans_vieja AS spans ENGINE = ReplacingMergeTree(ingested_at) "
        f"PARTITION BY toYYYYMM(start_time) ORDER BY ({vieja})"
    )
    client.command(
        "INSERT INTO spans_vieja SELECT * REPLACE (now64(3) - INTERVAL 1 DAY AS ingested_at) "
        "FROM spans"
    )
    client.command("EXCHANGE TABLES spans AND spans_vieja")
    client.command("DROP TABLE spans_vieja SYNC")
    assert clave(client) == vieja
    return spans


def test_una_instalacion_nueva_nace_con_la_clave_por_hora(nube):
    nube.migrate()
    assert clave(nube._client) == CLAVE_NUEVA
    assert not necesita(nube._client)
    assert "No hace falta" in plan(nube._client)


@pytest.mark.parametrize("vieja", [CLAVE_VIEJA, CLAVE_DIA], ids=["original", "por día"])
def test_la_migracion_conserva_todo_y_cambia_la_clave(nube, vieja):
    spans = _sembrar_con_clave_vieja(nube, vieja)
    client = nube._client
    ventana = Window(since=AHORA - timedelta(days=60), until=AHORA + timedelta(hours=1), days=60)
    antes = nube.summarize_window(PROYECTO, ventana)
    assert "Se copiaría" in plan(client)

    assert migrar(client, retention_days=90, avisar=lambda _: None) is True

    assert clave(client) == CLAVE_NUEVA
    assert clave(client, ANTES) == vieja, "la vieja se queda, sin borrar"
    assert int(client.query("SELECT count() FROM spans FINAL").result_rows[0][0]) == len(spans)
    despues = nube.summarize_window(PROYECTO, ventana)
    # Sumar en otro orden cambia el último decimal de un float: el dinero, con tolerancia,
    # y el resto, exacto.
    assert despues.total_cost_usd == pytest.approx(antes.total_cost_usd)
    antes.total_cost_usd = despues.total_cost_usd
    assert despues == antes
    # La retención vuelve a estar puesta: el TTL no pasa con CREATE TABLE ... AS.
    motor = client.query(
        "SELECT engine_full FROM system.tables WHERE database = currentDatabase() "
        "AND name = 'spans'"
    ).result_rows[0][0]
    assert "TTL" in motor and "90" in motor
    # Y una segunda vez no hace nada.
    assert migrar(client, avisar=lambda _: None) is False


class _Espia:
    """El cliente de ClickHouse, con un gancho antes o después de ciertas órdenes."""

    def __init__(self, client, antes=None, despues=None) -> None:
        self._client, self._antes, self._despues = client, antes, despues

    def command(self, sql, *args, **kwargs):
        if self._antes:
            self._antes(sql, kwargs.get("parameters") or {})
        resultado = self._client.command(sql, *args, **kwargs)
        if self._despues:
            self._despues(sql, kwargs.get("parameters") or {})
        return resultado

    def __getattr__(self, nombre):
        return getattr(self._client, nombre)


def test_lo_que_llega_mientras_se_copia_no_se_pierde(nube, monkeypatch):
    """La ingesta no se para. Un span de hoy que llega justo después de copiarse el día
    de hoy sólo lo recoge la puesta al día del final.

    Sin margen y con un segundo de por medio: en una copia de horas, la segunda puesta
    al día (la de después del cambio de nombre) no llega hasta él, y es lo que se simula.
    """
    import time

    from laplace_backend.storage import migrar_orden

    monkeypatch.setattr(migrar_orden, "MARGEN", timedelta(0))
    _sembrar_con_clave_vieja(nube)
    tardio = _span(uuid.uuid4().hex, 0, 0, coste=0.5)

    def despues(sql, parametros):
        if (
            sql.startswith(f"INSERT INTO {NUEVA}")
            and parametros.get("d") == tardio.start_time.date()
        ):
            nube.insert_spans([tardio])
            time.sleep(1.1)

    migrar(_Espia(nube._client, despues=despues), avisar=lambda _: None)
    assert [s.span_id for s in nube.get_trace_spans(tardio.trace_id, PROYECTO)] == [tardio.span_id]


def test_lo_que_llega_justo_antes_del_cambio_de_nombre_no_se_pierde(nube):
    """Entre la puesta al día y el `EXCHANGE` también se escribe: eso lo recoge la
    segunda puesta al día, ya desde la tabla vieja."""
    _sembrar_con_clave_vieja(nube)
    ultimo = _span(uuid.uuid4().hex, 0, 0, coste=0.7)

    def antes(sql, parametros):
        if sql.startswith("EXCHANGE TABLES"):
            nube.insert_spans([ultimo])

    migrar(_Espia(nube._client, antes=antes), avisar=lambda _: None)
    assert [s.span_id for s in nube.get_trace_spans(ultimo.trace_id, PROYECTO)] == [ultimo.span_id]


def test_una_copia_a_medias_se_rehace(nube):
    """Si se corta, se vuelve a lanzar: la partición a medias se tira y se copia entera."""
    spans = _sembrar_con_clave_vieja(nube)
    client = nube._client
    client.command(
        f"CREATE TABLE {NUEVA} AS spans ENGINE = ReplacingMergeTree(ingested_at) "
        f"PARTITION BY toYYYYMM(start_time) ORDER BY ({CLAVE_NUEVA})"
    )
    client.command(f"INSERT INTO {NUEVA} SELECT * FROM spans LIMIT 7")
    migrar(client, avisar=lambda _: None)
    assert int(client.query("SELECT count() FROM spans FINAL").result_rows[0][0]) == len(spans)
    # Sin FINAL: la copia a medias se tiró, no quedan siete filas de más en disco.
    assert int(client.query("SELECT count() FROM spans").result_rows[0][0]) == len(spans)


def test_un_span_reenviado_se_sigue_deduplicando(nube):
    """El día entra en la clave: un span reenviado conserva su `start_time`, así que
    sigue cayendo en la misma clave y `ReplacingMergeTree` se queda con uno."""
    nube.migrate()
    span = _span(uuid.uuid4().hex, 0, 0)
    nube.insert_spans([span])
    nube.insert_spans([span])
    nube._client.command("OPTIMIZE TABLE spans FINAL")
    assert int(nube._client.query("SELECT count() FROM spans").result_rows[0][0]) == 1
    pagina = nube.list_traces(TraceFilter(project_id=PROYECTO, since=AHORA - timedelta(days=1)))
    assert len(pagina.traces) == 1
