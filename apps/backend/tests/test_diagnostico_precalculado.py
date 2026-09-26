"""El Diagnóstico, recalculado antes de que caduque para quien lo está mirando (D-143).

Con diez millones de spans al día tarda unos 4 s. La caché de un minuto lo sirve al
instante… salvo la primera vez de cada minuto, que es justo la que se nota. Un proceso de
fondo lo recalcula cuando lleva tres cuartos de su vida en la caché, pero sólo si alguien
lo ha mirado en la última media hora: lo que nadie abre no gasta ClickHouse.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from laplace_backend.cache_diagnostico import CacheDiagnostico


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora


def _cache():
    reloj = Reloj()
    return CacheDiagnostico(reloj=reloj), reloj


def test_lo_que_alguien_mira_se_renueva_antes_de_caducar():
    cache, reloj = _cache()
    calculos = []

    def recalcular():
        calculos.append(reloj.ahora)
        return f"diagnóstico {len(calculos)}"

    cache.guardar("k", "diagnóstico 0", 60, recalcular)
    assert cache.leer("k", 60) == "diagnóstico 0"
    reloj.ahora += 30
    assert cache.renovar_pendientes(60) == 0, "a mitad de vida todavía no toca"
    reloj.ahora += 16  # 46 s: más de tres cuartos
    assert cache.renovar_pendientes(60) == 1
    reloj.ahora += 30  # 76 s desde el primero, 30 desde la renovación
    assert cache.leer("k", 60) == "diagnóstico 1", "se sirve el renovado, no se recalcula"


def test_lo_que_nadie_mira_no_se_renueva():
    cache, reloj = _cache()
    cache.guardar("k", "viejo", 60, lambda: "nuevo")
    reloj.ahora += 31 * 60  # nadie lo ha leído en media hora
    assert cache.renovar_pendientes(60) == 0
    assert cache.leer("k", 60) is None, "y caducó: la siguiente lectura lo calcula"


def test_leer_cuenta_como_mirar():
    cache, reloj = _cache()
    cache.guardar("k", "viejo", 60, lambda: "nuevo")
    for _ in range(40):  # alguien con la pestaña abierta durante 40 minutos
        reloj.ahora += 50
        cache.leer("k", 60)
        cache.renovar_pendientes(60)
    assert cache.leer("k", 60) == "nuevo"


def test_un_fallo_al_renovar_no_rompe_nada_y_deja_el_valor_hasta_que_caduca():
    cache, reloj = _cache()

    def roto():
        raise RuntimeError("clickhouse caído")

    cache.guardar("k", "bueno", 60, roto)
    reloj.ahora += 50
    assert cache.renovar_pendientes(60) == 0
    assert cache.leer("k", 60) == "bueno"
    reloj.ahora += 20
    assert cache.leer("k", 60) is None, "caducado, no se sirve para siempre"


def test_olvidar_quita_tambien_lo_que_habia_que_renovar():
    """Tras marcar un hallazgo, renovar con los estados de antes devolvería a la caché un
    Diagnóstico que ya no es verdad."""
    cache, reloj = _cache()
    cache.guardar("k", "viejo", 60, lambda: "con estados viejos")
    cache.olvidar()
    reloj.ahora += 50
    assert cache.renovar_pendientes(60) == 0
    assert cache.leer("k", 60) is None


def test_un_cambio_mientras_se_recalcula_no_deja_volver_lo_viejo():
    """Se renueva con los estados de antes; si a mitad alguien marca un hallazgo, lo
    recalculado ya no es verdad y no puede volver a la caché."""
    cache, reloj = _cache()

    def recalcular_y_mientras_tanto_cambian_algo():
        cache.olvidar()
        return "con estados viejos"

    cache.guardar("k", "viejo", 60, recalcular_y_mientras_tanto_cambian_algo)
    reloj.ahora += 50
    assert cache.renovar_pendientes(60) == 0
    assert cache.leer("k", 60) is None


# ---------------------------------------------------------------------------------
# El proceso de fondo, en la aplicación
# ---------------------------------------------------------------------------------


def _app(tmp_path, monkeypatch, cache: str | None):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    if cache is not None:
        monkeypatch.setenv("LAPLACE_OVERVIEW_CACHE_S", cache)
    from laplace_backend import api, config, main

    config.get_settings.cache_clear()
    api.CACHE_DIAGNOSTICO.olvidar()
    importlib.reload(main)
    return main


@pytest.fixture(autouse=True)
def _ajustes_limpios():
    yield
    from laplace_backend import config

    config.get_settings.cache_clear()


def test_con_cache_arranca_el_renovador(tmp_path, monkeypatch):
    main = _app(tmp_path, monkeypatch, "60")
    with TestClient(main.app):
        tarea = main.app.state.renovador_diagnostico
        assert tarea is not None and not tarea.done()


def test_sin_cache_no_hay_renovador(tmp_path, monkeypatch):
    main = _app(tmp_path, monkeypatch, None)
    with TestClient(main.app):
        assert main.app.state.renovador_diagnostico is None


def test_el_renovador_recalcula_lo_que_se_ha_pedido(tmp_path, monkeypatch):
    """De punta a punta: una petición deja su forma de recalcularse, y una vuelta del
    renovador la usa."""
    main = _app(tmp_path, monkeypatch, "60")
    from laplace_backend import api, cache_diagnostico

    with TestClient(main.app) as cliente:
        proyecto = "precalculo"
        r = cliente.get("/api/overview", params={"project_id": proyecto})
        assert r.status_code == 200
        llamadas = []
        original = api.overview

        def contando(*args, **kwargs):
            llamadas.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(api, "overview", contando)
        # Sin esperar un minuto: la vuelta se lanza a mano con la entrada ya vieja.
        api.CACHE_DIAGNOSTICO._envejecer(50)
        hechas = asyncio.run(cache_diagnostico.una_vuelta(api.CACHE_DIAGNOSTICO, 60))
        assert hechas == 1
        assert llamadas == [1], "se recalculó con la función de la API, una vez"
