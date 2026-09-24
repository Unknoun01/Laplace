"""Conexiones a Postgres, de un pool por DSN.

Antes cada operación abría su conexión y la cerraba: TCP, TLS si lo hay y autenticación
en cada lectura de metadatos, y al menos dos por cada petición con sesión (la sesión y
los roles). Con tráfico, Postgres pasaba más tiempo aceptando conexiones que contestando.

El pool es por DSN y de todo el proceso: los tres almacenes que hablan con Postgres
—metadatos, cuentas y estado de las alertas— comparten el suyo. Se abre sin esperar a
que la base conteste: una API que arranca antes que su Postgres tiene que arrancar igual
(D-128), y el pool reintenta por su cuenta.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .metadata import MetadataUnavailable

#: Tope de conexiones por proceso. Las peticiones corren en el pool de hilos de Starlette
#: (40 por defecto), pero casi ninguna tiene una conexión abierta mucho rato.
MAX_CONEXIONES = 10
#: Lo que se espera a tener conexión antes de contestar «no hay dónde guardar».
ESPERA_SEGUNDOS = 5.0

_pools: dict[str, Any] = {}
_cerrojo = threading.Lock()


def _pool(dsn: str) -> Any:
    with _cerrojo:
        pool = _pools.get(dsn)
        if pool is None:
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=MAX_CONEXIONES,
                kwargs={"autocommit": True, "connect_timeout": int(ESPERA_SEGUNDOS)},
                # Una conexión que Postgres cerró (un reinicio, un corte) se descarta al
                # sacarla en vez de fallar en la primera consulta de alguien.
                check=ConnectionPool.check_connection,
                open=True,
                name=f"laplace-{len(_pools)}",
            )
            _pools[dsn] = pool
        return pool


@contextmanager
def conexion(dsn: str) -> Iterator[Any]:
    """Una conexión prestada del pool; vuelve a él al salir.

    Se usa igual que `psycopg.connect(...)` en un `with`, que es como la usaban todos los
    almacenes, así que cambiar a pool no tocó ninguna consulta. «No conecto» se dice como
    `MetadataUnavailable`, que las rutas traducen a 503 con su motivo.
    """
    import psycopg
    from psycopg_pool import PoolTimeout

    try:
        with _pool(dsn).connection(timeout=ESPERA_SEGUNDOS) as conn:
            yield conn
    except PoolTimeout as exc:
        raise MetadataUnavailable(f"postgres no responde: {exc}") from exc
    except psycopg.OperationalError as exc:
        raise MetadataUnavailable(f"se ha perdido la conexión con postgres: {exc}") from exc


def cerrar_todos() -> None:
    """Cierra los pools. Al apagar el proceso, y en las pruebas."""
    with _cerrojo:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


@contextmanager
def turno_exclusivo(dsn: str, clave: int) -> Iterator[bool]:
    """`True` para un solo proceso a la vez entre todos los que comparten esta base.

    Es un candado consultivo de Postgres atado a una transacción: se suelta solo al
    salir, y también si el proceso muere a medias, porque muere su conexión. Sirve para
    que un trabajo periódico —las alertas— lo haga uno aunque haya varios workers o
    varias réplicas; los demás ven `False` y se saltan esa vuelta.
    """
    with conexion(dsn) as conn, conn.transaction():
        fila = conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (clave,)).fetchone()
        yield bool(fila and fila[0])
