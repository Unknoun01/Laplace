"""Prueba de carga de la nube: millones de spans en ClickHouse y lo que tardan las
pantallas sobre ellos (Fase 4).

    python scripts/carga.py --spans 10000000        # genera y mide
    python scripts/carga.py --solo-medir            # mide lo que ya hay
    python scripts/carga.py --borrar                # quita los datos de carga

Los spans se generan **dentro** de ClickHouse con `INSERT … SELECT FROM numbers()`: desde
Python, diez millones de filas son media hora y aquí son segundos. Tienen la forma de un
agente de verdad —trazas de ocho spans: un agente, tres llamadas a modelo, dos
herramientas y dos pasos—, repartidas en un día, con un 3 % de errores, repeticiones en
una de cada diez trazas y payloads de un par de KB. La mitad del tráfico es de un solo
proyecto grande, que es el caso que hay que aguantar: un cliente con mucho volumen.

El objetivo, escrito para que haya algo contra lo que medir: **10 millones de spans al
día en la instalación, y el Diagnóstico de un proyecto por debajo de 1,5 s**.

Cuidado: con 10 millones hacen falta unos 8 GB para Docker. La primera medida, antes de
D-142, pidió más de 2 GB en una sola consulta y tumbó la máquina virtual.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "apps" / "backend"))

from laplace_backend import insights, panel
from laplace_backend.config import Settings
from laplace_backend.storage.base import TraceFilter, Window
from laplace_backend.storage.clickhouse import ClickHouseStore

PREFIJO = "carga-"
GRANDE = "carga-grande"
OBJETIVO_S = 1.5
SPANS_POR_TRAZA = 8

_INSTRUCCIONES = "Eres el asistente de Vuelos Laplace. Responde con el manual. " * 30

GENERAR = f"""
INSERT INTO spans (
    project_id, trace_id, span_id, parent_span_id, name, span_type, status,
    start_time, end_time, duration_ms, gen_ai_system, operation, request_model,
    response_model, input_tokens, output_tokens, cached_input_tokens,
    cost_input_usd, cost_output_usd, cost_total_usd, price_rate,
    input_messages, output_messages, tool_name, tool_arguments, tool_output,
    session_id, user_id, dedup_hash, loop_hash, loop_out_hash,
    step_key, step_site, step_label, step_hint, ingested_at
)
SELECT
    if(t %% 2 = 0, '{GRANDE}', concat('{PREFIJO}', toString(t %% 10))) AS project_id,
    lower(hex(MD5(concat(%(semilla)s, toString(t)))))                 AS trace_id,
    substring(lower(hex(MD5(concat(%(semilla)s, toString(n), 's')))), 1, 16) AS span_id,
    if(p = 0, '',
       substring(lower(hex(MD5(concat(%(semilla)s, toString(t * {SPANS_POR_TRAZA}), 's')))),
                 1, 16))                                              AS parent_span_id,
    multiIf(p = 0, 'atender_ticket', p = 1, 'chat gpt-5.6-luna',
            p = 3, 'chat gpt-5.6-terra', p = 5, 'chat claude-sonnet-5',
            p = 2, 'buscar_vuelo', p = 4, 'reservar', 'preparar')    AS name,
    multiIf(p = 0, 'agent', p IN (1, 3, 5), 'llm', p IN (2, 4), 'tool', 'chain')
                                                                      AS span_type,
    if(p = 0 AND t %% 33 = 0, 'error', 'ok')                           AS status,
    addMilliseconds(toDateTime64(%(desde)s, 6, 'UTC'),
                    toInt64(t * %(ms_por_traza)s) + p * 300)          AS start_time,
    addMilliseconds(start_time, if(p = 0, 2400, 250))                 AS end_time,
    if(p = 0, 2400., 250.)                                            AS duration_ms,
    if(p IN (1, 3), 'openai', if(p = 5, 'anthropic', ''))             AS gen_ai_system,
    if(p IN (1, 3, 5), 'chat', '')                                    AS operation,
    multiIf(p = 1, 'gpt-5.6-luna', p = 3, 'gpt-5.6-terra', p = 5, 'claude-sonnet-5', '')
                                                                      AS request_model,
    request_model                                                     AS response_model,
    if(p IN (1, 3, 5), 1000 + (n * 7919) %% 5000, 0)                   AS input_tokens,
    if(p IN (1, 3, 5), 20 + (n * 104729) %% 300, 0)                    AS output_tokens,
    if(p IN (1, 3, 5), intDiv(input_tokens, 2), 0)                    AS cached_input_tokens,
    input_tokens * multiIf(p = 1, 0.2, p = 3, 1.0, p = 5, 2.0, 0) / 1e6 AS cost_input_usd,
    output_tokens * multiIf(p = 1, 1.2, p = 3, 6.0, p = 5, 10.0, 0) / 1e6 AS cost_output_usd,
    cost_input_usd + cost_output_usd                                  AS cost_total_usd,
    if(request_model != '', concat(request_model, ' @ carga'), '')    AS price_rate,
    if(p IN (1, 3, 5),
       concat('[{{"role":"system","content":"', %(instrucciones)s,
              '"}},{{"role":"user","content":"pregunta ', toString(t %% 997),
              ' sobre el ticket TK', toString(t), '"}}]'),
       '')                                                            AS input_messages,
    if(p IN (1, 3, 5),
       concat('[{{"role":"assistant","content":"respuesta ', toString(n %% 5003), '"}}]'),
       '')                                                            AS output_messages,
    if(p IN (2, 4), name, '')                                         AS tool_name,
    if(p IN (2, 4), concat('{{"vuelo":"IB', toString(t %% 9000), '"}}'), '') AS tool_arguments,
    if(p IN (2, 4), '{{"ok":true}}', '')                              AS tool_output,
    concat('sesion-', toString(intDiv(t, 5)))                         AS session_id,
    concat('cliente-', toString(t %% 5000))                            AS user_id,
    -- Una de cada diez trazas repite la llamada a terra: mismo hash que la de luna.
    if(t %% 10 = 0 AND p IN (1, 3), concat('rep-', toString(t)), toString(n))
                                                                      AS dedup_hash,
    dedup_hash                                                        AS loop_hash,
    toString(n)                                                       AS loop_out_hash,
    if(p IN (1, 3, 5), concat('paso-', toString(p)), '')              AS step_key,
    if(p IN (1, 3, 5), 'atender_ticket', '')                          AS step_site,
    if(p IN (1, 3, 5), multiIf(p = 1, 'clasificar', p = 3, 'extraer', 'responder'), '')
                                                                      AS step_label,
    ''                                                                AS step_hint,
    now64(3)                                                          AS ingested_at
