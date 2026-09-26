"""Alertas a Slack.

Lo que se comprueba aquí no es que el mensaje salga: es que **no salga de más**. Una
alerta que se repite se ignora, y en cuanto se ignora una se ignoran todas, así que la
mayoría de estas pruebas son sobre silencios.

Nada de esto toca la red: el notificador se sustituye por uno que apunta lo que le
mandan. Una prueba que manda mensajes de verdad a un canal es una prueba que nadie
vuelve a ejecutar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from laplace_backend import insights
from laplace_backend.alerts import (
    AlertConfig,
    AlertRecord,
    AlertRunner,
    MemoryAlertState,
    ProjectAlertConfig,
    SQLiteAlertState,
    compose,
    decide,
)
from laplace_backend.config import Settings
from laplace_backend.insights import Finding, Overview
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class NotificadorFalso:
    """Un Slack de mentira que apunta lo que le mandan."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.enviados: list[tuple[str, str]] = []

    def send(self, webhook_url: str, text: str) -> bool:
        self.enviados.append((webhook_url, text))
        return self.ok


def _finding(id_: str, usd: float, **kwargs) -> Finding:
    return Finding(
        id=id_,
        kind=kwargs.pop("kind", "modelo_caro"),
        title=kwargs.pop("title", f"Problema {id_}"),
        summary="",
        window_waste_usd=usd,
        observed_days=kwargs.pop("observed_days", 3.0),
        monthly_saving_usd=kwargs.pop("monthly", usd / 3 * 30),
        **kwargs,
    )


def _overview(*findings: Finding, **kwargs) -> Overview:
    return Overview(
        project_id=kwargs.pop("project_id", "p"),
        days=7,
        observed_days=kwargs.pop("observed_days", 3.0),
        projected=kwargs.pop("projected", True),
        window_cost_usd=100.0,
        findings=list(findings),
        **kwargs,
    )


AJUSTES = ProjectAlertConfig(
    project_id="p", webhook_url="https://hooks.slack.com/x", min_usd=1.0, quiet_hours=24
)


# ---------------------------------------------------------------------------------
# El umbral
# ---------------------------------------------------------------------------------


def test_solo_alerta_lo_que_pasa_del_umbral():
    vista = _overview(_finding("caro", 5.0), _finding("barato", 0.5))
    decision = decide(vista, AJUSTES, {}, AHORA)
    assert [f.id for f in decision.due] == ["caro"]


def test_el_umbral_es_dinero_ya_gastado_no_proyectado():
    """Con minutos de datos no hay proyección (D-073), pero sí hay factura.

    Si el umbral mirase la cifra mensual, un proyecto recién instalado no podría
    alertar nunca, que es justo cuando más se agradece.
    """
    vista = _overview(
        _finding("nuevo", 5.0, monthly=None, observed_days=0.01),
        projected=False,
        observed_days=0.01,
    )
    assert [f.id for f in decide(vista, AJUSTES, {}, AHORA).due] == ["nuevo"]


def test_un_hallazgo_que_solo_cuesta_tiempo_no_alerta():
    """Deliberado: una alerta de coste habla de coste.

    Un bucle de herramientas que no gasta tokens sale en la pantalla con los segundos
    que tira, pero no cruza un umbral en dólares ni despierta a nadie de madrugada.
    """
    vista = _overview(_finding("lento", 0.0, costs_money=False, window_waste_ms=90_000))
    assert decide(vista, AJUSTES, {}, AHORA).due == []


def test_el_umbral_es_por_proyecto():
    vista = _overview(_finding("medio", 3.0))
    exigente = ProjectAlertConfig(project_id="p", webhook_url="x", min_usd=10.0)
    assert decide(vista, exigente, {}, AHORA).due == []
    assert decide(vista, AJUSTES, {}, AHORA).due


# ---------------------------------------------------------------------------------
# No repetirse: lo que decide si esto se lee o se silencia el canal (D-074)
# ---------------------------------------------------------------------------------


def test_el_mismo_problema_no_vuelve_a_avisar_dentro_del_periodo_de_calma():
    vista = _overview(_finding("caro", 5.0))
    estado = {
        "caro": AlertRecord("caro", notified_at=AHORA - timedelta(hours=2), seen_at=AHORA)
    }
    decision = decide(vista, AJUSTES, estado, AHORA)
    assert decision.due == []
    assert [f.id for f in decision.silenced] == ["caro"]


