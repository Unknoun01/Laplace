"""Lo que el usuario decide sobre su proyecto: estados, presupuesto, reparto, tarifas y
borrado (D-123).

Casi todo aquí comprueba que **no se diga de más**: que un arreglo no se dé por bueno
sin ejecuciones después, que un problema que sigue saliendo vuelva a la lista aunque el
usuario lo marcara, que una tarifa nueva alcance lo ya guardado y no sólo lo que llegue,
y que borrar un proyecto no deje nada suyo detrás.

Corre sin red ni almacenes de nube: SQLite en un directorio temporal.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laplace_backend import insights, presupuesto, seguimiento
from laplace_backend.storage.base import Window
from laplace_backend.storage.metadata import SQLiteMetadataStore
from laplace_backend.storage.sqlite import SQLiteStore

sys.path.insert(0, str(Path(__file__).parent))
from test_sqlite_store import _agente  # noqa: E402

AHORA = datetime.now(timezone.utc)


def _en(spans, cuando: datetime, *, usuario: str = "", sano: bool = False):
    """Los spans de `_agente`, movidos a `cuando` y, si `sano`, sin las repeticiones.

    `_agente` repite tres veces el paso «paso-json» en cada traza: es la repetición que
    sale como hallazgo. Quitar dos de las tres es exactamente «haberlo arreglado».
    """
    inicio = min(s.start_time for s in spans)
    vistos: set[str] = set()
    salida = []
    for s in spans:
        if sano and s.step_key == "paso-json":
            if s.trace_id in vistos:
                continue
            vistos.add(s.trace_id)
        s.start_time = cuando + (s.start_time - inicio) / 100
        s.end_time = s.start_time + timedelta(milliseconds=s.duration_ms)
        if usuario:
            s.user_id = usuario
        salida.append(s)
    return salida


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(tmp_path / "laplace.db")
    s.migrate()
    return s


def _ventana(dias: float = 30) -> Window:
    return Window(since=AHORA - timedelta(days=dias), until=AHORA + timedelta(minutes=1), days=30)


def _repeticion(store, project):
    hallazgos = insights.detect(store, project, _ventana())
    rep = [f for f in hallazgos if f.kind == "repeticion"]
    assert rep, f"el tráfico de prueba tiene que producir una repetición: {hallazgos}"
    return rep[0]


# ---------------------------------------------------------------------------------
# Estado de los hallazgos
# ---------------------------------------------------------------------------------


def test_un_hallazgo_ignorado_sale_de_la_lista_y_del_evitable(store):
    store.insert_spans(_en(_agente("p"), AHORA - timedelta(days=2)))
    rep = _repeticion(store, "p")
    antes = insights.overview(store, "p", _ventana())

    estados = {rep.id: {"status": "ignorado", "at": AHORA.isoformat()}}
    despues = insights.overview(store, "p", _ventana(), states=estados)

    assert rep.id not in {f.id for f in despues.findings}
    assert rep.id in {f.id for f in despues.set_aside}
    assert despues.window_avoidable_usd < antes.window_avoidable_usd


def test_un_arreglo_no_se_da_por_bueno_sin_ejecuciones_despues(store):
    """Marcarlo no es arreglarlo. Sin tráfico después, lo único honesto es esperar."""
    store.insert_spans(_en(_agente("p"), AHORA - timedelta(days=2)))
    rep = _repeticion(store, "p")
    check = seguimiento.comprobar(store, "p", rep, AHORA - timedelta(hours=1), AHORA)
    assert check.verdict == "pendiente"
    assert check.saved is None


def test_un_arreglo_que_funciona_dice_cuanto_ya_no_se_ha_gastado(store):
    marcado = AHORA - timedelta(days=1)
    store.insert_spans(_en(_agente("p"), marcado - timedelta(days=2)))
    store.insert_spans(_en(_agente("p"), marcado + timedelta(hours=2), sano=True))
    rep = _repeticion(store, "p")

    check = seguimiento.comprobar(store, "p", rep, marcado, AHORA)
    assert check.verdict == "arreglado", check.headline
    assert check.after_per_run == 0
    assert check.saved and check.saved > 0
    assert "no ha vuelto a aparecer" in check.headline


def test_un_arreglado_que_sigue_saliendo_vuelve_a_la_lista(store):
    """Fiarse de lo que dice el usuario sin mirar sería esconder justo lo que importa."""
    marcado = AHORA - timedelta(days=1)
    store.insert_spans(_en(_agente("p"), marcado - timedelta(days=2)))
    store.insert_spans(_en(_agente("p"), marcado + timedelta(hours=2)))
    rep = _repeticion(store, "p")

    estados = {rep.id: {"status": "arreglado", "at": marcado.isoformat()}}
    vista = insights.overview(store, "p", _ventana(), states=estados)
    vuelto = next((f for f in vista.findings if f.id == rep.id), None)
    assert vuelto is not None, "sigue saliendo igual: tiene que seguir en la lista"
    assert vuelto.state == "reaparecido"
    assert vuelto.fix_check and vuelto.fix_check.verdict == "sigue"


def test_el_heroe_suma_lo_ya_ahorrado_y_solo_lo_verificado(store):
    """El paso «verificar» del ciclo (D-156): lo que se enseña como ahorrado es la
    suma de `saved` de los arreglos que el seguimiento ha dado por buenos."""
    marcado = AHORA - timedelta(days=1)
    store.insert_spans(_en(_agente("p"), marcado - timedelta(days=2)))
    store.insert_spans(_en(_agente("p"), marcado + timedelta(hours=2), sano=True))
    rep = _repeticion(store, "p")
    estados = {rep.id: {"status": "arreglado", "at": marcado.isoformat()}}
    vista = insights.overview(store, "p", _ventana(), states=estados)
    apartado = next(f for f in vista.set_aside if f.id == rep.id)
    assert vista.saved_findings == 1
    assert vista.saved_usd == pytest.approx(apartado.fix_check.saved)
    assert vista.saved_usd > 0

    # Sin marcar nada, nada ahorrado.
    assert insights.overview(store, "p", _ventana()).saved_usd == 0


def test_lo_que_reaparece_o_espera_no_cuenta_como_ahorrado(store):
    marcado = AHORA - timedelta(days=1)
    store.insert_spans(_en(_agente("p"), marcado - timedelta(days=2)))
    store.insert_spans(_en(_agente("p"), marcado + timedelta(hours=2)))
    rep = _repeticion(store, "p")
    # Sigue saliendo igual: reaparecido, sin ahorro.
    estados = {rep.id: {"status": "arreglado", "at": marcado.isoformat()}}
    assert insights.overview(store, "p", _ventana(), states=estados).saved_usd == 0
    # Marcado hace un minuto: pendiente, sin ahorro.
    estados = {rep.id: {"status": "arreglado", "at": AHORA.isoformat()}}
    vista = insights.overview(store, "p", _ventana(), states=estados)
    assert vista.saved_usd == 0 and vista.saved_findings == 0


# ---------------------------------------------------------------------------------
# Presupuesto
# ---------------------------------------------------------------------------------


def test_sin_un_dia_de_datos_el_presupuesto_no_proyecta_el_mes(store):
    ahora = AHORA.replace(day=15) if AHORA.day < 15 else AHORA
    store.insert_spans(_en(_agente("p"), ahora - timedelta(hours=1)))
    b = presupuesto.calcular(store, "p", 100.0, ahora)
    assert b.month_to_date_usd > 0
    assert b.projected_usd is None
    assert b.status == "bien"
    assert "Todavía no proyectamos" in b.headline


def test_pasar_el_presupuesto_se_dice_y_avisa_una_vez_por_umbral(store):
    ahora = AHORA.replace(day=20) if AHORA.day < 20 else AHORA
    store.insert_spans(_en(_agente("p"), ahora - timedelta(days=3)))
    gastado = presupuesto.calcular(store, "p", None, ahora).month_to_date_usd
    b = presupuesto.calcular(store, "p", gastado / 2, ahora)
    assert b.status == "pasado"
    assert presupuesto.aviso_pendiente(b) == 100


# ---------------------------------------------------------------------------------
# Reparto por usuario
# ---------------------------------------------------------------------------------


def test_el_reparto_por_usuario_suma_el_total_con_los_anonimos_aparte(store):
    store.insert_spans(_en(_agente("p"), AHORA - timedelta(days=1), usuario="ana"))
    store.insert_spans(_en(_agente("p"), AHORA - timedelta(hours=5)))
    grupos = store.cost_by("p", _ventana(), "user", 10)
    total = store.summarize_window("p", _ventana()).total_cost_usd

    assert {g.key for g in grupos} == {"ana", ""}
    assert sum(g.cost_usd for g in grupos) == pytest.approx(total)
    assert next(g for g in grupos if g.key == "ana").traces == 6


# ---------------------------------------------------------------------------------
# La API: tarifas propias, borrado y alertas
# ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main, pricing

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main.app
    pricing.set_custom_prices({})
    config.get_settings.cache_clear()


def test_una_tarifa_nueva_alcanza_lo_ya_guardado(client):
    """El coste se calcula en la ingesta. Sin recálculo, poner un precio sólo servía para
    lo que llegara después y el histórico seguía diciendo «no lo sabemos»."""
    c, app = client
    spans = _en(_agente("p"), AHORA - timedelta(hours=3))
    for s in spans:
        if s.llm:
            s.llm.request_model = s.llm.response_model = "modelo-casero"
            s.llm.cost = s.llm.cost.model_copy(update={"total_usd": 0.0, "unknown": True})
    app.state.store.insert_spans(spans)
    antes = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()
    assert antes["unknown_cost_spans"] > 0

    r = c.put("/api/pricing/custom", json={"model": "modelo-casero", "input": 1.0, "output": 4.0})
    assert r.status_code == 200, r.text
    assert r.json()["repriced_spans"] > 0

    despues = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()
    assert despues["unknown_cost_spans"] == 0
    assert despues["window_cost_usd"] > 0


def test_borrar_un_proyecto_no_deja_nada_suyo(client):
    c, app = client
    app.state.store.insert_spans(_en(_agente("fuera"), AHORA - timedelta(hours=3)))
    app.state.store.insert_spans(_en(_agente("queda"), AHORA - timedelta(hours=3)))
    c.put("/api/budget", json={"project_id": "fuera", "monthly_limit": 10})

    mal = c.delete("/api/projects", params={"project_id": "fuera", "confirm": "otro"})
    assert mal.status_code == 400
    r = c.delete("/api/projects", params={"project_id": "fuera", "confirm": "fuera"})
    assert r.status_code == 200, r.text

    ids = {p["id"] for p in c.get("/api/projects").json()["projects"]}
    assert ids == {"queda"}
    meta: SQLiteMetadataStore = app.state.metadata
    assert meta.get_setting("fuera", presupuesto.CLAVE) is None


def test_un_webhook_de_alertas_nunca_sale_entero_por_la_api(client):
    c, _ = client
    secreto = "https://hooks.slack.com/services/T000/B000/muysecreto"
    r = c.put("/api/alert-settings", json={"project_id": "p", "webhook_url": secreto})
    assert r.status_code == 200, r.text
    assert "muysecreto" not in r.text
    assert r.json()["enabled"] is True
    assert "muysecreto" not in c.get("/api/alert-settings", params={"project_id": "p"}).text

    malo = c.put(
        "/api/alert-settings", json={"project_id": "p", "generic_webhook_url": "http://evil.com/x"}
    )
    assert malo.status_code == 400


def test_marcar_un_hallazgo_por_la_api_lo_aparta_del_inicio(client):
    c, app = client
    app.state.store.insert_spans(_en(_agente("p"), AHORA - timedelta(hours=3)))
    inicio = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()
    rep = next(f for f in inicio["findings"] if f["kind"] == "repeticion")

    r = c.post(
        "/api/finding-state",
        json={"project_id": "p", "finding_id": rep["id"], "status": "ignorado"},
    )
    assert r.status_code == 200, r.text
    vista = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()
    assert rep["id"] not in {f["id"] for f in vista["findings"]}
    assert rep["id"] in {f["id"] for f in vista["set_aside"]}

    c.delete("/api/finding-state", params={"project_id": "p", "finding_id": rep["id"]})
    vuelta = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()
    assert rep["id"] in {f["id"] for f in vuelta["findings"]}


def test_filtrar_por_usuario_no_deja_la_traza_sin_coste(store):
    """El usuario lo lleva el span raíz. Filtrar spans por él dejaba fuera las llamadas
    al modelo, y la traza salía en la lista con coste y tokens a cero."""
    from laplace_backend.storage.base import TraceFilter

    spans = _en(_agente("p"), AHORA - timedelta(hours=2))
    for s in spans:
        s.user_id = "ana" if s.parent_span_id is None else ""
    store.insert_spans(spans)

    todas = store.list_traces(TraceFilter(project_id="p", limit=50))
    de_ana = store.list_traces(TraceFilter(project_id="p", user_id="ana", limit=50))
    assert len(de_ana.traces) == len(todas.traces) == 6
    assert sum(t.cost.total_usd for t in de_ana.traces) == pytest.approx(
        sum(t.cost.total_usd for t in todas.traces)
    )
    assert all(t.cost.total_usd > 0 for t in de_ana.traces)
