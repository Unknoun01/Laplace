"""Plano de control: tope y parada puestos en Laplace, aplicados por el SDK.

Lo que se pone en Ajustes —tope de gasto por ejecución, de bucles y la parada— lo pide
el SDK cada 30 s en segundo plano y lo aplica con la maquinaria de `laplace.guard`
(D-184). Se suma a lo del código y manda el más estricto. Si Laplace no responde se
sigue con la última copia, y sin copia, sin reglas: un agente nunca se cae porque
Laplace esté caído.

Como en `test_guard.py`, las llamadas al modelo van por el cliente real de OpenAI con
un transporte falso: «cortar» quiere decir que la petición no sale.
"""

from __future__ import annotations

import importlib
import threading
import time

import pytest
from fastapi.testclient import TestClient
from laplace import GuardExceeded, _control, guard, observe

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
from laplace.integrations import openai as oi  # noqa: E402
from test_guard import _coste_de_una, _preguntar, _Proveedor  # noqa: E402


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    _control.reiniciar()
    yield
    _control.reiniciar()
    oi.uninstrument()


def _laplace_dice(monkeypatch, **reglas) -> None:
    """Laplace responde con estas reglas y el SDK las pide una vez."""
    cuerpo = {"max_usd_per_run": None, "max_loop": None, "stopped": False, **reglas}
    monkeypatch.setattr(_control, "request", lambda url, **kw: cuerpo)
    monkeypatch.setattr(_control, "endpoint", lambda *_: "http://laplace")
    assert _control.refrescar(project="p") is True


def _laplace_caido(monkeypatch) -> None:
    def _falla(url, **kw):
        raise _control.LaplaceHTTPError("no se puede hablar con Laplace")

    monkeypatch.setattr(_control, "request", _falla)
    monkeypatch.setattr(_control, "endpoint", lambda *_: "http://laplace")


# ---------------------------------------------------------------------------------
# Manda el más estricto
# ---------------------------------------------------------------------------------


def test_el_tope_de_laplace_aprieta_un_guard_mas_holgado(monkeypatch):
    una = _coste_de_una()
    _laplace_dice(monkeypatch, max_usd_per_run=una * 1.5)
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=100):
        _preguntar(cliente, "uno")
        _preguntar(cliente, "dos")
        with pytest.raises(GuardExceeded) as corte:
            _preguntar(cliente, "tres")
    assert proveedor.peticiones == 2
    assert corte.value.reason == "max_usd_per_run"
    assert corte.value.limit == pytest.approx(una * 1.5)
    assert corte.value.source == "laplace"


def test_si_el_codigo_es_mas_estricto_manda_el_codigo(monkeypatch):
    una = _coste_de_una()
    _laplace_dice(monkeypatch, max_usd_per_run=100)
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=una * 0.5):
        _preguntar(cliente, "uno")
        with pytest.raises(GuardExceeded) as corte:
            _preguntar(cliente, "dos")
    assert proveedor.peticiones == 1
    assert corte.value.source == "code"
    assert corte.value.limit == pytest.approx(una * 0.5)


def test_un_limite_de_cada_sitio_se_suman(monkeypatch):
    """Gasto en el código y bucles en Laplace: aplican los dos."""
    _laplace_dice(monkeypatch, max_loop=3)
    vueltas = []

    @observe(name="buscar")
    def buscar(q: str) -> str:
        vueltas.append(q)
        return "nada"

    with guard(max_usd_per_run=100), pytest.raises(GuardExceeded) as corte:
        for _ in range(10):
            buscar("lo mismo")
    assert len(vueltas) == 3
    assert corte.value.reason == "max_loop"
    assert corte.value.source == "laplace"


def test_sin_guard_en_el_codigo_la_ejecucion_es_la_raiz_de_observe(monkeypatch):
    """Quien no ha escrito `guard` también tiene tope: el de Laplace, por ejecución."""
    una = _coste_de_una()
    _laplace_dice(monkeypatch, max_usd_per_run=una * 1.5)
    proveedor = _Proveedor()
    cliente = proveedor.cliente()

    @observe(name="atender")
    def atender(n: int) -> None:
        for i in range(n):
            _preguntar(cliente, f"pregunta {i}")

    with pytest.raises(GuardExceeded) as corte:
        atender(5)
    assert proveedor.peticiones == 2
    assert corte.value.source == "laplace"

    # Cada ejecución es nueva: la siguiente vuelve a tener su presupuesto entero.
    with pytest.raises(GuardExceeded):
        atender(5)
    assert proveedor.peticiones == 4


