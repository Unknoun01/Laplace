"""`service.version` como segunda señal de despliegue del bot de PR (D-216).

Si el tráfico trae `service.version` y se puede atar al commit de la fusión —un SHA, o
una etiqueta que GitHub resuelve—, la frontera del «arreglado» pasa a la primera
ejecución con esa versión. Un texto libre no vale como señal, y la nota lo dice.
"""

# ruff: noqa: F811 - `con_pr` y `api` son fixtures importadas

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG
from test_bot_arreglos import api  # noqa: F401 - `api` es una fixture
from test_bot_webhook import (
    FUSION_SHA,
    _desplegar,
    _estado,
    _fusionar,
    con_pr,  # noqa: F401 - fixture
)

from laplace_backend import api_ajustes
from laplace_backend import github_bot as gb
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore
from laplace_backend.textos import t

ETIQUETA_SHA = "b" * 40
VIEJA_SHA = "a" * 40


@pytest.fixture(autouse=True)
def sin_espera():
    """Cada prueba mira las versiones desde cero, sin el respiro de 5 minutos."""
    api_ajustes._VERSIONES_REVISADAS.clear()
    yield
    api_ajustes._VERSIONES_REVISADAS.clear()


def _hora(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=timezone.utc)


def _llamada(project: str, version: str, cuando: str, *, tags: list[str] | None = None) -> Span:
    inicio = _hora(cuando)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        project_id=project,
        name="chat",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=300),
        duration_ms=300.0,
        tags=tags or [],
        service_version=version,
    )
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        cost=Cost(input_usd=0.001, total_usd=0.001),
    )
    return span


def _trafico(app, *llamadas: tuple[str, str], tags: list[str] | None = None) -> None:
    app.state.store.insert_spans([_llamada("p", v, cuando, tags=tags) for v, cuando in llamadas])


def _mirar(c, hallazgo) -> None:
    """Abrir la ficha es una de las veces en que se miran las versiones."""
    api_ajustes._VERSIONES_REVISADAS.clear()
    r = c.get(f"/api/findings/{hallazgo['id']}", params={"project_id": "p", "days": 7})
    assert r.status_code == 200, r.text


@pytest.fixture
def github_con_etiquetas(con_pr, monkeypatch):
    """GitHub sabe de la etiqueta `v1.4.0`, de la vieja `v1.3.0` y de nada más."""
    falso = gb._pedir
    preguntas: list[str] = []
    etiquetas = {"v1.4.0": ETIQUETA_SHA, "v1.3.0": VIEJA_SHA}
    lleva = {ETIQUETA_SHA: "ahead", VIEJA_SHA: "behind"}

    def pedir(metodo, url, token, cuerpo):
        if "/commits/tags/" in url:
            preguntas.append(url)
            nombre = url.rsplit("/commits/tags/", 1)[1]
            if nombre not in etiquetas:
                raise gb.GitHubError("github.no_encontrado")
            return {"sha": etiquetas[nombre]}
        if "/compare/" in url:
            return {"status": lleva[url.rsplit("...", 1)[1]]}
        return falso(metodo, url, token, cuerpo)

    monkeypatch.setattr(gb, "_pedir", pedir)
    return (*con_pr, preguntas)


# ---------------------------------------------------------------------------------
# La ingesta y los almacenes
# ---------------------------------------------------------------------------------


def test_la_ingesta_guarda_service_version_del_recurso():
    from test_origen_spans import _uno

    span = _uno({"service.name": "p", "service.version": "v1.4.0"}, {"http.method": "GET"})
    assert span.service_version == "v1.4.0"


def _primeras(store, project: str) -> dict:
    ventana = Window(since=_hora("2026-10-03T10:00:00"), until=_hora("2026-10-20T00:00:00"),
                     days=17)
    return store.primeras_versiones(project, ventana)


def _versiones(project: str) -> list[Span]:
    return [
        _llamada(project, "v1.4.0", "2026-10-05T09:00:00"),
        _llamada(project, "v1.4.0", "2026-10-04T12:00:00"),
        _llamada(project, "v1.3.0", "2026-10-03T11:00:00"),
        # Antes de la ventana, sin versión y de evaluación: nada de eso cuenta.
        _llamada(project, "v1.2.0", "2026-10-01T09:00:00"),
        _llamada(project, "", "2026-10-04T09:00:00"),
        _llamada(project, "v9.9.9", "2026-10-04T09:00:00", tags=[EVAL_TAG]),
    ]


ESPERADAS = {
    "v1.4.0": _hora("2026-10-04T12:00:00"),
    "v1.3.0": _hora("2026-10-03T11:00:00"),
}


