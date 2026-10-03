"""Los preagregados del Diagnóstico dan lo mismo que la cuenta en crudo (D-177).

La prueba de fuego que pedía la hoja de ruta: un mes de la demo por la ingesta de verdad,
leído con los preagregados y sin ellos, lector a lector y el Diagnóstico entero. Se mira
en los tres estados en que puede estar una hora: sin calcular (se lee en crudo con la
selección de los parciales), calculada, y sucia después de calcularse (un lote reenviado,
una tarifa que cambia, un borrado). Y las dos trampas que se buscaban: que un reenvío o
un recálculo de coste cuenten dos veces, y que una repetición partida por el borde de una
hora se pierda.
"""

from __future__ import annotations

import dataclasses
import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from laplace.semconv import EVAL_TAG

from laplace_backend.config import Settings
from laplace_backend.insights import overview
from laplace_backend.storage import clickhouse
from laplace_backend.storage.base import Window

sys.path.insert(0, str(Path(__file__).parent))

FIN = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def mes():
    """Los spans de un mes de la demo, con todas las patologías, ya traducidos."""
    from helpers import exporter, ingest
    from laplace import demo

    parche = pytest.MonkeyPatch()
    parche.setattr(demo, "init", lambda **_: None)
    parche.setattr(demo, "flush", lambda: None)
    parche.setattr(demo, "set_context", lambda **_: None)
    try:
        exporter.clear()
        demo.generar_mes("http://nadie", project="demo", ahora=FIN)
        spans = ingest()
        exporter.clear()
    finally:
        parche.undo()
    return spans


@pytest.fixture
def almacenes():
    """El mismo ClickHouse leído con preagregados y sin ellos, en una base propia."""
    general = clickhouse.ClickHouseStore(Settings())
    if not general.health():
        pytest.skip("no hay ClickHouse escuchando")
    base = f"prueba_d177_{uuid.uuid4().hex[:8]}"
    general._client.command(f"CREATE DATABASE {base}")
    pre = clickhouse.ClickHouseStore(Settings(clickhouse_database=base, preagregados=True))
    crudo = clickhouse.ClickHouseStore(Settings(clickhouse_database=base, preagregados=False))
    pre.migrate()
    try:
        yield pre, crudo
    finally:
        general._client.command(f"DROP DATABASE IF EXISTS {base} SYNC")


def _cargar(store, spans: list[Span], proyecto: str) -> None:
    for span in spans:
        span.project_id = proyecto
    for i in range(0, len(spans), 5000):
        store.insert_spans(spans[i : i + 5000])


def _calcular_todo(store) -> int:
    total = 0
    while hechas := store.recalcular_preagregados(limite=500, quieta_s=0):
        total += hechas
    return total