def test_empeorar_no_es_motivo_para_repetir():
    """El «avisamos otra vez porque ha subido» es la puerta trasera de la repetición.

    Cualquier cifra oscila, así que cualquier umbral de empeoramiento acaba disparando
    solo. Mientras dure la calma, se calla aunque el número sea diez veces mayor.
    """
    estado = {
        "caro": AlertRecord(
            "caro", notified_at=AHORA - timedelta(hours=1), seen_at=AHORA, amount_usd=5.0
        )
    }
    vista = _overview(_finding("caro", 50.0))
    assert decide(vista, AJUSTES, estado, AHORA).due == []


def test_pasado_el_periodo_de_calma_vuelve_a_avisar():
    estado = {
        "caro": AlertRecord("caro", notified_at=AHORA - timedelta(hours=25), seen_at=AHORA)
    }
    vista = _overview(_finding("caro", 5.0))
    assert [f.id for f in decide(vista, AJUSTES, estado, AHORA).due] == ["caro"]


def test_un_problema_que_baila_alrededor_del_umbral_no_avisa_cada_vez():
    """El caso que convierte un buen sistema de alertas en ruido.

    Un hallazgo que cruza y descruza el umbral desaparecería del estado en cuanto baja
    y volvería a contar como «nuevo» al subir. Por eso sólo se olvida cuando lleva un
    periodo de calma entero sin verse.
    """
    estado = {
        "caro": AlertRecord(
            "caro", notified_at=AHORA - timedelta(hours=2), seen_at=AHORA - timedelta(hours=2)
        )
    }
    # Ciclo en el que ha bajado del umbral: ni se avisa ni se olvida.
    bajado = decide(_overview(_finding("caro", 0.2)), AJUSTES, estado, AHORA)
    assert bajado.due == [] and bajado.forgotten == []

    # Y al volver a subir sigue dentro de su calma, así que sigue callado.
    subido = decide(_overview(_finding("caro", 5.0)), AJUSTES, estado, AHORA)
    assert subido.due == []


def test_un_problema_arreglado_se_olvida_y_puede_volver_a_avisar():
    viejo = AlertRecord(
        "caro",
        notified_at=AHORA - timedelta(days=3),
        seen_at=AHORA - timedelta(days=3),
    )
    decision = decide(_overview(), AJUSTES, {"caro": viejo}, AHORA)
    assert decision.forgotten == ["caro"]


def test_silenciar_un_proyecto_y_una_regla():
    vista = _overview(_finding("caro", 5.0, kind="repeticion"), _finding("otro", 5.0))
    sin_repeticiones = ProjectAlertConfig(
        project_id="p", webhook_url="x", min_usd=1.0, muted_kinds=frozenset({"repeticion"})
    )
    assert [f.id for f in decide(vista, sin_repeticiones, {}, AHORA).due] == ["otro"]

    callado = ProjectAlertConfig(project_id="p", webhook_url="x", muted=True)
    assert callado.enabled is False


# ---------------------------------------------------------------------------------
# El mensaje
# ---------------------------------------------------------------------------------


def test_el_mensaje_agrupa_todo_el_proyecto_y_lleva_dinero_y_enlace():
    vista = _overview(_finding("a", 5.0, title="Modelo caro"), _finding("b", 2.0, title="Contexto"))
    decision = decide(vista, AJUSTES, {}, AHORA)
    texto = compose(
        vista,
        decision,
        AJUSTES,
        {"a": "http://laplace.local/problema?id=a", "b": "http://laplace.local/problema?id=b"},
    )
    assert texto.count("•") == 2, "un mensaje por proyecto, no uno por hallazgo"
    assert "Modelo caro" in texto and "Contexto" in texto
    assert "5,00 US$" in texto, "coma decimal española, como todo el producto (D-120)"
    assert "http://laplace.local/problema?id=a" in texto
    assert "al mes" in texto


