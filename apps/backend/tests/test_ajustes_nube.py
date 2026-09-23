"""El SQL nuevo de D-122 a D-125, ejecutado en la nube y comparado con el local.

Todo lo de estas tandas tenía su prueba en SQLite, y el camino de la nube —ClickHouse y
Postgres— estaba escrito y sin ejecutar: la tabla de ajustes, el borrado de un proyecto,
el reparto por usuario, el recálculo de tarifas, la retención, el filtro por paso y la
frase de la pregunta. D-112 ya dejó escrito que una tanda que toca SQL de nube y se
entrega con esas pruebas saltadas está sin terminar.

Cada prueba siembra los mismos spans en los dos almacenes y exige que digan lo mismo.
Se saltan solas si no hay nada escuchando (`docker compose up -d clickhouse postgres`).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from laplace_backend.config import Settings
from laplace_backend.ingest.otlp import recalcular_coste
from laplace_backend.pricing import get_price_table, set_custom_prices
from laplace_backend.storage.base import TraceFilter, Window
from laplace_backend.storage.metadata import SQLiteMetadataStore
from laplace_backend.storage.sqlite import SQLiteStore

sys.path.insert(0, str(Path(__file__).parent))
from test_sqlite_store import _agente  # noqa: E402

AHORA = datetime.now(timezone.utc).replace(microsecond=0)


def _clickhouse():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando")
    store.migrate()
    return store


def _postgres():
    from laplace_backend.storage.postgres import PostgresMetadataStore

    meta = PostgresMetadataStore(Settings())
    if not meta.health():
        pytest.skip("no hay Postgres escuchando")
    meta.migrate()
    return meta


def _sembrar(project: str, cuando: datetime, *, usuario: str = "") -> list:
    """Los spans de `_agente`, en `cuando`, con usuario y entrada sólo en el raíz."""
    spans = _agente(project)
    inicio = min(s.start_time for s in spans)
    for s in spans:
        s.start_time = cuando + (s.start_time - inicio)
        s.end_time = s.start_time + timedelta(milliseconds=s.duration_ms)
        if s.parent_span_id is None:
            s.user_id = usuario
            s.input = {"pregunta": f"¿Qué pasa con {project}?", "intentos": 3}
    return spans


@pytest.fixture
def almacenes(tmp_path):
    nube = _clickhouse()
    local = SQLiteStore(tmp_path / "l.db")
    local.migrate()
    proyectos: list[str] = []

    def sembrar(spans):
        proyectos.extend({s.project_id for s in spans})
        nube.insert_spans(spans)
        local.insert_spans(spans)

    yield nube, local, sembrar
    for p in set(proyectos):
        nube.delete_project(p)


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(days=3), until=AHORA + timedelta(minutes=5), days=3)


# ---------------------------------------------------------------------------------
# ClickHouse frente a SQLite
# ---------------------------------------------------------------------------------


def test_el_reparto_por_usuario_es_el_mismo_en_los_dos(almacenes):
    nube, local, sembrar = almacenes
    p = f"nube-reparto-{uuid.uuid4().hex[:8]}"
    sembrar(_sembrar(p, AHORA - timedelta(hours=5), usuario="ana"))
    sembrar(_sembrar(p, AHORA - timedelta(hours=2), usuario="bea"))
    sembrar(_sembrar(p, AHORA - timedelta(hours=1)))

    def forma(grupos):
        return [(g.key, g.traces, round(g.cost_usd, 9), g.tokens) for g in grupos]

    en_nube = forma(nube.cost_by(p, _ventana(), "user", 10))
    en_local = forma(local.cost_by(p, _ventana(), "user", 10))
    assert en_nube == en_local
    assert {k for k, *_ in en_nube} == {"ana", "bea", ""}


def test_filtrar_por_usuario_y_por_paso_da_lo_mismo_y_con_coste(almacenes):
    nube, local, sembrar = almacenes
    p = f"nube-filtro-{uuid.uuid4().hex[:8]}"
    sembrar(_sembrar(p, AHORA - timedelta(hours=3), usuario="ana"))
    sembrar(_sembrar(p, AHORA - timedelta(hours=1), usuario="bea"))

    for filtro in (
        TraceFilter(project_id=p, user_id="ana", limit=50),
        TraceFilter(project_id=p, step_key="paso-json", limit=50),
    ):
        def filas(store, filtro=filtro):
            return sorted(
                (t.trace_id, round(t.cost.total_usd, 9)) for t in store.list_traces(filtro).traces
            )

        a, b = filas(nube), filas(local)
        assert a == b, filtro
        assert a and all(coste > 0 for _, coste in a), f"trazas sin coste con {filtro}"


def test_la_pregunta_de_cada_traza_sale_igual(almacenes):
    nube, local, sembrar = almacenes
    p = f"nube-pregunta-{uuid.uuid4().hex[:8]}"
    sembrar(_sembrar(p, AHORA - timedelta(hours=1)))
    filtro = TraceFilter(project_id=p, limit=50)
    a = {t.trace_id: t.input_preview for t in nube.list_traces(filtro).traces}
    b = {t.trace_id: t.input_preview for t in local.list_traces(filtro).traces}
    assert a == b
    assert set(a.values()) == {f"¿Qué pasa con {p}?"}


def test_una_tarifa_nueva_se_aplica_a_lo_guardado_en_la_nube(almacenes):
    """Reinsertar el span recalculado tiene que sustituirlo, no duplicarlo: en
    ClickHouse eso depende de `ReplacingMergeTree` y de leer con `FINAL`."""
    nube, _, sembrar = almacenes
    p = f"nube-tarifa-{uuid.uuid4().hex[:8]}"
    modelo = f"casero-{uuid.uuid4().hex[:6]}"
    spans = _sembrar(p, AHORA - timedelta(hours=1))
    for s in spans:
        if s.llm:
            s.llm.request_model = s.llm.response_model = modelo
            s.llm.cost = s.llm.cost.model_copy(update={"total_usd": 0.0, "unknown": True})
    sembrar(spans)
    antes = nube.summarize_window(p, _ventana())
    assert antes.unknown_cost_spans > 0 and antes.total_cost_usd == 0

    try:
        set_custom_prices({modelo: {"input": 1.0, "output": 4.0}})
        guardados = nube.spans_by_model(modelo)
        assert len(guardados) == antes.unknown_cost_spans
        nube.insert_spans([recalcular_coste(s, get_price_table()) for s in guardados])
    finally:
        set_custom_prices({})

    despues = nube.summarize_window(p, _ventana())
    assert despues.unknown_cost_spans == 0
    assert despues.total_cost_usd > 0
    assert despues.spans == antes.spans, "el recálculo ha duplicado spans en vez de sustituir"


def test_la_retencion_borra_lo_viejo_y_nada_mas(almacenes):
    nube, local, sembrar = almacenes
    p = f"nube-retencion-{uuid.uuid4().hex[:8]}"
    # Muy atrás, para que el corte no alcance a nada de lo que haya en la base de
    # desarrollo: la retención es de toda la instalación, no de un proyecto.
    muy_viejo = datetime(1999, 1, 1, tzinfo=timezone.utc)
    sembrar(_sembrar(p, muy_viejo))
    sembrar(_sembrar(p, AHORA - timedelta(hours=1)))
    corte = datetime(2000, 1, 1, tzinfo=timezone.utc)

    for store in (nube, local):
        store.delete_before(corte)
        quedan = store.list_traces(TraceFilter(project_id=p, limit=50)).traces
        assert quedan and all(t.start_time >= corte for t in quedan), type(store).__name__
        assert len(quedan) == 6, type(store).__name__


# ---------------------------------------------------------------------------------
# Postgres frente a SQLite
# ---------------------------------------------------------------------------------


def test_los_ajustes_se_guardan_igual_en_los_dos(tmp_path):
    nube = _postgres()
    local = SQLiteMetadataStore(tmp_path / "m.db")
    local.migrate()
    p = f"nube-ajustes-{uuid.uuid4().hex[:8]}"
    try:
        for meta in (nube, local):
            meta.set_setting(p, "budget", {"monthly_usd": 12.5})
            meta.set_setting(p, "finding:bucle:abc", {"status": "ignorado", "note": "ñandú"})
            meta.set_setting(p, "finding:bucle:abc", {"status": "arreglado", "note": ""})
            meta.set_setting(p, "finding:repeticion:x", {"status": "ignorado"})

        for meta in (nube, local):
            assert meta.get_setting(p, "budget") == {"monthly_usd": 12.5}
            assert meta.get_setting(p, "nada") is None
        assert nube.list_settings(p, "finding:") == local.list_settings(p, "finding:")
        assert nube.list_settings(p, "finding:")["finding:bucle:abc"]["status"] == "arreglado"

        for meta in (nube, local):
            assert meta.delete_setting(p, "budget") is True
            assert meta.delete_setting(p, "budget") is False
    finally:
        nube.delete_project_data(p)


def test_borrar_un_proyecto_en_postgres_no_deja_nada_suyo_ni_toca_a_otro():
    from laplace.schema import Annotation, Dataset, DatasetItem

    nube = _postgres()
    fuera = f"nube-fuera-{uuid.uuid4().hex[:8]}"
    queda = f"nube-queda-{uuid.uuid4().hex[:8]}"
    ahora = datetime.now(timezone.utc)
    try:
        for p in (fuera, queda):
            nube.ensure_project(p)
            nube.set_setting(p, "budget", {"monthly_usd": 1})
            nota = Annotation(
                id=f"ann-{uuid.uuid4().hex[:8]}",
                trace_id=f"t-{p}",
                verdict="pass",
                created_at=ahora,
            )
            nube.save_annotation(p, nota)
            ds = Dataset(id=f"ds-{uuid.uuid4().hex[:8]}", project_id=p, name="x", created_at=ahora)
            caso = DatasetItem(
                id=f"c-{uuid.uuid4().hex[:8]}",
                dataset_id=ds.id,
                trace_id=f"t-{p}",
                created_at=ahora,
            )
            nube.create_dataset(ds, [caso])

        nube.delete_project_data(fuera)

        assert nube.get_setting(fuera, "budget") is None
        assert nube.list_datasets(fuera) == []
        assert nube.annotations_for([f"t-{fuera}"]) in ({}, {f"t-{fuera}": []})
        # Y el otro sigue entero.
        assert nube.get_setting(queda, "budget") == {"monthly_usd": 1}
        assert len(nube.list_datasets(queda)) == 1
        assert nube.annotations_for([f"t-{queda}"]).get(f"t-{queda}")
    finally:
        nube.delete_project_data(fuera)
        nube.delete_project_data(queda)


# ---------------------------------------------------------------------------------
# Cuentas (D-127): el mismo recorrido en Postgres y en SQLite
# ---------------------------------------------------------------------------------


def _recorrido_de_cuentas(cuentas, sufijo: str) -> dict:
    """Todo lo que hace una organización, en orden, y lo que se ve al final."""
    from datetime import timedelta as td

    usuario = cuentas.crear_usuario(f"ana-{sufijo}@ejemplo.com", "Ana", "contraseña-larga-1")
    org = cuentas.crear_org(f"Org {sufijo}")
    cuentas.poner_miembro(org, usuario.id, "propietario")
    proyecto = f"cuentas-{sufijo}"
    assert cuentas.asignar_proyecto(proyecto, org) is True
    assert cuentas.asignar_proyecto(proyecto, "otra-org") is False

    token = cuentas.abrir_sesion(usuario.id, "pytest")
    visto = cuentas.usuario_de_sesion(token)
    invitacion = cuentas.invitar(org, f"bea-{sufijo}@ejemplo.com", "lector", usuario.id)
    info = cuentas.invitacion(invitacion)
    key_id, _ = cuentas.crear_clave(proyecto, "ingesta", usuario.email, None)
    _, caducada = cuentas.crear_clave(proyecto, "vieja", usuario.email,
                                      datetime.now(timezone.utc) - td(days=1))
    revocada = cuentas.revocar_clave(key_id, [proyecto])
    cuentas.anotar(org, usuario.id, "crear_clave", proyecto)
    cuentas.cerrar_sesion(token)
    return {
        "sesion": visto.email if visto else None,
        "tras_cerrar": cuentas.usuario_de_sesion(token),
        "roles": cuentas.roles_por_proyecto(usuario.id),
        "invitacion": (info["role"], info["email"].split("@")[0].split("-")[0]) if info else None,
        "claves": sorted(
            (k["name"], k["revoked_at"] is not None) for k in cuentas.claves([proyecto])
        ),
        "revocada": revocada,
        "auditoria": [e["action"] for e in cuentas.auditoria(org)],
        "miembros": [m["role"] for m in cuentas.miembros(org)],
        "caducada": bool(caducada),
        "proyecto": proyecto,
        "org": org,
    }


def test_las_cuentas_hacen_lo_mismo_en_postgres_y_en_sqlite(tmp_path):
    from laplace_backend.cuentas import PostgresCuentas, SQLiteCuentas

    _postgres()  # se salta si no hay Postgres, y deja el esquema aplicado
    nube = PostgresCuentas(Settings().postgres_dsn)
    nube.migrate()
    local_meta = SQLiteMetadataStore(tmp_path / "c.db")
    local_meta.migrate()
    local = SQLiteCuentas(tmp_path / "c.db")
    local.migrate()

    sufijo = uuid.uuid4().hex[:8]
    a = _recorrido_de_cuentas(nube, sufijo)
    b = _recorrido_de_cuentas(local, sufijo)
    try:
        for clave in ("sesion", "tras_cerrar", "invitacion", "claves", "revocada", "auditoria",
                      "miembros", "caducada"):
            assert a[clave] == b[clave], clave
        assert a["roles"] == {a["proyecto"]: "propietario"}
        assert a["sesion"] == f"ana-{sufijo}@ejemplo.com"
        assert a["tras_cerrar"] is None
    finally:
        nube._ejecutar("DELETE FROM api_keys WHERE project_id = ?", (a["proyecto"],))
        nube._ejecutar("DELETE FROM org_projects WHERE project_id = ?", (a["proyecto"],))
        nube._ejecutar("DELETE FROM memberships WHERE org_id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM invitations WHERE org_id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM audit_log WHERE org_id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM orgs WHERE id = ?", (a["org"],))
        nube._ejecutar("DELETE FROM projects WHERE id = ?", (a["proyecto"],))
        nube._ejecutar(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email LIKE ?)",
            (f"%-{sufijo}@ejemplo.com",),
        )
        nube._ejecutar("DELETE FROM users WHERE email LIKE ?", (f"%-{sufijo}@ejemplo.com",))
