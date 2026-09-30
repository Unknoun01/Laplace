"""Preagregados del Diagnóstico en ClickHouse (D-177).

El Diagnóstico de un proyecto con diez millones de spans al día hacía una decena de
barridos de la ventana, uno por lector, y cada uno cuesta lo que cuesta leer y deduplicar
con `FINAL` cinco millones de filas: 6–7 s el de un día, casi un minuto el de siete, en el
contenedor de 4 núcleos de D-166. Lanzarlos a la vez no ayuda: ClickHouse ya satura los
núcleos con uno solo. Hacía falta leer menos.

**Qué se guarda.** Parciales por minuto en cuatro tablas (ver `clickhouse_schema.sql`):

* `pre_pasos`: por paso, modelo, prompt y tipo de span, con sumas y, para lo que no se
  suma, estados de ClickHouse (trazas distintas, medianas, el mínimo de entrada);
* `pre_trazas`: por traza, su primer inicio y su último fin en el minuto;
* `pre_repes` y `pre_bucles`: por pareja (traza, entrada), sólo las que pueden llegar a
  repetirse (ver `_CANDIDATAS`).

**Cómo se calculan.** Por horas enteras, desde `spans FINAL`, y sustituyendo al cálculo
anterior de la hora: nunca se suman al insertar. Un lote reenviado y `recalcular_coste`
vuelven a insertar spans, y una tabla que sumara al insertar los contaría dos veces
(la trampa que la hoja de ruta dejó escrita). Cada escritura en `spans` apunta sus horas
en `pre_sucias`; el bucle de fondo recalcula las que tienen un apunte posterior a su
último cálculo.

**Cómo se leen.** Cada lector es una sola agregación sobre parciales. Los de las horas
limpias salen de las tablas; los del resto de la ventana (las horas sucias o sin
calcular, que siempre incluyen la hora en curso, y los trozos de minuto de los bordes)
se calculan en el momento con **la misma** selección que usa el cálculo. Así no hay dos
cuentas que mantener, y lo que se lee es siempre exacto: una hora que no está al día no
se lee de las tablas. `test_preagregados.py` exige que el Diagnóstico salga igual que
calculado en crudo.

**Las tiradas de evaluación.** Las reglas no las miran (D-135). Aquí son las trazas con la
etiqueta en un span a menos de una hora de la hora que se calcula; en crudo eran las que
la llevan dentro de la ventana. Sólo difieren si una tirada dura más de una hora y cruza
el borde de la ventana.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from laplace.semconv import EVAL_TAG

logger = logging.getLogger("laplace.preagregados")

HORA = timedelta(hours=1)
MINUTO = timedelta(minutes=1)
#: Lo que se mira alrededor de una hora para saber si una traza la cruza o si es una
#: tirada de evaluación.
MARGEN = timedelta(hours=1)
#: Horas recalculadas como mucho por vuelta del bucle de fondo.
POR_VUELTA = 48

TABLAS = ("pre_pasos", "pre_trazas", "pre_repes", "pre_bucles")


# ---------------------------------------------------------------------------------
# Las selecciones parciales: la misma para calcular una hora y para leer en crudo
# ---------------------------------------------------------------------------------

#: Si la traza es una tirada de evaluación. `%(evaluaciones)s` nunca va vacía: sin
#: ninguna lleva `''`, que no es el id de ninguna traza.
_ES_EVAL = "toUInt8(trace_id IN %(evaluaciones)s)"

COLUMNAS = {
    "pre_pasos": (
        "project_id, minuto, es_eval, span_type, step_key, name, step_label, step_site, "
        "step_hint, request_model, prompt_name, prompt_version, n, n_con_coste, coste, "
        "tok_in, tok_out, cache_tok, cache_escrito, ahorro_cache, duracion, sin_tarifa, "
        "asumida, con_tokens, primero, ultimo, traza_min, traza_max, "
        "min_entrada, mediana_dur, mediana_sal"
    ),
    "pre_trazas": (
        "project_id, minuto, trace_id, es_eval, fallo, inicio, fin, g_uso, g_cob, g_prompt"
    ),
    "pre_repes": (
        "project_id, minuto, es_eval, trace_id, dedup_hash, etiqueta, pista, sitio, tipo, "
        "modelo, paso, ultima, n, coste, duracion, tok_in, tok_out, sin_tarifa, asumida, "
        "primera"
    ),
    "pre_bucles": (
        "project_id, minuto, es_eval, trace_id, loop_hash, etiqueta, pista, sitio, tipo, "
        "modelo, paso, ultima, n, entradas, salidas, coste, coste_min, duracion, "
        "duracion_min, tok_in, tok_in_min, tok_out, tok_out_min, sin_tarifa"
    ),
}

_PARCIAL = {
    "pre_pasos": f"""
        SELECT
            project_id,
            toStartOfMinute(start_time)          AS minuto,
            {_ES_EVAL}                           AS es_eval,
            span_type, step_key, name, step_label, step_site, step_hint, request_model,
            prompt_name, prompt_version,
            count()                              AS n,
            countIf(cost_total_usd > 0)          AS n_con_coste,
            sum(cost_total_usd)                  AS coste,
            sum(input_tokens)                    AS tok_in,
            sum(output_tokens)                   AS tok_out,
            sum(cached_input_tokens)             AS cache_tok,
            sum(cache_write_tokens + cache_write_1h_tokens) AS cache_escrito,
            sum(cost_cache_saving_usd)           AS ahorro_cache,
            sum(duration_ms)                     AS duracion,
            countIf(cost_unknown = 1)            AS sin_tarifa,
            countIf(cost_rate_assumed = 1)       AS asumida,
            countIf(usage_estimated = 0 AND (input_tokens > 0 OR output_tokens > 0))
                                                 AS con_tokens,
            min(start_time)                      AS primero,
            max(start_time)                      AS ultimo,
            min(trace_id)                        AS traza_min,
            max(trace_id)                        AS traza_max,
            minIfState(input_tokens, status != 'error' AND input_tokens > 0) AS min_entrada,
            quantileExactIfState(0.5)(duration_ms, status != 'error')   AS mediana_dur,
            quantileExactIfState(0.5)(output_tokens, status != 'error') AS mediana_sal
        FROM spans FINAL
        WHERE project_id = %(project_id)s AND {{rango}}
        GROUP BY project_id, minuto, es_eval, span_type, step_key, name, step_label,
                 step_site, step_hint, request_model, prompt_name, prompt_version
    """,
    "pre_trazas": f"""
        SELECT project_id, toStartOfMinute(start_time) AS minuto, trace_id,
               {_ES_EVAL}                        AS es_eval,
               max(status = 'error')             AS fallo,
               min(start_time) AS inicio, max(end_time) AS fin,
               groupUniqArrayIf({{g_uso}}, span_type = 'llm' AND request_model != '') AS g_uso,
               groupUniqArrayIf({{g_cob}}, span_type = 'llm')                         AS g_cob,
               groupUniqArrayIf({{g_prompt}}, prompt_name != '')                     AS g_prompt
        FROM spans FINAL
        WHERE project_id = %(project_id)s AND {{rango}}
        GROUP BY project_id, minuto, trace_id, es_eval
    """,
    "pre_repes": f"""
        SELECT
            project_id,
            toStartOfMinute(start_time)          AS minuto,
            {_ES_EVAL}                           AS es_eval,
            trace_id, dedup_hash,
            max(if(step_label != '', step_label, name)) AS etiqueta,
            max(step_hint)                       AS pista,
            max(step_site)                       AS sitio,
            max(span_type)                       AS tipo,
            max(request_model)                   AS modelo,
            max(if(step_key != '', step_key, name)) AS paso,
            max(start_time)                      AS ultima,
            count()                              AS n,
            sum(cost_total_usd)                  AS coste,
            sum(duration_ms)                     AS duracion,
            sum(input_tokens)                    AS tok_in,
            sum(output_tokens)                   AS tok_out,
            sum(cost_unknown)                    AS sin_tarifa,
            sum(cost_rate_assumed)               AS asumida,
            -- La primera ocurrencia, con el desempate dentro de la clave (D-099).
            argMinState((cost_total_usd, duration_ms, input_tokens, output_tokens,
                         cost_unknown, cost_rate_assumed), (start_time, span_id)) AS primera
        FROM spans FINAL
        WHERE project_id = %(project_id)s AND {{rango}} AND dedup_hash != ''
          AND {{candidatas}}
        GROUP BY project_id, minuto, es_eval, trace_id, dedup_hash
    """,
    "pre_bucles": f"""
        SELECT
            project_id,
            toStartOfMinute(start_time)          AS minuto,
            {_ES_EVAL}                           AS es_eval,
            trace_id, loop_hash,
            max(if(step_label != '', step_label, name)) AS etiqueta,
            max(step_hint)                       AS pista,
            max(step_site)                       AS sitio,
            max(span_type)                       AS tipo,
            max(request_model)                   AS modelo,
            max(if(step_key != '', step_key, name)) AS paso,
            max(start_time)                      AS ultima,
            count()                              AS n,
            uniqExactState(dedup_hash)           AS entradas,
            uniqExactState(loop_out_hash)        AS salidas,
            sum(cost_total_usd)                  AS coste,
            min(cost_total_usd)                  AS coste_min,
            sum(duration_ms)                     AS duracion,
            min(duration_ms)                     AS duracion_min,
            sum(input_tokens)                    AS tok_in,
            min(input_tokens)                    AS tok_in_min,
            sum(output_tokens)                   AS tok_out,
            min(output_tokens)                   AS tok_out_min,
            sum(cost_unknown)                    AS sin_tarifa
        FROM spans FINAL
        WHERE project_id = %(project_id)s AND {{rango}} AND loop_hash != ''
          AND {{candidatas}}
        GROUP BY project_id, minuto, es_eval, trace_id, loop_hash
    """,
}

#: Cómo se nombra cada grupo en `pre_trazas`, a partir de las columnas de un span. Los
#: lectores calculan el mismo hash a partir de sus claves agregadas, y así cruzan cada
#: grupo con sus ejecuciones distintas.
G_USO = "sipHash64(if(step_key != '', step_key, name), toString(request_model))"
G_COB = "sipHash64(multiIf(step_site != '', step_site, step_label != '', step_label, name))"
G_PROMPT = "sipHash64(toString(prompt_name), prompt_version)"


#: Qué parejas se guardan. Una pareja cuenta en una ventana si tiene dos spans o más
#: dentro de ella. Si todos sus spans caen en la misma hora (lo normal: una ejecución
#: dura segundos), no llega a dos en ninguna ventana si no los tiene en la hora entera.
#: Se guardan, por tanto, las que tienen dos o más en su ámbito (sin `FINAL`: un span
#: reenviado sólo cuela una candidata de más, que el HAVING del lector descarta) y todas
#: las de las trazas que cruzan el borde del ámbito, que pueden sumar con la hora de al
#: lado. `{ambito}` son horas enteras.
_CANDIDATAS = """(
    sipHash64(trace_id, {columna}) IN (
        SELECT sipHash64(trace_id, {columna}) FROM spans
        WHERE project_id = %(project_id)s AND {ambito} AND {columna} != ''
        GROUP BY sipHash64(trace_id, {columna}) HAVING count() >= 2
    )
    OR trace_id IN (
        SELECT trace_id FROM spans
        WHERE project_id = %(project_id)s
          AND start_time >= %(cruce_desde)s AND start_time < %(cruce_hasta)s
        GROUP BY trace_id
        HAVING countIf({ambito}) > 0 AND countIf(NOT ({ambito})) > 0
    )
)"""


def _candidatas(tabla: str, ambito: str) -> str:
    columna = "dedup_hash" if tabla == "pre_repes" else "loop_hash"
    return _CANDIDATAS.format(columna=columna, ambito=ambito)


def _seleccion(tabla: str, rango: str, ambito: str) -> str:
    sql = _PARCIAL[tabla]
    if tabla in ("pre_repes", "pre_bucles"):
        return sql.format(rango=rango, candidatas=_candidatas(tabla, ambito))
    if tabla == "pre_trazas":
        return sql.format(rango=rango, g_uso=G_USO, g_cob=G_COB, g_prompt=G_PROMPT)
    return sql.format(rango=rango)


# ---------------------------------------------------------------------------------
# Horas
# ---------------------------------------------------------------------------------


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def hora_de(t: datetime) -> datetime:
    return _utc(t).replace(minute=0, second=0, microsecond=0)


def _minuto_abajo(t: datetime) -> datetime:
    return _utc(t).replace(second=0, microsecond=0)


def _minuto_arriba(t: datetime) -> datetime:
    abajo = _minuto_abajo(t)
    return abajo if abajo == _utc(t) else abajo + MINUTO


def _texto_ms(t: datetime) -> str:
    """Un instante con sus milésimas, escrito a mano: el cálculo se reconoce por él."""
    return _utc(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _evaluaciones(client: Any, project_id: str, desde: datetime, hasta: datetime) -> list[str]:
    ids = [
        fila[0]
        for fila in client.query(
            f"""SELECT DISTINCT trace_id FROM spans
                WHERE project_id = %(p)s AND start_time >= %(d)s AND start_time < %(h)s
                  AND has(tags, '{EVAL_TAG}')""",
            parameters={"p": project_id, "d": desde, "h": hasta},
        ).result_rows
    ]
    return ids or [""]


def marcar(client: Any, horas: set[tuple[str, datetime]]) -> None:
    """Apunta horas sucias. La marca la pone el servidor, después de lo que las ensucia."""
    if not horas:
        return
    client.insert(
        "pre_sucias",
        [[proyecto, hora_de(h).replace(tzinfo=None)] for proyecto, h in sorted(horas)],
        column_names=["project_id", "hora"],
        # Como los spans: una parte por lote de la ingesta ahogaría las fusiones. La marca
        # la pone el servidor al escribir el lote, que es después de los spans.
        settings={"async_insert": 1, "wait_for_async_insert": 1},
    )


def recalcular(client: Any, project_id: str, hora: datetime) -> None:
    """Calcula los parciales de una hora entera y la da por buena al final."""
    hora = hora_de(hora)
    calculado = client.query("SELECT now64(3)").result_rows[0][0]
    params = {
        "project_id": project_id,
        "evaluaciones": _evaluaciones(client, project_id, hora - MARGEN, hora + HORA + MARGEN),
        "desde": hora,
        "hasta": hora + HORA,
        "cruce_desde": hora - MARGEN,
        "cruce_hasta": hora + HORA + MARGEN,
    }
    rango = "start_time >= %(desde)s AND start_time < %(hasta)s"
    for tabla in TABLAS:
        client.command(
            f"""INSERT INTO {tabla} ({COLUMNAS[tabla]}, calculado)
                SELECT {COLUMNAS[tabla]}, toDateTime64('{_texto_ms(calculado)}', 3, 'UTC')
                FROM ({_seleccion(tabla, rango, rango)})""",
            parameters=params,
        )
    # Lo último: hasta aquí se sigue leyendo el cálculo anterior, entero.
    client.insert(
        "pre_horas",
        [[project_id, hora.replace(tzinfo=None), calculado]],
        column_names=["project_id", "hora", "calculado"],
    )


def pendientes(
    client: Any, limite: int = POR_VUELTA, quieta_s: int = 120
) -> list[tuple[str, datetime]]:
    """Las horas con escrituras posteriores a su último cálculo, las más recientes antes."""
    filas = client.query(
        """
        SELECT s.project_id, s.hora
        FROM (SELECT project_id, hora, max(marca) AS m FROM pre_sucias
              GROUP BY project_id, hora) AS s
        LEFT JOIN (SELECT project_id, hora, max(calculado) AS c FROM pre_horas
                   GROUP BY project_id, hora) AS h
          ON s.project_id = h.project_id AND s.hora = h.hora
        WHERE s.m >= h.c
          -- La hora en curso se ensucia a los pocos segundos mientras llegan datos, y
          -- se lee en crudo igual: se calcula cuando acaba o cuando lleva un rato quieta.
          AND (s.hora < toStartOfHour(now()) OR s.m < now64(3) - INTERVAL %(quieta)s SECOND)
        ORDER BY s.hora DESC
        LIMIT %(n)s
        """,
        parameters={"n": limite, "quieta": quieta_s},
    ).result_rows
    return [(p, _utc(h)) for p, h in filas]


def recalcular_pendientes(client: Any, limite: int = POR_VUELTA, quieta_s: int = 120) -> int:
    hechas = 0
    for project_id, hora in pendientes(client, limite, quieta_s):
        try:
            recalcular(client, project_id, hora)
            hechas += 1
        except Exception:  # noqa: BLE001 - una hora que falla no para las demás
            logger.exception("preagregados: no se ha podido recalcular %s %s", project_id, hora)
    return hechas


#: Cada cuánto mira el bucle de fondo si hay horas que recalcular.
CADA_S = 30


async def bucle(store: Any, cada_s: int = CADA_S) -> None:
    """Recalcula lo pendiente cada poco. Con varios procesos puede que dos recalculen la
    misma hora a la vez: da igual, gana el cálculo más nuevo y los dos son correctos."""
    while True:
        try:
            await asyncio.to_thread(store.recalcular_preagregados)
        except Exception:  # noqa: BLE001 - el bucle no se cae por una vuelta
            logger.exception("preagregados: la vuelta ha fallado")
        await asyncio.sleep(cada_s)


def marcar_todo_si_nuevo(client: Any) -> None:
    """Una instalación con datos de antes de los preagregados: todas sus horas, sucias.

    Sin esto nunca se calcularían (nadie las ha escrito desde entonces) y se leerían
    siempre en crudo, que es correcto pero lento.
    """
    hay_calculo = client.query("SELECT count() FROM pre_horas").result_rows[0][0]
    hay_marcas = client.query("SELECT count() FROM pre_sucias").result_rows[0][0]
    if hay_calculo or hay_marcas:
        return
    client.command(
        "INSERT INTO pre_sucias (project_id, hora) "
        "SELECT DISTINCT project_id, toStartOfHour(start_time) FROM spans"
    )


# ---------------------------------------------------------------------------------
# Lectura: parciales de las tablas donde están al día y en crudo donde no
# ---------------------------------------------------------------------------------

#: Las horas de la ventana cuyo último cálculo es posterior a su última escritura.
_LIMPIAS = """
    SELECT h.hora, h.c FROM (
        SELECT hora, max(calculado) AS c FROM pre_horas
        WHERE project_id = %(project_id)s AND hora >= %(h_desde)s AND hora <= %(h_hasta)s
        GROUP BY hora
    ) AS h
    LEFT JOIN (
        SELECT hora, max(marca) AS m FROM pre_sucias
        WHERE project_id = %(project_id)s AND hora >= %(h_desde)s AND hora <= %(h_hasta)s
        GROUP BY hora
    ) AS s ON h.hora = s.hora
    WHERE s.m < h.c
