"""Migra la tabla `spans` de ClickHouse a la clave de ordenación por día (D-168).

    python -m laplace_backend.storage.migrar_orden            # dice qué haría
    python -m laplace_backend.storage.migrar_orden --hacerlo  # lo hace

En Docker: `docker compose exec backend python -m laplace_backend.storage.migrar_orden`.

Con la clave `(project_id, trace_id, span_id)` los días se mezclan en cada gránulo, y una
ventana de un día lee casi todo el histórico del proyecto (D-166). Con
`(project_id, toDate(start_time), trace_id, span_id)` lee su día. Las instalaciones
nuevas nacen así; las que ya existen se migran con esto, a mano y cuando se quiera,
porque es una copia entera de la tabla y no puede hacerse de paso al arrancar.

Cómo, y por qué es seguro:

1. **Copia a una tabla nueva, partición a partición y día a día.** La tabla vieja no se
   toca. Si se corta a medias, se vuelve a lanzar: una partición que ya cuadra se salta y
   una que no se tira y se copia otra vez (tirar una partición en ClickHouse es gratis).
2. **La ingesta no se para.** Lo que llega mientras se copia se recoge al final por su
   `ingested_at`, y otra vez justo después del cambio de nombre, que es atómico
   (`EXCHANGE TABLES`). Copiar un span dos veces no duplica nada: la tabla es
   `ReplacingMergeTree` y las lecturas ya se quedan con una versión de cada span.
3. **La tabla vieja no se borra.** Queda como `spans_antes_d168` con todo dentro, y el
   final dice cómo borrarla cuando se haya comprobado que todo cuadra.
4. **La retención se vuelve a poner**: el TTL no pasa con `CREATE TABLE ... AS`.

Hace falta sitio para una segunda copia de la tabla mientras dura. Se comprueba antes.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("laplace.migrar_orden")

CLAVE_NUEVA = "project_id, toDate(start_time), trace_id, span_id"
CLAVE_VIEJA = "project_id, trace_id, span_id"
NUEVA = "spans_d168"
ANTES = "spans_antes_d168"

#: Lo que llegue en los últimos minutos antes de empezar se vuelve a copiar al final,
#: por si una inserción asíncrona se confirmó con retraso.
MARGEN = timedelta(minutes=5)


def clave(client, tabla: str = "spans") -> str:
    filas = client.query(
        "SELECT sorting_key FROM system.tables "
        "WHERE database = currentDatabase() AND name = %(t)s",
        parameters={"t": tabla},
    ).result_rows
    return str(filas[0][0]) if filas else ""


def necesita(client) -> bool:
    """Si la tabla `spans` tiene todavía la clave de antes."""
    return clave(client) == CLAVE_VIEJA


def _existe(client, tabla: str) -> bool:
    return bool(clave(client, tabla)) or bool(
        client.query(
            "SELECT count() FROM system.tables WHERE database = currentDatabase() "
            "AND name = %(t)s",
            parameters={"t": tabla},
        ).result_rows[0][0]
    )


def _particiones(client, tabla: str) -> list[str]:
    filas = client.query(
        "SELECT DISTINCT partition FROM system.parts WHERE active "
        "AND database = currentDatabase() AND table = %(t)s ORDER BY partition",
        parameters={"t": tabla},
    ).result_rows
    return [str(f[0]) for f in filas]


def _filas(client, tabla: str, particion: str) -> int:
    return int(
        client.query(
            f"SELECT count() FROM {tabla} WHERE toYYYYMM(start_time) = %(p)s",
            parameters={"p": int(particion)},
        ).result_rows[0][0]
    )


def _bytes(client, tabla: str) -> int:
    return int(
        client.query(
            "SELECT sum(bytes_on_disk) FROM system.parts WHERE active "
            "AND database = currentDatabase() AND table = %(t)s",
            parameters={"t": tabla},
        ).result_rows[0][0]
        or 0
    )


def _libre(client) -> int:
    return int(
        client.query("SELECT min(free_space) FROM system.disks").result_rows[0][0] or 0
    )


def plan(client) -> str:
    """Lo que se haría, sin hacer nada."""
    actual = clave(client)
    if actual != CLAVE_VIEJA:
        return f"No hace falta: la tabla spans ya está ordenada por ({actual})."
    ocupa, libre = _bytes(client, "spans"), _libre(client)
    filas = int(client.query("SELECT count() FROM spans").result_rows[0][0])
    lineas = [
        f"La tabla spans tiene {filas:,} filas y ocupa {ocupa / 1e9:.1f} GB.",
        f"Se copiaría a una tabla ordenada por ({CLAVE_NUEVA}), partición a partición.",
        f"Hace falta sitio para una segunda copia: libres {libre / 1e9:.1f} GB.",
        f"La tabla vieja quedaría como {ANTES}, sin borrar.",
    ]
    if libre < ocupa * 1.2:
        lineas.append("NO HAY SITIO SUFICIENTE: libera disco antes de lanzarla.")
    return "\n".join(lineas)


def migrar(client, retention_days: int = 0, *, avisar=print) -> bool:
    """Hace la migración. Devuelve False si no hacía falta."""
    if not necesita(client):
        if _existe(client, ANTES):
            avisar(f"Ya estaba hecha. La tabla vieja sigue en {ANTES}.")
        return False
    ocupa, libre = _bytes(client, "spans"), _libre(client)
    if libre < ocupa * 1.2:
        raise RuntimeError(
            f"no hay sitio para la copia: ocupa {ocupa / 1e9:.1f} GB y hay "
            f"{libre / 1e9:.1f} GB libres"
        )

    inicio = datetime.now(timezone.utc) - MARGEN
    client.command(
        f"CREATE TABLE IF NOT EXISTS {NUEVA} AS spans "
        "ENGINE = ReplacingMergeTree(ingested_at) PARTITION BY toYYYYMM(start_time) "
        f"ORDER BY ({CLAVE_NUEVA}) SETTINGS index_granularity = 8192"
    )
    if clave(client, NUEVA) != CLAVE_NUEVA:
        raise RuntimeError(f"{NUEVA} existe con otra clave: bórrala y vuelve a lanzarla")

    for particion in _particiones(client, "spans"):
        origen = _filas(client, "spans", particion)
        if _filas(client, NUEVA, particion) >= origen:
            avisar(f"  {particion}: ya copiada ({origen:,} filas)")
            continue
        # Una copia a medias de un intento anterior se tira entera: es lo barato.
        client.command(f"ALTER TABLE {NUEVA} DROP PARTITION {particion}")
        dias = client.query(
            "SELECT DISTINCT toDate(start_time) AS d FROM spans "
            "WHERE toYYYYMM(start_time) = %(p)s ORDER BY d",
            parameters={"p": int(particion)},
        ).result_rows
        empezado = time.monotonic()
        for (dia,) in dias:
            client.command(
                f"INSERT INTO {NUEVA} SELECT * FROM spans WHERE toDate(start_time) = %(d)s",
                parameters={"d": dia},
            )
        avisar(
            f"  {particion}: {origen:,} filas en {len(dias)} días "
            f"({time.monotonic() - empezado:.0f} s)"
        )

    # Lo que la ingesta escribió mientras tanto.
    ponerse_al_dia = datetime.now(timezone.utc) - MARGEN
    client.command(
        f"INSERT INTO {NUEVA} SELECT * FROM spans WHERE ingested_at >= %(t)s",
        parameters={"t": inicio},
    )
    client.command(f"EXCHANGE TABLES spans AND {NUEVA}")
    # Y lo que llegó a la vieja entre la puesta al día y el cambio de nombre.
    client.command(
        f"INSERT INTO spans SELECT * FROM {NUEVA} WHERE ingested_at >= %(t)s",
        parameters={"t": ponerse_al_dia},
    )
    client.command(f"RENAME TABLE {NUEVA} TO {ANTES}")
    if retention_days > 0:
        client.command(
            f"ALTER TABLE spans MODIFY TTL toDateTime(start_time) + "
            f"INTERVAL {int(retention_days)} DAY"
        )

    viejas = int(client.query(f"SELECT uniqExact(span_id) FROM {ANTES}").result_rows[0][0])
    nuevas = int(client.query("SELECT uniqExact(span_id) FROM spans").result_rows[0][0])
    avisar(
        f"Hecho: spans ordenada por ({CLAVE_NUEVA}). Spans distintos: {nuevas:,} ahora, "
        f"{viejas:,} en la vieja."
    )
    avisar(
        f"La tabla vieja sigue en {ANTES}. Cuando hayas comprobado que todo cuadra: "
        f"DROP TABLE {ANTES}"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    from ..config import Settings
    from .clickhouse import ClickHouseStore

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hacerlo", action="store_true", help="sin esto, sólo dice qué haría")
    args = parser.parse_args(argv)
    ajustes = Settings()
    store = ClickHouseStore(ajustes)
    if not store.health():
        print("no hay ClickHouse escuchando", file=sys.stderr)
        return 1
    client = store._client
    print(plan(client))
    if not args.hacerlo:
        if necesita(client):
            print("\nPara hacerlo: python -m laplace_backend.storage.migrar_orden --hacerlo")
        return 0
    try:
        migrar(client, ajustes.retention_days)
    except RuntimeError as exc:
        print(f"no se ha migrado: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