FROM (
    SELECT number AS n,
           intDiv(number, {SPANS_POR_TRAZA}) AS t,
           number %% {SPANS_POR_TRAZA} AS p
    FROM numbers(%(inicio)s, %(cuantos)s)
)
"""


class Cronometrado:
    """El almacén, apuntando cuánto tarda cada método. Es lo que dice qué consulta pesa
    dentro de una pantalla que hace diez."""

    def __init__(self, store: ClickHouseStore) -> None:
        self._store = store
        self.tiempos: dict[str, list[float]] = defaultdict(list)

    def __getattr__(self, nombre: str):
        valor = getattr(self._store, nombre)
        if not callable(valor):
            return valor

        def medido(*args, **kwargs):
            inicio = time.perf_counter()
            try:
                return valor(*args, **kwargs)
            finally:
                self.tiempos[nombre].append(time.perf_counter() - inicio)

        return medido


def generar(store: ClickHouseStore, spans: int, lote: int) -> None:
    hoy = datetime.now(timezone.utc)
    desde = hoy - timedelta(days=1)
    trazas = spans // SPANS_POR_TRAZA
    ms_por_traza = 86_400_000 / max(trazas, 1)
    semilla = f"{time.time_ns()}-"
    inicio = time.perf_counter()
    for comienzo in range(0, spans, lote):
        store._client.command(
            GENERAR,
            parameters={
                "semilla": semilla,
                "desde": desde.strftime("%Y-%m-%d %H:%M:%S"),
                "ms_por_traza": ms_por_traza,
                "instrucciones": _INSTRUCCIONES,
                "inicio": comienzo,
                "cuantos": min(lote, spans - comienzo),
            },
        )
        hechos = min(comienzo + lote, spans)
        ritmo = hechos / (time.perf_counter() - inicio)
        print(f"  {hechos:>11,} spans · {ritmo:,.0f} spans/s", flush=True)


def _medir(nombre: str, fn, veces: int = 3) -> float:
    tiempos = []
    for _ in range(veces):
        inicio = time.perf_counter()
        fn()
        tiempos.append(time.perf_counter() - inicio)
    mejor, peor = min(tiempos), max(tiempos)
    marca = "OK " if peor <= OBJETIVO_S else "   "
    print(f"  {marca}{nombre:<46} mejor {mejor:6.2f} s · peor {peor:6.2f} s")
    return peor


def _desglose(cron: Cronometrado) -> None:
    """Todas las lecturas del Diagnóstico, no sólo las cuatro más pesadas: una lectura
    nueva (`step_cost_series`, D-152) tiene que verse aunque no esté arriba del todo.

    Con `Recordado` cada lectura se hace una vez por Diagnóstico, así que la media de
    cada método entre las tres medidas es lo que cuesta en una carga, y la suma de las
    medias se compara con el total para ver qué parte es Python y no consulta (D-154).
    """
    medias = {m: sum(t) / 3 for m, t in cron.tiempos.items()}
    total = sum(medias.values())
    for metodo, media in sorted(medias.items(), key=lambda kv: -kv[1]):
        veces = len(cron.tiempos[metodo]) / 3
        print(
            f"        {metodo:<32} {media:6.2f} s  {media / total:4.0%}"
            + (f"  ×{veces:.0f}" if veces > 1 else "")
        )
    print(f"        {'suma de lecturas':<32} {total:6.2f} s")


def medir(store: ClickHouseStore) -> dict[str, float]:
    ahora = datetime.now(timezone.utc)
    resultados: dict[str, float] = {}
    filas = store._client.query(
        f"SELECT count(), uniqExact(trace_id) FROM spans WHERE project_id LIKE '{PREFIJO}%'"
    ).result_rows[0]
    print(f"\n{filas[0]:,} spans de carga en {filas[1]:,} trazas\n")

    for dias in (1, 7):
        ventana = Window(since=ahora - timedelta(days=dias), until=ahora, days=dias)
        cron = Cronometrado(store)
        resultados[f"diagnostico {dias}d"] = _medir(
            f"Diagnóstico, proyecto grande, {dias} d",
            lambda v=ventana, c=cron: insights.overview(c, GRANDE, v),
        )
        _desglose(cron)
        resultados[f"panel {dias}d"] = _medir(
            f"Panel, proyecto grande, {dias} d",
            lambda v=ventana: panel.build(store, GRANDE, v),
        )

    desde = ahora - timedelta(days=7)
    resultados["lista"] = _medir(
        "Lista de trazas, primera página",
        lambda: store.list_traces(TraceFilter(project_id=GRANDE, since=desde, limit=50)),
    )
    resultados["lista errores"] = _medir(
        "Lista de trazas, sólo errores",
        lambda: store.list_traces(
            TraceFilter(project_id=GRANDE, since=desde, limit=50, status="error")
        ),
    )
    resultados["lista búsqueda"] = _medir(
        "Lista de trazas, buscando «reservar»",
        lambda: store.list_traces(
            TraceFilter(project_id=GRANDE, since=desde, limit=50, search="reservar")
        ),
    )
    # Un id que sale en una sola traza —lo que se busca de verdad— y un texto que sale
    # en miles, donde el índice no puede descartar nada (D-144).
    resultados["contenido raro"] = _medir(
        "Buscar en el contenido un id («TK123456»)",
        lambda: store.list_traces(
            TraceFilter(project_id=GRANDE, since=desde, limit=50, content="TK123456")
        ),
    )
    resultados["contenido común"] = _medir(
        "Buscar en el contenido algo común",
        lambda: store.list_traces(
            TraceFilter(project_id=GRANDE, since=desde, limit=50, content="respuesta 17")
        ),
    )
    traza = store.list_traces(TraceFilter(project_id=GRANDE, since=desde, limit=1)).traces[0]
    resultados["traza con proyecto"] = _medir(
        "Abrir una traza (con proyecto)",
        lambda: store.get_trace_spans(traza.trace_id, GRANDE),
    )
    resultados["traza sin proyecto"] = _medir(
        "Abrir una traza (sólo el id)",
        lambda: store.get_trace_spans(traza.trace_id),
    )

    compresion = store._client.query(
        """SELECT formatReadableSize(sum(data_compressed_bytes)),
                  formatReadableSize(sum(data_uncompressed_bytes)),
                  round(sum(data_uncompressed_bytes) / sum(data_compressed_bytes), 1)
           FROM system.columns WHERE database = currentDatabase() AND table = 'spans'"""
    ).result_rows[0]
    print(f"\n  Disco: {compresion[0]} ({compresion[1]} sin comprimir, {compresion[2]}x)")
    fallan = [k for k, v in resultados.items() if v > OBJETIVO_S]
    print(f"  Por encima de {OBJETIVO_S} s: {', '.join(fallan) if fallan else 'ninguna'}")
    return resultados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spans", type=int, default=10_000_000)
    parser.add_argument("--lote", type=int, default=1_000_000)
    parser.add_argument("--solo-medir", action="store_true")
    parser.add_argument("--borrar", action="store_true")
    args = parser.parse_args()

    store = ClickHouseStore(Settings())
    if not store.health():
        print("no hay ClickHouse escuchando: docker compose up -d clickhouse")
        return 1
    store.migrate()
    if args.borrar:
        store._client.command(f"DELETE FROM spans WHERE project_id LIKE '{PREFIJO}%'")
        print("datos de carga borrados")
        return 0
    if not args.solo_medir:
        print(f"Generando {args.spans:,} spans en un día…")
        # Sin OPTIMIZE FINAL: una tabla recién fusionada es más rápida que la de un
        # servidor de verdad, que siempre tiene partes sin fusionar, y fusionar diez
        # millones de filas a la vez que se mide tumbó Docker en este portátil (D-142).
        generar(store, args.spans, args.lote)
    medir(store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