def test_la_primera_ejecucion_de_cada_version_en_sqlite(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(_versiones("v"))
    assert _primeras(store, "v") == ESPERADAS


def test_la_primera_ejecucion_de_cada_version_es_la_misma_en_clickhouse(tmp_path):
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()
    project = f"version-{uuid.uuid4().hex[:8]}"
    spans = _versiones(project)
    nube.insert_spans(spans)
    try:
        assert _primeras(nube, project) == ESPERADAS
        (leido,) = nube.get_trace_spans(spans[0].trace_id, project)
        assert leido.service_version == "v1.4.0"
    finally:
        nube.delete_project(project)


# ---------------------------------------------------------------------------------
# La frontera
# ---------------------------------------------------------------------------------


def test_un_sha_que_es_la_fusion_mueve_la_frontera_a_su_primera_ejecucion(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    _fusionar(c, hallazgo, numero, secreto)
    _trafico(app, (FUSION_SHA, "2026-10-04T12:00:00"), (FUSION_SHA, "2026-10-04T09:00:00"))
    _mirar(c, hallazgo)
    estado = _estado(app, hallazgo["id"])
    assert estado["at"].startswith("2026-10-04T09:00:00")
    assert FUSION_SHA[:7] in estado["note"] and "service.version" in estado["note"]


def test_una_etiqueta_que_lleva_la_fusion_cuenta_y_una_vieja_no(github_con_etiquetas):
    c, app, hallazgo, numero, secreto, preguntas = github_con_etiquetas
    _fusionar(c, hallazgo, numero, secreto)
    # La vieja sigue corriendo después de la fusión: no lleva el arreglo.
    _trafico(app, ("v1.3.0", "2026-10-03T11:00:00"), ("v1.4.0", "2026-10-06T08:00:00"))
    _mirar(c, hallazgo)
    estado = _estado(app, hallazgo["id"])
    assert estado["at"].startswith("2026-10-06T08:00:00")
    assert "v1.4.0" in estado["note"]
    assert any(p.endswith("/repos/acme/agentes/commits/tags/v1.4.0") for p in preguntas)


def test_un_texto_libre_no_vale_y_la_nota_lo_dice(github_con_etiquetas):
    c, app, hallazgo, numero, secreto, preguntas = github_con_etiquetas
    _fusionar(c, hallazgo, numero, secreto)
    _trafico(app, ("build-verde", "2026-10-04T09:00:00"))
    _mirar(c, hallazgo)
    estado = _estado(app, hallazgo["id"])
    assert estado["at"].startswith("2026-10-03T10:00:00")
    assert t("github.version_libre", versiones="build-verde") in estado["note"]
    pr = app.state.metadata.get_setting("p", gb.PREFIJO_PR + hallazgo["id"])
    assert pr["versiones"] == {"build-verde": ""}
    # Ya se sabe que no se puede atar: no se le vuelve a preguntar a GitHub.
    antes = len(preguntas)
    _mirar(c, hallazgo)
    assert len(preguntas) == antes
    assert estado == _estado(app, hallazgo["id"])


def test_si_github_no_contesta_no_se_apunta_nada(con_pr, monkeypatch):
    c, app, hallazgo, numero, secreto = con_pr
    _fusionar(c, hallazgo, numero, secreto)
    falso = gb._pedir

    def caido(metodo, url, token, cuerpo):
        if "/commits/tags/" in url:
            raise gb.GitHubError("github.sin_respuesta")
        return falso(metodo, url, token, cuerpo)

    monkeypatch.setattr(gb, "_pedir", caido)
    _trafico(app, ("v1.4.0", "2026-10-04T09:00:00"))
    _mirar(c, hallazgo)
    estado = _estado(app, hallazgo["id"])
    assert estado["at"].startswith("2026-10-03T10:00:00")
    assert "service.version" not in estado["note"]
    pr = app.state.metadata.get_setting("p", gb.PREFIJO_PR + hallazgo["id"])
    assert "v1.4.0" not in (pr.get("versiones") or {})


def test_antes_de_la_fusion_o_en_evaluaciones_no_cuenta(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    _fusionar(c, hallazgo, numero, secreto)
    _trafico(app, (FUSION_SHA, "2026-10-02T09:00:00"))
    _trafico(app, (FUSION_SHA, "2026-10-04T09:00:00"), tags=[EVAL_TAG])
    _mirar(c, hallazgo)
    assert _estado(app, hallazgo["id"])["at"].startswith("2026-10-03T10:00:00")


@pytest.mark.parametrize(
    ("version_en", "version_antes", "esperada"),
    [
        ("2026-10-06T09:00:00", False, "2026-10-05T08:30:00"),  # despliegue, luego versión
        ("2026-10-04T09:00:00", False, "2026-10-04T09:00:00"),  # la versión, antes
        ("2026-10-04T09:00:00", True, "2026-10-04T09:00:00"),   # y si llega primero
    ],
)
def test_con_despliegue_y_version_vale_la_mas_temprana(con_pr, version_en, version_antes,
                                                       esperada):
    c, app, hallazgo, numero, secreto = con_pr
    _fusionar(c, hallazgo, numero, secreto)
    _trafico(app, (FUSION_SHA, version_en))
    if version_antes:
        _mirar(c, hallazgo)
    _desplegar(c, secreto)  # 2026-10-05T08:30
    _mirar(c, hallazgo)
    assert _estado(app, hallazgo["id"])["at"].startswith(esperada)


def test_lo_que_marco_la_persona_no_lo_mueve_la_version(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    _fusionar(c, hallazgo, numero, secreto)
    c.post("/api/finding-state",
           json={"project_id": "p", "finding_id": hallazgo["id"], "status": "arreglado"})
    a_mano = _estado(app, hallazgo["id"])
    _trafico(app, (FUSION_SHA, "2026-10-04T09:00:00"))
    _mirar(c, hallazgo)
    assert _estado(app, hallazgo["id"]) == a_mano


def test_tambien_se_mira_al_llegar_un_aviso_de_github(con_pr):
    c, app, hallazgo, numero, secreto = con_pr
    _trafico(app, (FUSION_SHA, "2026-10-04T09:00:00"))
    # El aviso de la fusión llega tarde, con el tráfico de la versión ya dentro.
    _fusionar(c, hallazgo, numero, secreto)
    assert _estado(app, hallazgo["id"])["at"].startswith("2026-10-04T09:00:00")
