"""Margen por cliente (Fase 6, D-161).

Lo que se exige:

* `laplace.set_context(customer_id=…)` llega del SDK a la traza guardada;
* el coste de cada cliente es el de sus ejecuciones, igual en los dos almacenes, y lo
  que no dice de quién es se cuenta aparte, nunca repartido;
* el margen compara ingresos al mes con el coste al mes, con la proyección del héroe;
  sin un día de datos no se compara, y con llamadas sin tarifa el margen es un techo;
* el aviso: cuántos clientes te hacen perder dinero, primero en la lista;
* los ingresos se ponen y se quitan por la API.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from helpers import ingest
from laplace import decorators
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import margen
from laplace_backend.config import Settings
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)
VENTANA = Window(since=AHORA - timedelta(days=10), until=AHORA + timedelta(minutes=1), days=10)


def test_el_cliente_viaja_del_sdk_a_la_traza():
    decorators.set_context(customer_id="acme")

    @decorators.observe(type="agent")
    def agente():
        return "ok"

    try:
        agente()
        span = ingest()[0]
        assert span.customer_id == "acme"
    finally:
        decorators._customer_id.set(None)


@pytest.fixture(params=["sqlite", "clickhouse"])
def almacen(request, tmp_path):
    proyecto = f"margen-{uuid.uuid4().hex[:8]}"
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


def _ejecucion(proyecto, cliente, coste, cuando, *, sin_tarifa=False) -> list[Span]:
    """Una ejecución: la raíz con el cliente y una llamada con el coste."""
    traza = uuid.uuid4().hex
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=traza,
        project_id=proyecto,
        name="agente",
        type="agent",
        status="ok",
        start_time=cuando,
        end_time=cuando + timedelta(seconds=1),
        duration_ms=1000.0,
        customer_id=cliente,
    )
    llamada = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=traza,
        parent_span_id=raiz.span_id,
        project_id=proyecto,
        name="chat",
        type="llm",
        status="ok",
        start_time=cuando,
        end_time=cuando + timedelta(milliseconds=500),
        duration_ms=500.0,
        dedup_hash=f"d-{traza}",
    )
    llamada.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=100),
        cost=Cost(total_usd=0.0 if sin_tarifa else coste, input_usd=coste, unknown=sin_tarifa),
    )
    return [raiz, llamada]


def _sembrar(store, proyecto, *, dias=10, sin_tarifa_en=None):
    """Diez días: «acme» gasta 1 $ al día, «beta» 0,1 $ y 0,05 $ sin cliente."""
    spans = []
    for d in range(dias):
        cuando = AHORA - timedelta(days=d, hours=1)
        spans += _ejecucion(proyecto, "acme", 1.0, cuando,
                            sin_tarifa=(sin_tarifa_en == "acme" and d == 0))
        spans += _ejecucion(proyecto, "beta", 0.1, cuando)
        spans += _ejecucion(proyecto, None, 0.05, cuando)
    store.insert_spans(spans)


def test_coste_por_cliente_y_lo_que_no_tiene_cliente_aparte(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    grupos = {g.key: g for g in store.cost_by(proyecto, VENTANA, "customer")}
    assert set(grupos) == {"acme", "beta", ""}
    assert grupos["acme"].traces == 10 and grupos["acme"].cost_usd == pytest.approx(10.0)
    assert grupos["beta"].cost_usd == pytest.approx(1.0)
    assert grupos[""].cost_usd == pytest.approx(0.5)


def test_el_margen_y_el_aviso_de_quien_hace_perder_dinero(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    # acme cuesta unos 30 $ al mes y paga 20; beta cuesta unos 3 y paga 100.
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 20.0, "beta": 100.0})
    assert vista.projected
    assert vista.losing == 1
    primero = vista.customers[0]
    assert primero.customer_id == "acme" and primero.status == "pierde"
    # El mes sale de los días de datos del proyecto, como el héroe.
    dias = vista.observed_days
    assert primero.monthly_cost_usd == pytest.approx(10.0 / dias * 30)
    assert primero.margin_usd == pytest.approx(20.0 - 10.0 / dias * 30)
    assert "pierdes" in primero.headline
    beta = next(c for c in vista.customers if c.customer_id == "beta")
    assert beta.status == "gana" and beta.margin_ratio > 0.9
    assert "1 cliente te hace perder dinero" in vista.headline
    # Lo que no dice de quién es no se reparte: se cuenta aparte.
    assert vista.unassigned_cost_usd == pytest.approx(0.5)
    assert vista.assigned_share == pytest.approx(11.0 / 11.5)


def test_un_margen_escaso_se_llama_ajustado(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    coste_mes = 1.0 / 1 * 30  # beta: 0,1 $ al día → unos 3 $ al mes
    vista = margen.calcular(store, proyecto, VENTANA, {"beta": 3.4})
    beta = next(c for c in vista.customers if c.customer_id == "beta")
    assert beta.status == "ajustado", (beta.margin_ratio, coste_mes)


def test_sin_un_dia_de_datos_no_se_compara_con_el_mes(almacen):
    store, proyecto = almacen
    store.insert_spans(
        _ejecucion(proyecto, "acme", 1.0, AHORA - timedelta(hours=3))
        + _ejecucion(proyecto, "acme", 1.0, AHORA - timedelta(hours=1))
    )
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 1.0})
    (acme,) = vista.customers
    assert not vista.projected
    assert acme.status == "sin-proyeccion"
    assert acme.margin_usd is None and acme.monthly_cost_usd is None
    assert vista.losing == 0, "no se afirma que pierda sin poder compararlo"


def test_sin_tarifa_el_margen_es_un_techo(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto, sin_tarifa_en="acme")
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 20.0, "beta": 100.0})
    acme = next(c for c in vista.customers if c.customer_id == "acme")
    assert acme.cost_is_floor and acme.status == "pierde"
    assert "al menos" in acme.headline
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 1000.0})
    acme = next(c for c in vista.customers if c.customer_id == "acme")
    assert acme.status == "gana" and "como mucho" in acme.headline


def test_un_cliente_con_ingresos_y_sin_trafico_no_desaparece(almacen):
    store, proyecto = almacen
    _sembrar(store, proyecto)
    vista = margen.calcular(store, proyecto, VENTANA, {"gamma": 50.0})
    gamma = next(c for c in vista.customers if c.customer_id == "gamma")
    assert gamma.status == "sin-trafico" and gamma.traces == 0
    assert vista.customers[-1].customer_id == "gamma"


def test_sin_clientes_en_las_trazas_se_dice(almacen):
    store, proyecto = almacen
    store.insert_spans(_ejecucion(proyecto, None, 1.0, AHORA - timedelta(days=2)))
    vista = margen.calcular(store, proyecto, VENTANA, {})
    assert vista.customers == []
    assert "no dicen de qué cliente" in vista.headline


def test_la_prueba_muerde_sin_proyeccion():
    """Si el margen se calculara con el coste de la ventana en vez del mes, un cliente
    que paga 20 al mes y cuesta 10 en diez días saldría ganando."""
    fila = margen._fila("acme", 10, 10.0, 0, margen.Ingreso(20.0), {}, 10.0, 10.0)
    assert fila.status == "pierde"


def test_los_ingresos_se_ponen_y_se_quitan_por_la_api(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans = []
    for d in range(3):
        spans += _ejecucion("local", "acme", 1.0, AHORA - timedelta(days=d, hours=1))
    store.insert_spans(spans)
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_HOME", str(tmp_path))

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        puesto = client.put(
            "/api/customers/revenue",
            json={"project_id": "local", "customer_id": "acme", "monthly": 10},
        )
        assert puesto.status_code == 200, puesto.text
        vista = client.get("/api/customers", params={"project_id": "local", "days": 7}).json()
        (acme,) = vista["customers"]
        assert acme["monthly_revenue"] == 10 and acme["status"] == "pierde"
        assert vista["losing"] == 1

        client.put(
            "/api/customers/revenue",
            json={"project_id": "local", "customer_id": "acme", "monthly": None},
        )
        vista = client.get("/api/customers", params={"project_id": "local", "days": 7}).json()
        assert vista["customers"][0]["status"] == "sin-ingresos"
        assert vista["losing"] == 0

        vacio = client.put(
            "/api/customers/revenue",
            json={"project_id": "local", "customer_id": "  ", "monthly": 5},
        )
        assert vacio.status_code == 422
    config.get_settings.cache_clear()


def test_una_tirada_de_evaluacion_no_se_cobra_a_ningun_cliente(almacen):
    """Aunque el agente fije un cliente dentro de la tirada —o lo herede del último
    `set_context`, como pasaba en la demo—, el margen no cuenta las evaluaciones."""
    from laplace.semconv import EVAL_TAG

    store, proyecto = almacen
    _sembrar(store, proyecto)
    tirada = _ejecucion(proyecto, "acme", 50.0, AHORA - timedelta(hours=2))
    tirada[0].tags = [EVAL_TAG]
    store.insert_spans(tirada)
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 20.0})
    acme = next(c for c in vista.customers if c.customer_id == "acme")
    assert acme.traces == 10 and acme.window_cost_usd == pytest.approx(10.0)
    assert vista.unassigned_cost_usd == pytest.approx(0.5)


def _repetida(proyecto, cliente, cuando, veces=3) -> list[Span]:
    """Una ejecución de `cliente` que llama tres veces al mismo paso con lo mismo."""
    spans = _ejecucion(proyecto, cliente, 0.01, cuando)
    llamada = spans[1]
    llamada.step_key = "paso-buscar"
    llamada.dedup_hash = f"rep-{llamada.trace_id}"
    for i in range(1, veces):
        copia = llamada.model_copy(deep=True)
        copia.span_id = uuid.uuid4().hex[:16]
        copia.start_time = cuando + timedelta(milliseconds=10 * i)
        spans.append(copia)
    return spans


def test_los_pasos_de_cada_cliente_en_los_dos_almacenes(almacen):
    store, proyecto = almacen
    spans = []
    for d in range(4):
        spans += _repetida(proyecto, "acme", AHORA - timedelta(days=d, hours=1))
        spans += _ejecucion(proyecto, "beta", 0.01, AHORA - timedelta(days=d, hours=2))
    store.insert_spans(spans)
    pasos = store.customer_steps(proyecto, VENTANA)
    assert pasos["acme"]["paso-buscar"] == 3
    assert pasos["acme"]["agente"] == 1
    assert "paso-buscar" not in pasos["beta"]
    assert "" not in pasos


def test_cada_cliente_lleva_los_problemas_de_sus_ejecuciones(almacen):
    from laplace_backend import insights

    store, proyecto = almacen
    spans = []
    for d in range(4):
        spans += _repetida(proyecto, "acme", AHORA - timedelta(days=d, hours=1))
        # beta pasa por el mismo paso una sola vez: no lo repite.
        una = _ejecucion(proyecto, "beta", 0.01, AHORA - timedelta(days=d, hours=2))
        una[1].step_key = "paso-buscar"
        spans += una
    store.insert_spans(spans)
    hallazgos = insights.detect(store, proyecto, VENTANA)
    assert any(f.kind == "repeticion" for f in hallazgos)
    vista = margen.calcular(store, proyecto, VENTANA, {})
    margen.con_problemas(vista, hallazgos, store.customer_steps(proyecto, VENTANA))
    por_cliente = {c.customer_id: [f.id for f in c.findings] for c in vista.customers}
    assert "repeticion:paso-buscar" in por_cliente["acme"]
    assert "repeticion:paso-buscar" not in por_cliente["beta"], "pasar una vez no es repetir"


@pytest.mark.parametrize("tipo", ["salida_truncada", "json_roto"])
def test_rehacer_una_respuesta_tampoco_es_de_quien_pasa_una_vez(almacen, tipo):
    """Rehacer una respuesta cortada (D-193) o rota (D-194) es pasar dos veces por el
    paso."""
    from laplace_backend.insights import Finding

    store, proyecto = almacen
    spans = []
    for d in range(4):
        spans += _repetida(proyecto, "acme", AHORA - timedelta(days=d, hours=1), veces=2)
        una = _ejecucion(proyecto, "beta", 0.01, AHORA - timedelta(days=d, hours=2))
        una[1].step_key = "paso-buscar"
        spans += una
    store.insert_spans(spans)
    cortada = Finding(
        id=f"{tipo}:paso-buscar:m", kind=tipo, title="t", summary="s",
        step_key="paso-buscar", window_waste_usd=0.01,
    )
    vista = margen.calcular(store, proyecto, VENTANA, {})
    margen.con_problemas(vista, [cortada], store.customer_steps(proyecto, VENTANA))
    por_cliente = {c.customer_id: [f.id for f in c.findings] for c in vista.customers}
    assert cortada.id in por_cliente["acme"]
    assert cortada.id not in por_cliente["beta"], "pasar una vez no es rehacer"


class _Notificador:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    def send(self, url: str, texto: str) -> bool:
        self.enviados.append(texto)
        return True


def _alertas(tmp_path, ingresos: dict[str, float]):
    from laplace_backend.alerts import AlertConfig, AlertRunner, MemoryAlertState
    from laplace_backend.storage.metadata import SQLiteMetadataStore

    db = tmp_path / "alertas.db"
    store = SQLiteStore(db)
    store.migrate()
    spans = []
    for d in range(5):
        spans += _ejecucion("p", "acme", 1.0, AHORA - timedelta(days=d, hours=1))
    store.insert_spans(spans)
    meta = SQLiteMetadataStore(db)
    meta.migrate()
    meta.set_setting("p", "alerts", {"webhook_url": "https://hooks.slack.com/services/T/B/x"})
    for cliente, importe in ingresos.items():
        meta.set_setting("p", margen.clave(cliente), {"monthly": importe})
    notificador = _Notificador()
    estado = MemoryAlertState()
    runner = AlertRunner(
        store, AlertConfig(Settings(store="sqlite", sqlite_path=str(db))), estado, notificador,
        metadata=meta,
    )
    return runner, notificador, meta, estado


def test_la_alerta_avisa_una_vez_cuando_un_cliente_pasa_a_perder(tmp_path):
    # acme cuesta unos 30 $ al mes y paga 5.
    runner, notificador, meta, estado = _alertas(tmp_path, {"acme": 5.0})
    primera = runner.evaluate("p")
    assert "acme" in primera.customers_notice
    assert any("ha pasado a hacerte perder dinero" in m for m in notificador.enviados)
    enviados = len(notificador.enviados)

    # Sigue perdiendo: no vuelve a sonar, ni siquiera pasado el periodo de calma.
    registro = estado.read("p")["customer:acme"]
    registro.seen_at -= timedelta(days=3)
    registro.notified_at -= timedelta(days=3)
    estado.save("p", registro)
    assert runner.evaluate("p").customers_notice == ""
    assert len(notificador.enviados) == enviados
    assert "customer:acme" in estado.read("p")

    # Se recupera (le sube el precio): se olvida. Si recae, vuelve a sonar.
    meta.set_setting("p", margen.clave("acme"), {"monthly": 500.0})
    runner.evaluate("p")
    assert "customer:acme" not in estado.read("p")
    meta.set_setting("p", margen.clave("acme"), {"monthly": 5.0})
    assert "acme" in runner.evaluate("p").customers_notice


def test_la_alerta_de_clientes_se_puede_silenciar_y_sin_ingresos_calla(tmp_path):
    runner, notificador, meta, _ = _alertas(tmp_path, {})
    assert runner.evaluate("p").customers_notice == ""
    meta.set_setting("p", margen.clave("acme"), {"monthly": 5.0})
    meta.set_setting(
        "p", "alerts",
        {
            "webhook_url": "https://hooks.slack.com/services/T/B/x",
            "muted_kinds": ["cliente_pierde"],
        },
    )
    assert runner.evaluate("p").customers_notice == ""