def test_un_tope_puesto_a_mitad_de_ejecucion_se_aplica_en_la_siguiente_comprobacion(
    monkeypatch,
):
    una = _coste_de_una()
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=100):
        _preguntar(cliente, "uno")
        _preguntar(cliente, "dos")
        _laplace_dice(monkeypatch, max_usd_per_run=una)
        with pytest.raises(GuardExceeded):
            _preguntar(cliente, "tres")
    assert proveedor.peticiones == 2


def test_sin_reglas_no_se_corta_nada(monkeypatch):
    _laplace_dice(monkeypatch)
    proveedor = _Proveedor()
    cliente = proveedor.cliente()

    @observe(name="atender")
    def atender() -> None:
        for i in range(5):
            _preguntar(cliente, f"pregunta {i}")

    atender()
    assert proveedor.peticiones == 5


# ---------------------------------------------------------------------------------
# La parada
# ---------------------------------------------------------------------------------


def test_parado_no_sale_ninguna_llamada_ni_fuera_de_un_paso(monkeypatch):
    _laplace_dice(monkeypatch, stopped=True)
    proveedor = _Proveedor()
    with pytest.raises(GuardExceeded) as corte:
        _preguntar(proveedor.cliente())
    assert proveedor.peticiones == 0
    assert corte.value.reason == "stopped"
    assert corte.value.source == "laplace"


def test_parado_tampoco_corre_un_paso(monkeypatch):
    _laplace_dice(monkeypatch, stopped=True)
    corrio = []

    @observe(name="enviar_correo", type="tool")
    def enviar_correo() -> None:
        corrio.append(1)

    with pytest.raises(GuardExceeded):
        enviar_correo()
    assert corrio == []


def test_la_ejecucion_parada_sigue_parada_aunque_se_reanude(monkeypatch):
    """Parar corta lo que está en marcha. Reanudar deja empezar ejecuciones nuevas,
    pero no resucita la que se paró a medias."""
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_loop=5):
        _preguntar(cliente, "uno")
        _laplace_dice(monkeypatch, stopped=True)
        with pytest.raises(GuardExceeded):
            _preguntar(cliente, "dos")
        _laplace_dice(monkeypatch, stopped=False)
        with pytest.raises(GuardExceeded):
            _preguntar(cliente, "tres")
    assert proveedor.peticiones == 1
    _preguntar(cliente, "cuatro")
    assert proveedor.peticiones == 2


# ---------------------------------------------------------------------------------
# Laplace caído
# ---------------------------------------------------------------------------------


def test_si_laplace_no_responde_se_sigue_con_la_copia(monkeypatch):
    _laplace_dice(monkeypatch, stopped=True)
    _laplace_caido(monkeypatch)
    assert _control.refrescar(project="p") is False
    proveedor = _Proveedor()
    with pytest.raises(GuardExceeded):
        _preguntar(proveedor.cliente())
    assert proveedor.peticiones == 0


def test_sin_copia_y_sin_laplace_el_agente_sigue_sin_reglas(monkeypatch):
    _laplace_caido(monkeypatch)
    assert _control.refrescar(project="p") is False
    proveedor = _Proveedor()
    _preguntar(proveedor.cliente())
    assert proveedor.peticiones == 1


def test_una_respuesta_rara_no_borra_la_copia_ni_tumba_nada(monkeypatch):
    una = _coste_de_una()
    _laplace_dice(monkeypatch, max_usd_per_run=una)
    for raro in ({"max_usd_per_run": "mucho"}, {"max_loop": 1}, ["no", "es", "esto"], None):
        monkeypatch.setattr(_control, "request", lambda url, raro=raro, **kw: raro)
        assert _control.refrescar(project="p") is False
    assert _control.actuales().max_usd_per_run == pytest.approx(una)


def test_la_copia_es_del_proyecto_y_no_pasa_a_otro(monkeypatch):
    _laplace_dice(monkeypatch, stopped=True)
    _laplace_caido(monkeypatch)
    _control.refrescar(project="otro")
    assert _control.actuales().stopped is False