"""


def _literal(t: datetime, precision: int = 6) -> str:
    """Un instante escrito en la SQL. Sale de la base o del reloj, nunca de fuera."""
    texto = _utc(t).strftime("%Y-%m-%d %H:%M:%S.%f")
    if precision == 0:
        return f"toDateTime('{texto[:19]}', 'UTC')"
    return f"toDateTime64('{texto[: 20 + precision]}', {precision}, 'UTC')"


def _unir(tramos: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    salida: list[tuple[datetime, datetime]] = []
    for desde, hasta in sorted(tramos):
        if salida and desde <= salida[-1][1]:
            salida[-1] = (salida[-1][0], max(salida[-1][1], hasta))
        else:
            salida.append((desde, hasta))
    return salida


def _rangos(columna: str, tramos: list[tuple[datetime, datetime]], hasta_incluido: datetime) -> str:
    """`[desde, hasta)` de cada tramo; el que acaba en el final de la ventana lo incluye,
    como `WINDOW_WHERE`."""
    partes = []
    for desde, hasta in tramos:
        cierre = "<=" if hasta == hasta_incluido else "<"
        partes.append(
            f"({columna} >= {_literal(desde)} AND {columna} {cierre} {_literal(hasta)})"
        )
    return "(" + " OR ".join(partes) + ")" if partes else "0"


@dataclass
class Ventana:
    """Qué sale de las tablas y qué se lee en crudo, decidido una vez por ventana."""

    params: dict[str, Any]
    #: (hora, cálculo) de las horas que se leen de las tablas.
    limpias: list[tuple[datetime, datetime]]
    #: Lo que se lee en crudo, en tramos de `start_time`.
    crudos: list[tuple[datetime, datetime]]
    #: Las horas enteras que tocan esos tramos, para buscar las parejas candidatas.
    ambito: list[tuple[datetime, datetime]]

    def parciales(self, tabla: str) -> str:
        """Los parciales de la ventana: de la tabla en las horas limpias, en crudo el resto."""
        partes = []
        if self.limpias:
            calculos = ", ".join(
                f"({_literal(h, 0)}, {_literal(c, 3)})" for h, c in self.limpias
            )
            partes.append(f"""
                SELECT {COLUMNAS[tabla]} FROM {tabla}
                WHERE project_id = %(project_id)s AND minuto >= %(a)s AND minuto < %(b)s
                  AND (toStartOfHour(minuto), calculado) IN ({calculos})""")
        if self.crudos or not partes:
            hasta = self.params["until"]
            rango = _rangos("start_time", self.crudos, hasta)
            ambito = _rangos("start_time", self.ambito, hasta)
            partes.append(f"SELECT {COLUMNAS[tabla]} FROM ({_seleccion(tabla, rango, ambito)})")
        return "(" + "\n UNION ALL\n".join(partes) + ")"


def ventana(client: Any, project_id: str, since: datetime, until: datetime) -> Ventana:
    since, until = _utc(since), _utc(until)
    h_desde, h_hasta = hora_de(since), hora_de(until)
    a, b = _minuto_arriba(since), _minuto_abajo(until)
    limpias = [
        (_utc(h), _utc(c))
        for h, c in client.query(
            _LIMPIAS,
            parameters={"project_id": project_id, "h_desde": h_desde, "h_hasta": h_hasta},
        ).result_rows
    ]
    # Lo que sale de las tablas: los minutos enteros de la ventana en horas limpias.
    cubiertos = _unir(
        [(max(h, a), min(h + HORA, b)) for h, _ in limpias if max(h, a) < min(h + HORA, b)]
    )
    # Lo que no, en crudo: la ventana menos lo cubierto.
    crudos, cursor = [], since
    for desde, hasta in cubiertos:
        if cursor < desde:
            crudos.append((cursor, desde))
        cursor = max(cursor, hasta)
    if cursor < until or (cursor == until and not cubiertos):
        crudos.append((cursor, until))
    elif cursor == until:
        # El instante final va incluido (`<=`), y ningún minuto cubierto lo lleva.
        crudos.append((until, until))
    ambito = _unir([(hora_de(d), hora_de(h) + HORA) for d, h in crudos])
    return Ventana(
        params={
            "project_id": project_id,
            "since": since,
            "until": until,
            "a": a,
            "b": b,
            "cruce_desde": (ambito[0][0] if ambito else h_desde) - MARGEN,
            "cruce_hasta": (ambito[-1][1] if ambito else h_hasta + HORA) + MARGEN,
            "evaluaciones": _evaluaciones(client, project_id, since - MARGEN, until + MARGEN),
        },
        limpias=limpias,
        crudos=crudos,
        ambito=ambito,
    )


def alineada(since: datetime) -> bool:
    """Si los tramos de una serie que empieza en `since` caen en minutos enteros."""
    return _utc(since) == _minuto_abajo(since)


# ---------------------------------------------------------------------------------
# Los lectores: una agregación sobre los parciales, con los alias de los de siempre
# ---------------------------------------------------------------------------------

RESUMEN = """
SELECT
    t.traces, p.spans, t.error_traces, p.llm_calls, p.tool_calls, p.input_tokens,
    p.output_tokens, p.cost, p.ahorro_cache, p.spans_sin_tarifa, p.tarifa_asumida,
    p.modelos_sin_tarifa, p.primero, p.ultimo
