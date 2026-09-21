"""Que una base **con datos dentro** sobreviva a la actualización.

`CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe. Por eso las columnas que
llegan después viven en un `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` al final del
esquema, y por eso hace falta comprobarlo sobre una tabla **que ya tiene filas**: sobre
una base vacía el `CREATE` la crea completa y el `ALTER` no se ejercita nunca. Es decir,
la prueba fácil pasa siempre y no prueba lo que hay que probar.

Lo que revienta en un despliegue no es el esquema nuevo, es el viejo con datos de un
cliente dentro. Esta tanda añadió tres columnas —`step_site`, `loop_hash`,
`loop_out_hash`— y una consulta que las usa, así que aquí se simula la instalación de
antes: se quitan las tres, se meten filas como las metía la versión anterior, y se pasa
la migración por encima.

Necesita ClickHouse escuchando. Se salta solo si no lo hay, y esa salida es justo el
agujero que este fichero existe para tapar: es la tercera vez que el camino de la nube
se queda sin ejecutar después de tocar su SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend.config import Settings
from laplace_backend.storage.base import Window

AHORA = datetime.now(timezone.utc) - timedelta(minutes=10)

#: Las columnas que esta tanda añadió. Quitarlas deja la tabla como la dejaba la
#: versión anterior, que es la situación de cualquiera que actualice.
COLUMNAS_NUEVAS = ("step_site", "loop_hash", "loop_out_hash")


def _nube():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; la migración de la nube no se prueba")
    store.migrate()
    return store


def _span(project: str, trace: str, i: int) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name="chat gpt-5.6-luna",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=300),
        duration_ms=300.0,
        dedup_hash=f"hash-{i}",
        step_key="k-responder",
        step_label="responder",
        step_site="atender > responder",
        loop_hash="bucle-responder",
        loop_out_hash="salida-unica",
    )
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=400, output_tokens=20),
        cost=Cost(input_usd=0.001, total_usd=0.001),
    )
    return span


def test_una_base_con_datos_sobrevive_a_las_columnas_nuevas():
    """El caso de un despliegue real: tabla vieja, con filas, y encima la versión nueva.

    Se comprueban las tres cosas que pueden romperse, en este orden: que el `ALTER`
    pase sobre una tabla con datos, que las filas de antes se sigan leyendo —con las
    columnas nuevas vacías, no con la consulta reventando—, y que las consultas que
    usan esas columnas funcionen mezclando filas viejas y nuevas.
    """
    store = _nube()
    project = f"migracion-{uuid.uuid4().hex[:8]}"
    cliente = store._client  # noqa: SLF001 - la prueba necesita el DDL en crudo

    # 1. La instalación de antes: sin las columnas nuevas, y con datos dentro.
    for columna in COLUMNAS_NUEVAS:
        cliente.command(f"ALTER TABLE spans DROP COLUMN IF EXISTS {columna}")
    columnas = {
        fila[0] for fila in cliente.query("DESCRIBE TABLE spans").result_rows
    }
    assert not (set(COLUMNAS_NUEVAS) & columnas), "la tabla tiene que quedar como la vieja"

    viejas = ", ".join(
        [
            "span_id", "trace_id", "project_id", "name", "span_type", "status",
            "start_time", "end_time", "duration_ms", "request_model", "input_tokens",
            "output_tokens", "cost_total_usd", "dedup_hash", "step_key", "step_label",
        ]
    )
    cliente.command(
        f"INSERT INTO spans ({viejas}) VALUES "
        f"('vieja0001', '{project}-vieja', '{project}', 'chat gpt-5.6-luna', 'llm', 'ok', "
        f"toDateTime64('{AHORA:%Y-%m-%d %H:%M:%S}', 3), "
        f"toDateTime64('{AHORA:%Y-%m-%d %H:%M:%S}', 3), "
        f"300, 'gpt-5.6-luna', 400, 20, 0.001, 'hash-viejo', 'k-responder', 'responder')"
    )

    try:
        # 2. La actualización.
        store.migrate()
        columnas = {fila[0] for fila in cliente.query("DESCRIBE TABLE spans").result_rows}
        for columna in COLUMNAS_NUEVAS:
            assert columna in columnas, f"la migración no añadió {columna}"

        # 3. La fila vieja se sigue leyendo, con las columnas nuevas vacías.
        ventana = Window(
            since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=1), days=1
        )
        resumen = store.summarize_window(project, ventana)
        assert resumen.llm_calls == 1, "la fila de antes tiene que seguir contándose"

        # 4. Y las consultas nuevas funcionan mezclando filas viejas y nuevas.
        store.insert_spans([_span(project, f"{project}-nueva", i) for i in range(6)])
        cobertura = store.coverage(project, ventana)
        assert cobertura.llm_calls == 7
        bucles = store.loop_groups(project, ventana, min_vueltas=4)
        assert [b.loop_hash for b in bucles] == ["bucle-responder"], (
            "la consulta de bucles tiene que ignorar la fila vieja, que no tiene hash, "
            "y encontrar el bucle de las nuevas"
        )
        assert bucles[0].total_spans == 6, "sólo las filas nuevas entran en el bucle"
    finally:
        store.delete_project(project)


def test_la_migracion_es_idempotente():
    """Pasarla dos veces no puede hacer nada: `laplace ui` la ejecuta en cada arranque."""
    store = _nube()
    store.migrate()
    store.migrate()
    columnas = {
        fila[0] for fila in store._client.query("DESCRIBE TABLE spans").result_rows  # noqa: SLF001
    }
    for columna in COLUMNAS_NUEVAS:
        assert columna in columnas
