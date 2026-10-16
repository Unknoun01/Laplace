"""De dónde viene cada span: el lenguaje, el SDK y el instrumentador (D-215).

La ingesta guarda del recurso OTLP `telemetry.sdk.language` y `telemetry.sdk.name`, y del
span la familia de atributos que trae. Con eso el plano de control dice qué parte de las
llamadas viene de procesos que no dicen llevar el SDK de Laplace, a los que la parada no
llega (D-206). Los spans de antes no lo tienen: no se cuentan, y se dice cuántos son.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan

from laplace_backend import control
from laplace_backend.coverage import MIN_CALLS_FOR_COVERAGE
from laplace_backend.ingest.otlp import parse_spans
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc).replace(microsecond=0)


def _kv(clave: str, valor: str) -> KeyValue:
    return KeyValue(key=clave, value=AnyValue(string_value=valor))


def _peticion(recurso: dict[str, str], atributos: dict[str, str]) -> ExportTraceServiceRequest:
    inicio = int(AHORA.timestamp() * 1e9)
    span = ProtoSpan(
        trace_id=uuid.uuid4().bytes,
        span_id=uuid.uuid4().bytes[:8],
        name="chat",
        start_time_unix_nano=inicio,
        end_time_unix_nano=inicio + 1_000_000,
        attributes=[_kv(k, v) for k, v in atributos.items()],
    )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=[_kv(k, v) for k, v in recurso.items()]),
                scope_spans=[ScopeSpans(spans=[span])],
            )
        ]
    )


def _uno(recurso: dict[str, str], atributos: dict[str, str]) -> Span:
    (span,) = parse_spans(_peticion(recurso, atributos))
    return span


NODE = {"service.name": "p", "telemetry.sdk.language": "nodejs",
        "telemetry.sdk.name": "opentelemetry"}
LAPLACE = {"service.name": "p", "telemetry.sdk.language": "python",
           "telemetry.sdk.name": "laplace"}


# ---------------------------------------------------------------------------------
# La ingesta
# ---------------------------------------------------------------------------------


def test_la_ingesta_guarda_lenguaje_y_sdk_del_recurso():
    span = _uno(NODE, {"gen_ai.request.model": "gpt-5.6-luna"})
    assert (span.sdk_language, span.sdk_name) == ("nodejs", "opentelemetry")
    span = _uno(LAPLACE, {"laplace.span.type": "llm"})
    assert (span.sdk_language, span.sdk_name) == ("python", "laplace")


@pytest.mark.parametrize(
    ("atributos", "familia"),
    [
        ({"laplace.span.type": "llm", "gen_ai.request.model": "m"}, "laplace"),
        ({"openinference.span.kind": "LLM", "llm.model_name": "m"}, "openinference"),
        ({"traceloop.span.kind": "task"}, "openllmetry"),
        ({"gen_ai.prompt.0.content": "hola", "gen_ai.request.model": "m"}, "openllmetry"),
        ({"gen_ai.request.model": "m", "gen_ai.operation.name": "chat"}, "genai"),
        ({"http.method": "GET"}, "otro"),
    ],
)
def test_la_ingesta_dice_que_familia_trae_el_span(atributos, familia):
    assert _uno(NODE, atributos).instrumentor == familia


def test_sin_nada_en_el_recurso_queda_vacio_pero_la_familia_si():
    span = _uno({"service.name": "p"}, {"http.method": "GET"})
    assert (span.sdk_language, span.sdk_name, span.instrumentor) == ("", "", "otro")


# ---------------------------------------------------------------------------------
# Los almacenes
# ---------------------------------------------------------------------------------


def _llamada(project: str, i: int, *, sdk: str, lenguaje: str, familia: str,
             peso: float = 1.0, tags: list[str] | None = None) -> Span:
    instante = AHORA - timedelta(hours=1, seconds=i)
    span = Span(
        span_id=f"{project[-6:]}{i:010d}",
        trace_id=f"{project}-t{i}",
        project_id=project,
        name="chat",
        type="llm",
        status="ok",
        start_time=instante,
        end_time=instante + timedelta(milliseconds=300),
        duration_ms=300.0,
        sample_rate=peso,
        tags=tags or [],
        sdk_name=sdk,
        sdk_language=lenguaje,
        instrumentor=familia,
    )
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        cost=Cost(input_usd=0.001, total_usd=0.001),
    )
    return span


def _mezcla(project: str) -> list[Span]:
    """6 de Python con el SDK (dos muestreadas a 1 de cada 2), 4 de Node y 3 viejas."""
    spans = [_llamada(project, i, sdk="laplace", lenguaje="python", familia="laplace")
             for i in range(4)]
    spans += [_llamada(project, 4 + i, sdk="laplace", lenguaje="python", familia="laplace",
                       peso=2.0) for i in range(2)]
    spans += [_llamada(project, 10 + i, sdk="opentelemetry", lenguaje="nodejs",
                       familia="genai") for i in range(4)]
    spans += [_llamada(project, 20 + i, sdk="", lenguaje="", familia="") for i in range(3)]
    # Una tirada de evaluación no es tráfico real.
    spans.append(_llamada(project, 30, sdk="opentelemetry", lenguaje="nodejs",
                          familia="genai", tags=[EVAL_TAG]))
    return spans


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(days=7), until=AHORA, days=7)


def test_el_origen_va_y_vuelve_en_sqlite(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(_mezcla("ida"))
    (leido,) = store.get_trace_spans("ida-t10")
    assert (leido.sdk_name, leido.sdk_language, leido.instrumentor) == (
        "opentelemetry", "nodejs", "genai"
    )


def _cuentas(store, project):
    return sorted(
        (f.sdk_name, f.sdk_language, f.instrumentor, f.llamadas, round(f.peso, 6))
        for f in store.origen_llamadas(project, _ventana())
    )


def test_las_llamadas_por_origen_en_sqlite(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(_mezcla("cuenta"))
    assert _cuentas(store, "cuenta") == [
        ("", "", "", 3, 3.0),
        ("laplace", "python", "laplace", 6, 8.0),
        ("opentelemetry", "nodejs", "genai", 4, 4.0),
    ]


def test_las_llamadas_por_origen_son_las_mismas_en_clickhouse(tmp_path):
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()
    local = SQLiteStore(tmp_path / "l.db")
    local.migrate()
    project = f"origen-{uuid.uuid4().hex[:8]}"
    spans = _mezcla(project)
    local.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        assert _cuentas(nube, project) == _cuentas(local, project)
        (leido,) = nube.get_trace_spans(f"{project}-t10", project)
        assert (leido.sdk_name, leido.sdk_language, leido.instrumentor) == (
            "opentelemetry", "nodejs", "genai"
        )
    finally:
        nube.delete_project(project)


def test_una_base_sqlite_de_antes_gana_las_columnas_y_sus_filas_quedan_sin_origen(tmp_path):
    ruta = tmp_path / "vieja.db"
    store = SQLiteStore(ruta)
    store.migrate()
    store.insert_spans([_llamada("viejo", 1, sdk="laplace", lenguaje="python",
                                 familia="laplace")])
    store._conn.close()  # noqa: SLF001
    # La base de antes de D-215: sin las tres columnas.
    con = sqlite3.connect(ruta)
    for columna in ("sdk_language", "sdk_name", "instrumentor"):
        con.execute(f"ALTER TABLE spans DROP COLUMN {columna}")
    con.commit()
    con.close()

    store = SQLiteStore(ruta)
    store.migrate()
    (leido,) = store.get_trace_spans("viejo-t1")
    assert (leido.sdk_name, leido.sdk_language, leido.instrumentor) == ("", "", "")
    assert _cuentas(store, "viejo") == [("", "", "", 1, 1.0)]


# ---------------------------------------------------------------------------------
# La cuenta del plano de control
# ---------------------------------------------------------------------------------


def test_el_alcance_dice_que_parte_queda_fuera_y_de_que_lenguajes(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    store.insert_spans(_mezcla("alcance"))
    alcance = control.alcance(store, "alcance", ahora=AHORA)
    assert alcance.llamadas == 10
    assert alcance.sin_origen == 3
    # 4 de Node contra 8 de Python con el SDK, pesadas por su muestreo.
    assert alcance.fuera == pytest.approx(4 / 12)
    assert alcance.lenguajes == ["nodejs"]
    assert alcance.minimo == MIN_CALLS_FOR_COVERAGE


def test_sin_muestra_no_hay_porcentaje(tmp_path):
    store = SQLiteStore(tmp_path / "l.db")
    store.migrate()
    pocas = [_llamada("pocas", i, sdk="opentelemetry", lenguaje="nodejs", familia="genai")
             for i in range(MIN_CALLS_FOR_COVERAGE - 1)]
    pocas += [_llamada("pocas", 50 + i, sdk="", lenguaje="", familia="") for i in range(20)]
    store.insert_spans(pocas)
    alcance = control.alcance(store, "pocas", ahora=AHORA)
    assert alcance.llamadas == MIN_CALLS_FOR_COVERAGE - 1
    assert alcance.sin_origen == 20
    assert alcance.fuera is None


def test_la_api_da_el_alcance(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    try:
        with TestClient(main.app) as client:
            main.app.state.store.insert_spans(_mezcla("api"))
            r = client.get("/api/control/alcance", params={"project_id": "api"})
            assert r.status_code == 200, r.text
            assert r.json()["fuera"] == pytest.approx(4 / 12)
            assert r.json()["lenguajes"] == ["nodejs"]
    finally:
        config.get_settings.cache_clear()