def _plano(x):
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {k: _plano(v) for k, v in dataclasses.asdict(x).items()}
    if hasattr(x, "model_dump"):
        return _plano(x.model_dump())
    if isinstance(x, dict):
        return {k: _plano(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plano(v) for v in x]
    return x


def _diferencias(a, b, donde: str = "") -> list[str]:
    """Lo que no coincide. Las sumas de coma flotante se comparan con tolerancia: el
    orden de la suma cambia con cómo se trocea, y ninguna cifra se enseña con 12
    decimales."""
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return []
        return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) else [f"{donde}: {a} != {b}"]
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{donde}: claves {sorted(a.keys() ^ b.keys())}"]
        return [d for k in a for d in _diferencias(a[k], b[k], f"{donde}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{donde}: {len(a)} elementos != {len(b)}"]
        pares = enumerate(zip(a, b, strict=True))
        return [d for i, (x, y) in pares for d in _diferencias(x, y, f"{donde}[{i}]")]
    return [] if a == b else [f"{donde}: {a!r} != {b!r}"]


def _lecturas(store, proyecto: str, ventana: Window) -> dict:
    return {
        "resumen": store.summarize_window(proyecto, ventana),
        "repeticiones": store.repeated_groups(proyecto, ventana),
        "repeticiones_2": store.repeated_groups(proyecto, ventana, min_repeats=2),
        "bucles": store.loop_groups(proyecto, ventana),
        "uso": store.model_usage(proyecto, ventana, min_calls=1),
        "cobertura": store.coverage(proyecto, ventana),
        "serie_horas": store.step_cost_series(proyecto, ventana, 60),
        "serie_dias": store.step_cost_series(proyecto, ventana, 1440),
        "prompts": store.prompt_usage(proyecto, ventana),
        "prompts_reglas": store.prompt_usage(proyecto, ventana, rules=True),
        "prefijos": sorted(store.prefix_traces(proyecto, ventana).items()),
        "diagnostico": overview(store, proyecto, ventana),
    }


def _comparar(pre, crudo, proyecto: str, ventana: Window, cuando: str) -> None:
    a = _plano(_lecturas(pre, proyecto, ventana))
    b = _plano(_lecturas(crudo, proyecto, ventana))
    diferencias = _diferencias(a, b)
    assert diferencias == [], f"{cuando}, ventana {ventana.since}–{ventana.until}:\n" + "\n".join(
        diferencias[:20]
    )


VENTANAS = {
    "30 días": Window(since=FIN - timedelta(days=30), until=FIN, days=30),
    "7 días": Window(since=FIN - timedelta(days=7), until=FIN, days=7),
    "1 día": Window(since=FIN - timedelta(days=1), until=FIN, days=1),
    # Sin alinear: los bordes caen a media hora y a mitad de minuto.
    "a destiempo": Window(
        since=FIN - timedelta(days=2, minutes=37, seconds=12.25),
        until=FIN - timedelta(hours=5, seconds=31.5),
        days=2,
    ),
}


def _con_tiradas(mes: list[Span]) -> list[Span]:
    """El mes, con una de cada quince ejecuciones marcada como tirada de evaluación en su
    raíz, que es donde la pone el SDK. La demo no trae ninguna."""
    tiradas = set(sorted({s.trace_id for s in mes})[::15])
    return [
        s.model_copy(update={"tags": [*s.tags, EVAL_TAG]})
        if s.trace_id in tiradas and s.parent_span_id is None
        else s.model_copy()
        for s in mes
    ]


def test_el_diagnostico_sale_igual_con_y_sin_preagregados(almacenes, mes):
    pre, crudo = almacenes
    _cargar(pre, _con_tiradas(mes), "demo")
    for nombre, ventana in VENTANAS.items():
        _comparar(pre, crudo, "demo", ventana, f"sin calcular ({nombre})")
    assert _calcular_todo(pre) > 300, "tiene que haber calculado las horas con tráfico del mes"
    for nombre, ventana in VENTANAS.items():
        _comparar(pre, crudo, "demo", ventana, f"calculado ({nombre})")


def test_un_reenvio_o_una_tarifa_nueva_no_cuentan_dos_veces(almacenes, mes):
    """La trampa: sumar al insertar contaría el reenvío y el recálculo."""
    pre, crudo = almacenes
    _cargar(pre, [s.model_copy() for s in mes], "demo")
    _calcular_todo(pre)
    ventana = VENTANAS["7 días"]
    del_dia = [s for s in mes if FIN - timedelta(days=2) <= s.start_time < FIN - timedelta(days=1)]
    # El exportador reintenta: los mismos spans otra vez.
    _cargar(pre, [s.model_copy() for s in del_dia], "demo")
    _comparar(pre, crudo, "demo", ventana, "reenviado, sin recalcular")
    _calcular_todo(pre)
    _comparar(pre, crudo, "demo", ventana, "reenviado y recalculado")
    # Una tarifa nueva: los mismos spans con otro coste.
    caros = []
    for s in del_dia:
        if s.llm is not None:
            s = s.model_copy(deep=True)
            s.llm.cost.total_usd = (s.llm.cost.total_usd or 0.0) * 3 + 0.01
            caros.append(s)
    assert caros
    _cargar(pre, caros, "demo")
    _comparar(pre, crudo, "demo", ventana, "coste nuevo, sin recalcular")
    _calcular_todo(pre)
    _comparar(pre, crudo, "demo", ventana, "coste nuevo y recalculado")
    assert pre.summarize_window("demo", ventana).total_cost_usd == pytest.approx(
        crudo.summarize_window("demo", ventana).total_cost_usd
    )


def _llamada(proyecto: str, traza: str, cuando: datetime, entrada: str) -> Span:
    return Span(
        span_id=uuid.uuid4().hex[:16], trace_id=traza, project_id=proyecto,
        name="chat gpt-5.6-terra", type="llm", status="ok", start_time=cuando,
        end_time=cuando + timedelta(milliseconds=300), duration_ms=300.0,
        dedup_hash=entrada, loop_hash=f"bucle-{traza[:6]}", loop_out_hash="mismo",
        step_key="paso-json", step_label="responder", step_hint="Devuelve JSON",
        llm=LLMAttributes(
            system="openai", request_model="gpt-5.6-terra", response_model="gpt-5.6-terra",
            usage=TokenUsage(input_tokens=60, output_tokens=6),
            cost=Cost(total_usd=0.0002),
        ),
    )


def test_una_repeticion_partida_por_el_borde_de_una_hora_no_se_pierde(almacenes):
    """Tres llamadas iguales, una a cada lado de las 14:00: cada hora sólo ve una o dos,
    y ninguna llega a tres sola. Juntas, en una ventana que cubre las dos, sí."""
    pre, crudo = almacenes
    borde = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)
    traza = uuid.uuid4().hex
    spans = [
        _llamada("borde", traza, borde - timedelta(seconds=2), "igual"),
        _llamada("borde", traza, borde + timedelta(seconds=1), "igual"),
        _llamada("borde", traza, borde + timedelta(seconds=3), "igual"),
        # Y un bucle: cuatro vueltas con entradas distintas, dos a cada lado.
        *[
            _llamada("borde", traza, borde + timedelta(seconds=s), f"vuelta-{s}")
            for s in (-4, -3, 5, 6)
        ],
    ]
    pre.insert_spans(spans)
    _calcular_todo(pre)
    ventana = Window(since=borde - timedelta(hours=2), until=borde + timedelta(hours=2), days=1)
    assert [g.total_spans for g in pre.repeated_groups("borde", ventana)] == [3]
    assert [g.total_spans for g in pre.loop_groups("borde", ventana)] == [7]
    _comparar(pre, crudo, "borde", ventana, "partida por la hora")
    # Una ventana que empieza en el borde sólo ve dos: ahí no hay repetición.
    tras = Window(since=borde, until=borde + timedelta(hours=2), days=1)
    assert pre.repeated_groups("borde", tras) == crudo.repeated_groups("borde", tras) == []