FROM (
    SELECT
        sum(n)                                     AS spans,
        sumIf(n, span_type = 'llm')                AS llm_calls,
        sumIf(n, span_type = 'tool')               AS tool_calls,
        sum(tok_in)                                AS input_tokens,
        sum(tok_out)                               AS output_tokens,
        sum(coste)                                 AS cost,
        sum(ahorro_cache)                          AS ahorro_cache,
        sum(sin_tarifa)                            AS spans_sin_tarifa,
        sum(asumida)                               AS tarifa_asumida,
        arrayDistinct(groupArrayIf(toString(request_model),
                                   sin_tarifa > 0 AND request_model != '')) AS modelos_sin_tarifa,
        min(primero)                               AS primero,
        max(ultimo)                                AS ultimo
    FROM {pre_pasos}
) AS p
CROSS JOIN (
    SELECT uniqExact(trace_id) AS traces, uniqExactIf(trace_id, fallo = 1) AS error_traces
    FROM {pre_trazas}
) AS t
"""

P95 = """
SELECT quantile(0.95)(duracion) FROM (
    SELECT dateDiff('millisecond', min(inicio), max(fin)) AS duracion
    FROM {pre_trazas}
    GROUP BY trace_id
)
"""

USO = """
SELECT u.* EXCEPT (h), t.trazas AS trazas
FROM (
    SELECT
        if(step_key != '', step_key, name) AS paso_clave,
        max(if(step_label != '', step_label, name)) AS paso,
        max(step_hint)           AS pista,
        max(step_site)           AS sitio,
        max(toString(prompt_name)) AS prompt,
        max(prompt_version)      AS version_prompt,
        request_model,
        sum(n)                   AS llamadas,
        sum(tok_in)              AS in_tok,
        sum(tok_out)             AS out_tok,
        sum(cache_tok)           AS cache_tok,
        sum(cache_escrito)       AS cache_escrito,
        sum(ahorro_cache)        AS ahorro_cache,
        sum(coste)               AS coste,
        sum(sin_tarifa)          AS sin_tarifa,
        sum(asumida)             AS tarifa_asumida,
        sum(tok_out) / sum(n)    AS media_salida,
        sum(tok_in) / sum(n)     AS media_entrada,
        minIfMerge(min_entrada)  AS min_entrada,
        sum(duracion)            AS duracion,
        quantileExactIfMerge(0.5)(mediana_dur) AS mediana,
        quantileExactIfMerge(0.5)(mediana_sal) AS mediana_salida,
        max(ultimo)              AS ultima,
        min(traza_min)           AS traza_ejemplo,
        sipHash64(paso_clave, toString(request_model)) AS h
    FROM {pre_pasos}
    WHERE es_eval = 0 AND span_type = 'llm' AND request_model != ''
    GROUP BY if(step_key != '', step_key, name), request_model
    HAVING llamadas >= %(min_calls)s
) AS u
LEFT JOIN (
    SELECT g AS h, uniqExact(trace_id) AS trazas
    FROM {pre_trazas} ARRAY JOIN g_uso AS g
    WHERE es_eval = 0
    GROUP BY g
) AS t ON u.h = t.h
ORDER BY coste DESC, paso_clave, request_model
LIMIT %(limit)s
"""

COBERTURA = """
SELECT
    sum(n)                                                   AS llamadas,
    sumIf(n, step_hint != '' OR step_label != name)          AS identificadas,
    sum(n) - sum(sin_tarifa)                                 AS con_tarifa,
    sum(con_tokens)                                          AS con_tokens,
    sumIf(n, prompt_name != '')                              AS con_prompt