def test_una_cifra_no_fiable_se_anuncia_como_suelo_y_nunca_como_total():
    """La regla conservadora de siempre, aplicada a lo que se manda fuera.

    Si el coste del hallazgo está incompleto o cobrado a tarifa asumida, la alerta no
    puede afirmar una cifra: dice que se ha gastado *al menos* eso, y por qué.
    """
    hallazgo = _finding("dudoso", 5.0, cost_is_floor=True, assumed_rate_spans=4)
    vista = _overview(hallazgo)
    decision = decide(vista, AJUSTES, {}, AHORA)
    texto = compose(vista, decision, AJUSTES, {})

    assert "al menos 5,00 US$" in texto
    assert "suelo" in texto
    assert "mayor, nunca menor" in texto


def test_una_cifra_con_tarifa_sin_verificar_lo_dice_y_no_es_un_suelo():
    """Lo que sale fuera también tiene que decirlo (D-141). Pero no con «al menos»: una
    tarifa de LiteLLM puede pasarse igual que quedarse corta."""
    hallazgo = _finding(
        "gemini", 5.0, cost_unverified=True, unverified_rate_models=["gemini-2.5-pro"]
    )
    vista = _overview(hallazgo)
    texto = compose(vista, decide(vista, AJUSTES, {}, AHORA), AJUSTES, {})
    assert "al menos" not in texto
    assert "5,00 US$" in texto
    assert "tarifa sin verificar" in texto


def test_sin_proyeccion_la_alerta_no_inventa_una_cifra_mensual():
    hallazgo = _finding("nuevo", 5.0, monthly=None, observed_days=0.02)
    vista = _overview(hallazgo, projected=False, observed_days=0.02)
    texto = compose(vista, decide(vista, AJUSTES, {}, AHORA), AJUSTES, {})
    assert "al mes" not in texto
    assert "No se proyecta a mes" in texto


def test_el_mensaje_dice_cuantos_problemas_se_esta_callando():
    """Que el silencio se note. Si no, parece que el problema ha desaparecido."""
    vista = _overview(_finding("a", 5.0), _finding("b", 5.0))
    estado = {"b": AlertRecord("b", notified_at=AHORA - timedelta(hours=1), seen_at=AHORA)}
    decision = decide(vista, AJUSTES, estado, AHORA)
    texto = compose(vista, decision, AJUSTES, {})
    assert "Otro problema sigue abierto" in texto
    assert "periodo de calma (1 día)" in texto


# ---------------------------------------------------------------------------------
# El envío, con el estado de verdad
# ---------------------------------------------------------------------------------


@pytest.fixture
def escenario(tmp_path):
    """Un proyecto con las tres patologías, repartido en tres días de datos."""
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_sqlite_store import _agente, _repartir

    store = SQLiteStore(tmp_path / "laplace.db")
    store.migrate()
    store.insert_spans(_repartir(_agente("alertas"), dias=3))

    settings = Settings(
        store="sqlite",
        sqlite_path=str(tmp_path / "laplace.db"),
        alerts_enabled=True,
        alerts_slack_webhook="https://hooks.slack.com/services/T/B/x",
        alerts_base_url="http://127.0.0.1:8100",
        alerts_min_usd=0.01,
        alerts_quiet_hours=24,
    )
    notificador = NotificadorFalso()
    runner = AlertRunner(
        store,
        AlertConfig(settings),
        SQLiteAlertState(tmp_path / "laplace.db"),
        notificador,
    )
    return runner, notificador, store


def test_avisa_una_vez_y_luego_se_calla(escenario):
    """La prueba que resume el punto entero.

    Dos ciclos seguidos sobre el mismo problema tienen que producir **un** mensaje. Es
    lo que separa una alerta útil de un canal silenciado.
    """
    runner, notificador, _ = escenario

    primera = runner.evaluate("alertas")
    assert primera.due, "el primer ciclo tiene que avisar"
    assert len(notificador.enviados) == 1

    segunda = runner.evaluate("alertas")
    assert segunda.due == []
    assert segunda.silenced, "el problema sigue ahí, sólo que callado"
    assert len(notificador.enviados) == 1, "el segundo ciclo no puede volver a mandar"