def test_borrar_un_cliente_deja_las_horas_al_dia(almacenes, mes):
    pre, crudo = almacenes
    # Una de cada diez ejecuciones es del cliente que pide que se borre lo suyo.
    suyas = {t for t in sorted({s.trace_id for s in mes})[::10]}
    spans = [
        s.model_copy(update={"customer_id": "acme"}) if s.trace_id in suyas else s.model_copy()
        for s in mes
    ]
    _cargar(pre, spans, "demo")
    _calcular_todo(pre)
    assert pre.delete_subject("demo", customer_id="acme") == len(suyas)
    ventana = VENTANAS["30 días"]
    _comparar(pre, crudo, "demo", ventana, "borrado un cliente, sin recalcular")
    _calcular_todo(pre)
    _comparar(pre, crudo, "demo", ventana, "borrado un cliente y recalculado")


def test_la_hora_en_curso_no_se_recalcula_mientras_llegan_datos(almacenes):
    pre, _ = almacenes
    ahora = datetime.now(timezone.utc)
    antes = ahora - timedelta(hours=3)
    pre.insert_spans([_llamada("vivo", uuid.uuid4().hex, antes, "x")])
    pre.insert_spans([_llamada("vivo", uuid.uuid4().hex, ahora, "y")])
    pendientes = {h for _, h in clickhouse.preagregados.pendientes(pre._client)}
    assert clickhouse.preagregados.hora_de(antes) in pendientes, "una hora acabada, sí"
    assert clickhouse.preagregados.hora_de(ahora) not in pendientes, "la que se está llenando, no"
    quieta = {h for _, h in clickhouse.preagregados.pendientes(pre._client, quieta_s=0)}
    assert clickhouse.preagregados.hora_de(ahora) in quieta, "cuando se queda quieta, sí"


class _ClienteQueApunta:
    """Lo justo de `clickhouse-connect` para ver qué se le pasa al insertar."""

    def __init__(self, ahora: datetime) -> None:
        self.ahora = ahora
        self.filas: dict[str, list] = {}

    def insert(self, tabla, filas, column_names, **_):
        self.filas[tabla] = filas

    def query(self, sql, parameters=None):
        # El driver devuelve `now64()` sin zona, aunque sea UTC.
        filas = [[self.ahora.replace(tzinfo=None)]] if "now64" in sql else []
        return type("R", (), {"result_rows": filas})()

    def command(self, *_, **__):
        pass


def test_las_horas_se_apuntan_en_utc_y_no_en_la_hora_del_ordenador():
    """El driver lee una fecha sin zona como hora LOCAL del ordenador que inserta. En un
    servidor en UTC da igual; en uno en Madrid, la hora y el cálculo quedaban dos horas
    antes. Ninguna hora llegaba a darse por calculada —el bucle de recalcular no acababa
    nunca— y las lecturas no casaban el cálculo con sus parciales. Por eso se le pasan
    con su zona: no depende de dónde corra."""
    from laplace_backend.storage import preagregados

    ahora = datetime(2026, 10, 3, 13, 46, 30, 726000, tzinfo=timezone.utc)
    cliente = _ClienteQueApunta(ahora)
    hora = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)

    preagregados.marcar(cliente, {("p", hora + timedelta(minutes=15))})
    assert cliente.filas["pre_sucias"] == [["p", hora]]
    assert cliente.filas["pre_sucias"][0][1].tzinfo is not None

    preagregados.recalcular(cliente, "p", hora)
    _, apuntada, calculado = cliente.filas["pre_horas"][0]
    assert (apuntada, calculado) == (hora, ahora)
    assert apuntada.tzinfo is not None and calculado.tzinfo is not None


def test_calcular_lo_pendiente_se_acaba(almacenes):
    """Calculada una hora, deja de estar pendiente. Con el desfase de arriba no dejaba
    de estarlo nunca, y `_calcular_todo` daba vueltas para siempre."""
    pre, _ = almacenes
    traza = uuid.uuid4().hex
    inicio = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    pre.insert_spans(
        [_llamada("fin", traza, inicio + timedelta(minutes=m), f"m{m}") for m in (5, 65, 125)]
    )
    vueltas = 0
    while pre.recalcular_preagregados(limite=500, quieta_s=0):
        vueltas += 1
        assert vueltas < 3, "lo calculado vuelve a salir como pendiente"