def test_se_pregunta_cada_treinta_segundos_en_segundo_plano(monkeypatch):
    assert _control.INTERVALO_SEGUNDOS == 30
    llegaron = threading.Event()
    lento = threading.Event()

    def _pide(url, **kw):
        assert "/api/control?project_id=p" in url
        assert kw["timeout"] <= 5
        llegaron.set()
        lento.wait(2)  # Laplace tarda: quien arranca no espera por ello.
        return {"max_usd_per_run": None, "max_loop": 4, "stopped": False}

    monkeypatch.setattr(_control, "request", _pide)
    monkeypatch.setattr(_control, "endpoint", lambda *_: "http://laplace")
    inicio = time.monotonic()
    _control.arrancar(project="p")
    assert time.monotonic() - inicio < 0.5
    assert llegaron.wait(2)
    lento.set()
    for _ in range(50):
        if _control.actuales().max_loop == 4:
            break
        time.sleep(0.02)
    assert _control.actuales().max_loop == 4
    hilo = _control._hilo
    assert hilo is not None and hilo.daemon


def test_init_arranca_la_consulta_y_se_puede_apagar(monkeypatch):
    from laplace import _tracer
    from opentelemetry.sdk.trace import SpanProcessor

    arrancado = []
    monkeypatch.setattr(_control, "arrancar", lambda **kw: arrancado.append(kw))
    monkeypatch.setattr(_tracer, "_build_processor", lambda cfg, batch: SpanProcessor())
    monkeypatch.setattr(_tracer, "_auto_instrument", lambda cfg: None)
    monkeypatch.setattr(_tracer, "_config", _tracer._config)
    monkeypatch.setattr(_tracer, "_provider", None)
    import laplace

    laplace.init(project="p", endpoint="http://laplace")
    assert arrancado, "init no ha arrancado la consulta de reglas"
    arrancado.clear()
    laplace.init(project="p", endpoint="http://laplace", remote_rules=False)
    assert not arrancado
    monkeypatch.setenv("LAPLACE_REMOTE_RULES", "false")
    laplace.init(project="p", endpoint="http://laplace")
    assert not arrancado
    _tracer._shutdown_provider()


# ---------------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------------


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_sin_nada_puesto_no_hay_reglas(local):
    r = local.get("/api/control", params={"project_id": "p"})
    assert r.status_code == 200, r.text
    leido = r.json()
    assert (leido["max_usd_per_run"], leido["max_loop"], leido["stopped"]) == (None, None, False)


def test_poner_tope_y_parar_desde_la_interfaz(local):
    puesto = local.put(
        "/api/control",
        json={"project_id": "p", "max_usd_per_run": 0.5, "max_loop": 4, "stopped": False},
    )
    assert puesto.status_code == 200, puesto.text
    leido = local.get("/api/control", params={"project_id": "p"}).json()
    assert (leido["max_usd_per_run"], leido["max_loop"], leido["stopped"]) == (0.5, 4, False)

    parado = local.put("/api/control", json={**leido, "stopped": True}).json()
    assert parado["stopped"] is True
    assert parado["stopped_at"]
    # Otro proyecto no se entera.
    assert local.get("/api/control", params={"project_id": "q"}).json()["stopped"] is False

    quitado = local.put(
        "/api/control",
        json={"project_id": "p", "max_usd_per_run": None, "max_loop": None, "stopped": False},
    ).json()
    assert (quitado["max_usd_per_run"], quitado["max_loop"], quitado["stopped"]) == (
        None,
        None,
        False,
    )


@pytest.mark.parametrize(
    "malo", [{"max_usd_per_run": 0}, {"max_usd_per_run": -1}, {"max_loop": 1}]
)
def test_los_mismos_limites_que_guard(local, malo):
    """Lo que `guard()` no acepta tampoco se puede poner desde la interfaz."""
    r = local.put("/api/control", json={"project_id": "p", **malo})
    assert r.status_code == 422


def test_cambiar_las_reglas_es_cosa_de_admin():
    from laplace_backend.auth import ADMIN_WRITES

    assert ("PUT", "/api/control") in ADMIN_WRITES


def test_el_sdk_entiende_lo_que_dice_la_api(local, monkeypatch):
    """El contrato de punta a punta: lo que se pone en la API es lo que aplica el SDK."""
    local.put(
        "/api/control",
        json={"project_id": "p", "max_usd_per_run": 0.25, "max_loop": 3, "stopped": True},
    )

    def _por_la_api(url, **kw):
        ruta = url.removeprefix("http://laplace")
        respuesta = local.get(ruta)
        assert respuesta.status_code == 200
        return respuesta.json()

    monkeypatch.setattr(_control, "request", _por_la_api)
    monkeypatch.setattr(_control, "endpoint", lambda *_: "http://laplace")
    assert _control.refrescar(project="p") is True
    reglas = _control.actuales()
    assert (reglas.max_usd_per_run, reglas.max_loop, reglas.stopped) == (0.25, 3, True)