def test_el_estado_sobrevive_al_reinicio(escenario, tmp_path):
    """Un reinicio no puede reabrir el periodo de calma.

    Con el estado en memoria, un backend que se reinicia cada pocos minutos —un bucle
    de despliegue, por ejemplo— volvería a avisar de todo en cada arranque.
    """
    runner, notificador, store = escenario
    runner.evaluate("alertas")
    assert len(notificador.enviados) == 1

    otro = AlertRunner(
        store,
        runner._config,  # noqa: SLF001 - se reconstruye el runner, no la configuración
        SQLiteAlertState(tmp_path / "laplace.db"),
        notificador,
    )
    assert otro.evaluate("alertas").due == []
    assert len(notificador.enviados) == 1


def test_si_slack_falla_no_se_da_por_avisado(escenario):
    """Recordar un envío que no ha salido es perder la alerta para siempre."""
    runner, notificador, _ = escenario
    notificador.ok = False

    fallida = runner.evaluate("alertas")
    assert fallida.due == [] and "fallado" in fallida.reason

    notificador.ok = True
    assert runner.evaluate("alertas").due, "al recuperarse, la alerta sale"


def test_el_enlace_apunta_a_la_ficha_del_problema(escenario):
    runner, notificador, _ = escenario
    runner.evaluate("alertas")
    _, texto = notificador.enviados[0]
    assert "http://127.0.0.1:8100/problema?project=alertas&days=7&id=" in texto


def test_sin_webhook_no_se_evalua_nada(tmp_path):
    """Sin sitio al que avisar, no se gasta ni una consulta."""
    store = SQLiteStore(tmp_path / "vacio.db")
    store.migrate()
    settings = Settings(store="sqlite", sqlite_path=str(tmp_path / "vacio.db"))
    runner = AlertRunner(store, AlertConfig(settings), MemoryAlertState(), NotificadorFalso())
    decision = runner.evaluate("lo-que-sea")
    assert decision.due == [] and "webhook" in decision.reason


def test_no_se_manda_nada_a_un_host_que_no_es_slack():
    """Una errata en la configuración no puede publicar la factura de nadie fuera."""
    from laplace_backend.alerts import SlackNotifier

    notificador = SlackNotifier()
    assert notificador.send("http://evil.example.com/hook", "hola") is False
    assert notificador.send("https://evil.example.com/hook", "hola") is False


# ---------------------------------------------------------------------------------
# Local y nube, el mismo comportamiento
# ---------------------------------------------------------------------------------


def test_en_local_el_estado_va_al_mismo_sqlite_que_las_trazas(tmp_path):
    from laplace_backend.alerts import build_alert_state

    settings = Settings(store="sqlite", sqlite_path=str(tmp_path / "laplace.db"))
    estado = build_alert_state(settings)
    assert isinstance(estado, SQLiteAlertState)

    estado.save("p", AlertRecord("x", notified_at=AHORA, seen_at=AHORA, times=1))
    assert estado.read("p")["x"].times == 1
    estado.forget("p", ["x"])
    assert estado.read("p") == {}


def test_las_alertas_estan_apagadas_por_defecto(tmp_path, monkeypatch):
    """Nada que mande mensajes fuera se enciende solo."""
    assert Settings().alerts_enabled is False

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")

    import importlib

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        cuerpo = client.get("/api/alerts").json()
        assert cuerpo["enabled"] is False
        assert "LAPLACE_ALERTS_ENABLED" in cuerpo["detail"]
    config.get_settings.cache_clear()