FROM {pre_pasos}
WHERE span_type = 'llm'
"""

COBERTURA_PASOS = """
SELECT c.* EXCEPT (h), t.trazas AS trazas
FROM (
    SELECT
        multiIf(step_site != '', step_site, step_label != '', step_label, name) AS paso,
        max(if(step_label != '', step_label, name)) AS etiqueta,
        uniqExact(step_key)     AS identidades,
        sipHash64(paso)         AS h
    FROM {pre_pasos}
    WHERE span_type = 'llm'
    GROUP BY paso
) AS c
LEFT JOIN (
    SELECT g AS h, uniqExact(trace_id) AS trazas
    FROM {pre_trazas} ARRAY JOIN g_cob AS g
    GROUP BY g
) AS t ON c.h = t.h
"""

PROMPTS = """
SELECT v.* EXCEPT (h), t.trazas AS trazas
FROM (
    SELECT
        toString(prompt_name)          AS nombre,
        prompt_version                 AS version,
        sum(n)                         AS llamadas,
        sum(coste)                     AS coste,
        sum(tok_in)                    AS tok_in,
        sum(tok_out)                   AS tok_out,
        sum(duracion)                  AS duracion,
        sum(sin_tarifa)                AS sin_tarifa,
        sum(asumida)                   AS asumida,
        min(primero)                   AS primero,
        max(ultimo)                    AS ultimo,
        {pasos}                        AS pasos,
        {ejemplo}                      AS ejemplo,
        sipHash64(nombre, version)     AS h
    FROM {pre_pasos}
    WHERE {reglas} prompt_name != ''
    GROUP BY prompt_name, prompt_version
) AS v
LEFT JOIN (
    SELECT g AS h, uniqExact(trace_id) AS trazas
    FROM {pre_trazas} ARRAY JOIN g_prompt AS g
    WHERE {reglas} 1
    GROUP BY g
) AS t ON v.h = t.h
ORDER BY nombre, version DESC
"""

SERIE_TOTAL = """
SELECT {tramo} AS tramo, sum(coste) AS coste
FROM {pre_pasos}
GROUP BY tramo
"""

SERIE_PASOS = """
SELECT if(step_key != '', step_key, name) AS paso,
       max(if(step_label != '', step_label, name)) AS etiqueta,
       max(step_site) AS sitio, max(step_hint) AS pista,
       {tramo} AS tramo, sum(coste) AS coste
FROM {pre_pasos}
WHERE es_eval = 0 AND n_con_coste > 0
GROUP BY paso, tramo
"""

REPETICIONES = """
SELECT
    paso,
    modelo,
    max(etiqueta)                    AS nombre,
    max(pista)                       AS pista,
    max(sitio)                       AS sitio,
    max(tipo)                        AS tipo,
    argMax(dedup_hash, (n, dedup_hash)) AS hash_ejemplo,
    max(ultima)                      AS ultima,
    uniqExact(trace_id)              AS trazas,
    sum(n)                           AS total_spans,
    sum(n - 1)                       AS extra_spans,
    sum(coste - primera.1)           AS extra_coste,
    sum(duracion - primera.2)        AS extra_duracion,
    sum(tok_in - primera.3)          AS extra_tok_in,
    sum(tok_out - primera.4)         AS extra_tok_out,
    sum(sin_tarifa - primera.5)      AS extra_sin_tarifa,
    sum(asumida - primera.6)         AS extra_asumida,
    max(n)                           AS max_por_traza,
    argMax(trace_id, (n, trace_id))  AS traza_ejemplo
FROM (
    SELECT
        trace_id, dedup_hash,
        max(etiqueta) AS etiqueta, max(pista) AS pista, max(sitio) AS sitio,
        max(tipo) AS tipo, max(modelo) AS modelo, max(paso) AS paso,
        max(ultima) AS ultima, sum(n) AS n, sum(coste) AS coste,
        sum(duracion) AS duracion, sum(tok_in) AS tok_in, sum(tok_out) AS tok_out,
        sum(sin_tarifa) AS sin_tarifa, sum(asumida) AS asumida,
        argMinMerge(primera) AS primera
    FROM {pre_repes}
    WHERE es_eval = 0
    GROUP BY trace_id, dedup_hash
    HAVING n >= %(min_repeats)s
)
GROUP BY paso, modelo
ORDER BY extra_coste DESC, extra_spans DESC, paso, modelo
LIMIT %(limit)s
"""

BUCLES = """
SELECT
    max(loop_hash)                   AS hash_ejemplo,
    max(ultima)                      AS ultima,
    max(etiqueta)                    AS nombre,
    max(pista)                       AS pista,
    max(sitio)                       AS sitio,
    max(tipo)                        AS tipo,
    max(modelo)                      AS modelo,
    paso_clave                       AS paso,
    uniqExact(trace_id)              AS trazas,
    sum(n)                           AS total_spans,
    sum(n - 1)                       AS extra_spans,
    max(n)                           AS max_por_traza,
    max(entradas)                    AS distintas_entradas,
    max(salidas)                     AS distintas_salidas,
    sum(coste - coste_min)           AS extra_coste,
    sum(duracion - duracion_min)     AS extra_duracion,
    sum(tok_in - tok_in_min)         AS extra_tok_in,
    sum(tok_out - tok_out_min)       AS extra_tok_out,
    sum(sin_tarifa)                  AS extra_sin_tarifa,
    max(trace_id)                    AS traza_ejemplo