def test_laplace_ui_levanta_las_alertas_con_las_mismas_variables(tmp_path, monkeypatch):
    """El modo local no es una versión recortada, tampoco en esto (D-075).

    Se monta la aplicación como la monta `laplace ui`, con el webhook por entorno, y
    se comprueba que la API de estado dice que están en pie y sobre qué proyecto.
    """
    import importlib
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_sqlite_store import _agente, _repartir

    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    store.insert_spans(_repartir(_agente("local"), dias=3))

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_ALERTS_ENABLED", "true")
    monkeypatch.setenv("LAPLACE_ALERTS_SLACK_WEBHOOK", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setenv("LAPLACE_ALERTS_BASE_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("LAPLACE_ALERTS_MIN_USD", "0.01")

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        cuerpo = client.get("/api/alerts", params={"project_id": "local"}).json()
        assert cuerpo["enabled"] is True
        proyecto = cuerpo["projects"][0]
        assert proyecto["webhook_configured"] is True
        assert proyecto["would_alert"], "con datos y umbral bajo, algo tiene que saltar"
        # El secreto no sale por una ruta sin autenticar, pase lo que pase.
        assert "hooks.slack.com" not in client.get("/api/alerts").text
    config.get_settings.cache_clear()


def test_los_dos_almacenes_deciden_lo_mismo(tmp_path):
    """Paridad local/nube, con la decisión de alerta incluida.

    Es la misma promesa que sostiene el test de paridad del almacén: no puede haber un
    proyecto que alerte en la nube y se calle en local con los mismos spans.
    """
    import uuid

    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()

    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_sqlite_store import _agente, _repartir

    project = f"alertas-paridad-{uuid.uuid4().hex[:8]}"
    spans = _repartir(_agente(project), dias=3)
    local = SQLiteStore(tmp_path / "laplace.db")
    local.migrate()
    local.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        ahora = datetime.now(timezone.utc)
        window = Window(since=ahora - timedelta(days=7), until=ahora, days=7)
        ajustes = ProjectAlertConfig(project_id=project, webhook_url="x", min_usd=0.01)
        aqui = decide(insights.overview(local, project, window), ajustes, {}, ahora)
        alli = decide(insights.overview(nube, project, window), ajustes, {}, ahora)
        assert [f.id for f in aqui.due] == [f.id for f in alli.due]
        assert aqui.due, "sin hallazgos la comparación no comprueba nada"
    finally:
        nube.delete_project(project)


# ---------------------------------------------------------------------------------
# Canales desde la interfaz, estados y presupuesto (D-123)
# ---------------------------------------------------------------------------------


def _runner_con_metadatos(tmp_path):
    import sys
    from pathlib import Path

    from laplace_backend.storage.metadata import SQLiteMetadataStore

    sys.path.insert(0, str(Path(__file__).parent))
    from test_sqlite_store import _agente, _repartir

    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    store.insert_spans(_repartir(_agente("ui"), dias=0.5))
    meta = SQLiteMetadataStore(db)
    meta.migrate()
    # Sin LAPLACE_ALERTS_ENABLED: el canal se pone desde la interfaz.
    settings = Settings(store="sqlite", sqlite_path=str(db), alerts_min_usd=0.0)
    notificador = NotificadorFalso()
    runner = AlertRunner(
        store, AlertConfig(settings), MemoryAlertState(), notificador, metadata=meta
    )
    return runner, notificador, meta


def test_un_canal_puesto_en_la_interfaz_basta_para_avisar(tmp_path):
    runner, notificador, meta = _runner_con_metadatos(tmp_path)
    assert runner.evaluate("ui").due == [], "sin canal no se avisa, como siempre"

    meta.set_setting("ui", "alerts", {"webhook_url": "https://hooks.slack.com/services/T/B/x"})
    decision = runner.evaluate("ui")
    assert decision.due, decision.reason
    assert len(notificador.enviados) == 1


def test_lo_ignorado_no_alerta(tmp_path):
    runner, notificador, meta = _runner_con_metadatos(tmp_path)
    meta.set_setting("ui", "alerts", {"webhook_url": "https://hooks.slack.com/services/T/B/x"})
    for f in runner.evaluate("ui", dry_run=True).due:
        meta.set_setting("ui", f"finding:{f.id}", {"status": "ignorado", "at": "2026-01-01"})
    assert runner.evaluate("ui").due == []
    assert notificador.enviados == []


def test_el_presupuesto_avisa_una_vez_por_umbral_y_mes(tmp_path):
    runner, notificador, meta = _runner_con_metadatos(tmp_path)
    meta.set_setting(
        "ui",
        "alerts",
        {"webhook_url": "https://hooks.slack.com/services/T/B/x", "muted_kinds": [
            "repeticion", "bucle", "modelo_caro", "contexto_fijo"]},
    )
    meta.set_setting("ui", "budget", {"monthly_usd": 0.000001})

    primera = runner.evaluate("ui")
    assert primera.budget_notice and "Presupuesto" in notificador.enviados[0][1]
    segunda = runner.evaluate("ui")
    assert segunda.budget_notice == ""
    assert len(notificador.enviados) == 1