FROM (
    SELECT
        trace_id, loop_hash,
        max(etiqueta) AS etiqueta, max(pista) AS pista, max(sitio) AS sitio,
        max(tipo) AS tipo, max(modelo) AS modelo, max(paso) AS paso_clave,
        max(ultima) AS ultima, sum(n) AS n,
        uniqExactMerge(entradas) AS entradas, uniqExactMerge(salidas) AS salidas,
        sum(coste) AS coste, min(coste_min) AS coste_min,
        sum(duracion) AS duracion, min(duracion_min) AS duracion_min,
        sum(tok_in) AS tok_in, min(tok_in_min) AS tok_in_min,
        sum(tok_out) AS tok_out, min(tok_out_min) AS tok_out_min,
        sum(sin_tarifa) AS sin_tarifa
    FROM {pre_bucles}
    WHERE es_eval = 0
    GROUP BY trace_id, loop_hash
    HAVING n >= %(min_vueltas)s AND entradas > 1 AND salidas <= %(max_salidas)s
)
GROUP BY paso_clave
ORDER BY extra_spans DESC, paso_clave
LIMIT %(limit)s
"""


def sql(v: Ventana, plantilla: str, **extra: str) -> str:
    """La plantilla de un lector con los parciales de la ventana puestos."""
    return plantilla.format(
        **{tabla: v.parciales(tabla) for tabla in TABLAS if "{" + tabla + "}" in plantilla},
        **extra,
    )
