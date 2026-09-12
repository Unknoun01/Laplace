"""El panel.

El panel existe por dos ideas, y estas pruebas son sobre esas dos ideas, no sobre que
salgan puntos en una gráfica:

1. **Coste por unidad de trabajo.** Que un 40 % más de gasto con un 40 % más de
   ejecuciones se lea como «normal», y el mismo gasto sin más ejecuciones como
   «revisar». La lectura en palabras es la funcionalidad; las series son el respaldo.
2. **Atribución de picos con datos reales.** Que cuando hay una causa se nombre, y que
   cuando no la hay **se diga que no la hay** en lugar de insinuar una correlación.

Y el criterio de D-073: donde no hay base, `None` con su motivo, nunca cero.

Casi todo corre contra la parte pura (`read_out`, `find_spikes`, `attribute`), que no
necesita almacén. Lo que sí lo necesita corre contra SQLite, que viene con Python, y la
paridad con ClickHouse se comprueba aparte.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage, ToolAttributes

from laplace_backend import panel
from laplace_backend.config import Settings
from laplace_backend.storage.base import Bucket, StepFacts, Window, WindowFacts
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _agg(traces: int, cost: float, tokens: int = 0, spans: int = 0) -> panel._Aggregate:
    return panel._Aggregate(
        traces=traces, cost_usd=cost, tokens=tokens, spans=spans or traces * 2
    )


# ---------------------------------------------------------------------------------
# La lectura en palabras: la idea número uno
# ---------------------------------------------------------------------------------


def test_mas_gasto_con_mas_ejecuciones_es_normal():
    """El caso que separa este panel de uno de totales.

    Un 40 % más de gasto con un 40 % más de ejecuciones no es una noticia, y un panel
    que lo pinte como una línea que sube está mintiendo por omisión.
    """
    lectura = panel.read_out(_agg(140, 14.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "normal"
    assert "más ejecuciones" in lectura.headline
    assert "trabajando más, no peor" in lectura.detail


def test_mas_gasto_sin_mas_ejecuciones_es_revisar():
    """Degradación: mismo trabajo, más caro."""
    lectura = panel.read_out(_agg(100, 14.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "revisar"
    assert lectura.headline == "Subida sin más ejecuciones: revisar."
    assert "no lo explica la demanda" in lectura.detail


def test_menos_ejecuciones_y_el_mismo_gasto_tambien_es_revisar():
    """El caso que un panel de totales no puede ver.

    El gasto total está plano —línea aburrida, nadie mira— pero cada ejecución cuesta
    el doble. Es una degradación escondida detrás de una caída de tráfico.
    """
    lectura = panel.read_out(_agg(50, 10.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "revisar"
    assert "el coste de cada una sube" in lectura.detail


def test_menos_gasto_por_menos_trabajo_no_se_vende_como_mejora():
    """Bajar porque hay menos tráfico no es haber arreglado nada, y se dice."""
    lectura = panel.read_out(_agg(50, 5.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "estable"
    assert "no una mejora" in lectura.detail


def test_abaratar_cada_ejecucion_si_es_una_mejora():
    lectura = panel.read_out(_agg(100, 5.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "mejora"


def test_un_cambio_pequeno_no_es_un_cambio():
    """Sin umbral, el panel gritaría «degradación» en cada despliegue."""
    lectura = panel.read_out(_agg(100, 10.4), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "estable"
    # Y no se dice un porcentaje de algo que no se mueve.
    assert "%" not in lectura.detail


def test_las_dos_cosas_a_la_vez_se_separan():
    lectura = panel.read_out(_agg(140, 20.0), _agg(100, 10.0), "7 días")
    assert lectura.verdict == "mixto"
    assert "La parte del volumen es trabajo real" in lectura.detail


def test_la_frase_concuerda_en_numero():
    """«las ejecuciones sube un 40 %» hace dudar de todo lo demás que dice la pantalla."""
    lectura = panel.read_out(_agg(140, 20.0), _agg(100, 10.0), "7 días")
    assert "las ejecuciones suben" in lectura.detail
    assert "las ejecuciones sube " not in lectura.detail


# ---------------------------------------------------------------------------------
# Sin base para comparar: `None` y el motivo, nunca cero (D-073)
# ---------------------------------------------------------------------------------


def test_sin_periodo_anterior_no_se_inventa_una_variacion():
    lectura = panel.read_out(_agg(100, 10.0), None, "3 horas")
    assert lectura.verdict == "sin-base"
    assert "no son cero" in lectura.detail


def test_un_periodo_anterior_casi_vacio_no_sirve_para_comparar():
    """La misma trampa que la proyección mensual sobre una hora, con otra cara.

    Comparar contra un periodo en el que el proyecto casi no existía da «el gasto sube
    un 193.100 %», y ese número quema la confianza en el resto de la pantalla.
    """
    vacio = [Bucket(start=AHORA + timedelta(hours=i)) for i in range(12)]
    assert "muy pocas para comparar" in panel.comparable(vacio)

    # Un periodo con ejecuciones pero sólo al final tampoco vale: el proyecto acababa
    # de empezar a enviar trazas.
    tardio = [Bucket(start=AHORA + timedelta(hours=i)) for i in range(12)]
    for b in tardio[-2:]:
        b.traces, b.cost_usd = 20, 1.0
    assert "casi vacío" in panel.comparable(tardio)

    # Y uno cubierto de verdad sí vale.
    lleno = [
        Bucket(start=AHORA + timedelta(hours=i), traces=10, cost_usd=1.0) for i in range(12)
    ]
    assert panel.comparable(lleno) == ""


def test_las_metricas_por_ejecucion_son_none_sin_ejecuciones():
    metricas = panel._per_execution_metrics(_agg(0, 0.0), None)
    for metrica in metricas:
        assert metrica.value is None
        assert metrica.unavailable, metrica.label
        assert metrica.change_ratio is None


def test_una_variacion_contra_cero_no_es_infinito():
    """«+∞ %» en una pantalla se lee como un error del producto, no como un dato."""
    assert panel._change(5.0, 0.0) is None
    assert panel._change(None, 1.0) is None


# ---------------------------------------------------------------------------------
# Picos: detección
# ---------------------------------------------------------------------------------


def _serie(unitarios: list[float], trazas: int = 10) -> list[Bucket]:
    return [
        Bucket(start=AHORA + timedelta(hours=i), traces=trazas, cost_usd=u * trazas)
        for i, u in enumerate(unitarios)
    ]


def test_un_pico_es_coste_por_ejecucion_no_gasto_del_tramo():
    """Una hora punta con el triple de tráfico no es un pico, es un martes.

    Si el criterio fuese el gasto del tramo, el panel señalaría cada hora de más
    demanda como una anomalía y nadie volvería a mirarlo.
    """
    ocupado = _serie([0.01] * 10)
    ocupado[5].traces *= 5
    ocupado[5].cost_usd *= 5  # cinco veces más gasto, mismo coste por ejecución
    indices, _, _ = panel.find_spikes(ocupado)
    assert indices == []

    caro = _serie([0.01] * 10)
    caro[5].cost_usd *= 5  # mismo tráfico, cinco veces más caro cada ejecución
    indices, base, _ = panel.find_spikes(caro)
    assert indices == [5]
    assert base == pytest.approx(0.01)


def test_sin_suficientes_tramos_no_se_habla_de_picos():
    """Con tres tramos, un pico es la mitad de la muestra: la mediana no es base de nada."""
    indices, _, motivo = panel.find_spikes(_serie([0.01, 0.01, 5.0]))
    assert indices == []
    assert "línea base" in motivo


def test_la_linea_base_es_la_mediana_y_no_la_media():
    """Una media contaminada por el propio pico sube con él y acaba escondiéndolo."""
    serie = _serie([0.01] * 9 + [10.0])
    indices, base, _ = panel.find_spikes(serie)
    assert base == pytest.approx(0.01)
    assert indices == [9]


def test_solo_se_investigan_los_picos_mas_caros():
    serie = _serie([0.01] * 6 + [1.0, 2.0, 3.0, 4.0])
    indices, _, _ = panel.find_spikes(serie)
    assert len(indices) == panel.MAX_SPIKES
    assert indices[0] == 9, "el más caro primero"


# ---------------------------------------------------------------------------------
# Picos: atribución. Con datos o con nada.
# ---------------------------------------------------------------------------------


def _facts(traces, cost, models=(), tools=(), steps=None) -> WindowFacts:
    return WindowFacts(
        traces=traces,
        cost_usd=cost,
        models=set(models),
        tools=set(tools),
        steps={k: StepFacts(cost_usd=v, calls=1) for k, v in (steps or {}).items()},
    )


def test_un_modelo_nuevo_en_las_trazas_es_una_causa():
    dentro = _facts(10, 5.0, models={"gpt-5.6-terra", "gpt-5.6-luna"})
    antes = _facts(100, 1.0, models={"gpt-5.6-luna"})
    causas, sin_atribuir = panel.attribute(dentro, antes, 4.0, None)
    assert sin_atribuir == ""
    assert any(c.kind == "modelo_nuevo" and "gpt-5.6-terra" in c.text for c in causas)


def test_una_herramienta_nueva_tambien():
    dentro = _facts(10, 5.0, tools={"buscar", "pagar"})
    antes = _facts(100, 1.0, tools={"buscar"})
    causas, _ = panel.attribute(dentro, antes, 4.0, None)
    assert any(c.kind == "herramienta_nueva" and "pagar" in c.text for c in causas)


def test_un_paso_que_se_lleva_el_sobrecoste_se_nombra_con_su_parte():
    """Dos pasos suben, uno se lleva la mayor parte: se nombra ése y con su cifra."""
    dentro = _facts(10, 5.0, steps={"resumir": 3.0, "clasificar": 2.0})
    antes = _facts(100, 5.0, steps={"resumir": 0.5, "clasificar": 4.5})
    causas, _ = panel.attribute(dentro, antes, 4.4, None)
    paso = next(c for c in causas if c.kind == "paso_disparado")
    assert "resumir" in paso.text and "% del sobrecoste" in paso.text
    assert "por encima de lo que costaba antes" in paso.evidence


def test_un_paso_que_se_lleva_casi_todo_no_dice_un_porcentaje_absurdo():
    """La parte de un paso puede pasar del 100 % del sobrecoste neto del tramo.

    Ocurre cuando a la vez otro paso se abarata: el exceso de uno es mayor que el neto.
    Es cierto y se lee como un error de cálculo, así que por encima del 95 % se dice
    con palabras. Y la frase tiene que quedar bien escrita, no «todo del sobrecoste».
    """
    dentro = _facts(10, 5.0, steps={"resumir": 5.0, "clasificar": 0.0})
    antes = _facts(10, 1.0, steps={"resumir": 0.1, "clasificar": 0.9})
    causas, _ = panel.attribute(dentro, antes, 4.0, None)
    paso = next(c for c in causas if c.kind.startswith("paso"))
    assert "prácticamente todo el sobrecoste" in paso.text
    assert "%" not in paso.text
    assert "todo del sobrecoste" not in paso.text


def test_un_paso_que_solo_aporta_su_parte_no_se_senala():
    """Si tres pasos suben a la vez, ninguno es «la causa»: eso es reparto, no causa."""
    dentro = _facts(10, 3.0, steps={"a": 1.0, "b": 1.0, "c": 1.0})
    antes = _facts(10, 0.3, steps={"a": 0.1, "b": 0.1, "c": 0.1})
    causas, sin_atribuir = panel.attribute(dentro, antes, 2.7, None)
    assert [c for c in causas if c.kind.startswith("paso")] == []
    assert sin_atribuir


def test_sin_nada_nuevo_se_dice_que_no_se_identifica_la_causa():
    """La regla que impide que esto se convierta en un generador de correlaciones."""
    dentro = _facts(10, 5.0, models={"m"}, tools={"t"}, steps={"a": 5.0})
    antes = _facts(100, 40.0, models={"m"}, tools={"t"}, steps={"a": 40.0})
    causas, sin_atribuir = panel.attribute(dentro, antes, 0.0, None)
    assert causas == []
    assert "No identificamos la causa" in sin_atribuir


# ---------------------------------------------------------------------------------
# Contra el almacén, de punta a punta
# ---------------------------------------------------------------------------------


def _traza(project, momento, *, modelo, coste, tokens, paso, ms=1000) -> list[Span]:
    trace = uuid.uuid4().hex
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name="responder",
        type="agent",
        status="ok",
        start_time=momento,
        end_time=momento + timedelta(milliseconds=ms),
        duration_ms=float(ms),
    )
    hijo = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        parent_span_id=raiz.span_id,
        project_id=project,
        name="chat",
        type="llm",
        status="ok",
        start_time=momento,
        end_time=momento + timedelta(milliseconds=ms // 2),
        duration_ms=ms / 2,
        step_key=paso,
        step_label=paso,
    )
    hijo.llm = LLMAttributes(
        system="openai",
        request_model=modelo,
        usage=TokenUsage(input_tokens=tokens, output_tokens=tokens // 10),
        cost=Cost(input_usd=coste * 0.8, output_usd=coste * 0.2, total_usd=coste),
    )
    return [raiz, hijo]


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    almacen = SQLiteStore(tmp_path / "laplace.db")
    almacen.migrate()
    return almacen


@pytest.fixture
def window() -> Window:
    ahora = datetime.now(timezone.utc)
    return Window(since=ahora - timedelta(days=3), until=ahora, days=3)


def _proyecto_con_pico(store: SQLiteStore, project: str) -> datetime:
    """Seis días de tráfico normal y una hora en la que alguien prueba un modelo caro."""
    ahora = datetime.now(timezone.utc)
    spans: list[Span] = []
    for h in range(144):
        spans += _traza(
            project,
            ahora - timedelta(hours=144 - h),
            modelo="gpt-5.6-luna",
            coste=0.00016,
            tokens=500,
            paso="paso-responder",
        )
    pico = ahora - timedelta(hours=10)
    for i in range(6):
        spans += _traza(
            project,
            pico + timedelta(minutes=i * 8),
            modelo="gpt-5.6-terra",
            coste=0.0496,
            tokens=20_000,
            paso="paso-experimento",
        )
    store.insert_spans(spans)
    return pico


def test_el_panel_completo_encuentra_el_pico_y_lo_atribuye(store, window):
    project = "con-pico"
    _proyecto_con_pico(store, project)
    vista = panel.build(store, project, window)

    # El escenario mete ejecuciones nuevas junto con el modelo caro, así que puede
    # leerse como «revisar» o como «mixto» según cuánto suba el volumen. Lo que no
    # puede fallar —y es lo que se comprueba— es que señale el coste por ejecución.
    assert vista.reading.verdict in ("revisar", "mixto")
    assert "el coste de cada una sube" in vista.reading.detail
    assert vista.has_previous is True

    assert len(vista.spikes) == 1
    pico = vista.spikes[0]
    assert pico.times_baseline > panel.SPIKE_FACTOR
    assert pico.excess_usd > 0
    assert pico.unattributed == ""
    tipos = {c.kind for c in pico.causes}
    assert "modelo_nuevo" in tipos
    assert any("gpt-5.6-terra" in c.text for c in pico.causes)

    # Y el enlace a las trazas responsables es el rango exacto del pico.
    assert pico.traces_query["since"] == pico.start.isoformat()
    assert pico.traces_query["until"] == pico.end.isoformat()
    assert pico.traces_query["project_id"] == project


def test_los_tramos_empiezan_en_hora_redonda(store, window):
    """«de 06:00 a 12:00» se entiende; «de 04:32 a 10:32» no lo relaciona nadie."""
    _proyecto_con_pico(store, "reloj")
    vista = panel.build(store, "reloj", window)
    for tramo in vista.buckets:
        minutos = tramo.start.hour * 60 + tramo.start.minute
        assert tramo.start.second == 0 and tramo.start.microsecond == 0
        assert minutos % vista.bucket_minutes == 0


def test_un_proyecto_recien_instalado_no_finge_un_panel(store, window):
    """El caso que importa: minutos de datos, y el panel lo dice en vez de pintar ceros."""
    project = "recien"
    ahora = datetime.now(timezone.utc)
    spans: list[Span] = []
    for i in range(4):
        spans += _traza(
            project,
            ahora - timedelta(minutes=10 - i * 2),
            modelo="gpt-5.6-luna",
            coste=0.001,
            tokens=500,
            paso="paso-responder",
        )
    store.insert_spans(spans)
    vista = panel.build(store, project, window)

    assert vista.has_previous is False
    assert vista.comparison_unavailable
    assert vista.reading.verdict == "sin-base"

    # Lo observado sí está: no se puede comparar, pero sí medir.
    coste = next(m for m in vista.per_execution if m.label == "Coste por ejecución")
    assert coste.value == pytest.approx(0.001)
    assert coste.change_ratio is None
    assert coste.previous is None

    # Y no se habla de picos sin línea base.
    assert vista.spikes == []
    assert "línea base" in vista.spikes_unavailable


def test_un_proyecto_sin_datos_no_revienta(store, window):
    vista = panel.build(store, "no-existe", window)
    assert vista.reading.verdict == "sin-base"
    assert all(m.value is None for m in vista.per_execution)
    assert vista.spikes == []


def test_los_tramos_vacios_existen_y_valen_none(store, window):
    """Un hueco es información: dos horas sin ejecuciones son dos horas sin ejecuciones.

    Y su coste por ejecución no es cero, es que no hay ejecuciones. Pintarlo como cero
    dibujaría una caída del coste que nadie ha tenido.
    """
    project = "con-huecos"
    ahora = datetime.now(timezone.utc)
    store.insert_spans(
        _traza(
            project,
            ahora - timedelta(hours=1),
            modelo="gpt-5.6-luna",
            coste=0.001,
            tokens=100,
            paso="p",
        )
    )
    vista = panel.build(store, project, window)
    vacios = [b for b in vista.buckets if b.traces == 0]
    assert vacios, "la serie tiene que cubrir la ventana entera, huecos incluidos"
    assert all(b.cost_per_trace_usd is None for b in vacios)
    assert all(b.tokens_per_trace is None for b in vacios)


def test_el_ancho_del_tramo_depende_del_rango():
    assert panel.bucket_minutes_for(1) == 60
    assert panel.bucket_minutes_for(7) == 360
    assert panel.bucket_minutes_for(30) == 1440
    # Y siempre deja una gráfica legible: ni cuatro puntos ni doscientos.
    for dias in (1, 2, 7, 14, 30, 90):
        puntos = dias * 1440 / panel.bucket_minutes_for(dias)
        assert 20 <= puntos <= 100, dias


# ---------------------------------------------------------------------------------
# Los dos almacenes dicen lo mismo
# ---------------------------------------------------------------------------------


@pytest.fixture
def clickhouse():
    from laplace_backend.storage.clickhouse import ClickHouseStore

    almacen = ClickHouseStore(Settings())
    if not almacen.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    almacen.migrate()
    return almacen


def test_los_dos_almacenes_pintan_el_mismo_panel(store, clickhouse, window):
    """La serie y la atribución son SQL nuevo, así que son sitio nuevo para divergir.

    `intDiv(dateDiff(...))` contra `CAST((julianday(...)) AS INTEGER)` tienen que caer
    en el mismo tramo, y `max(status = 'error')` por traza tiene que contar igual. Si
    esto se rompe, el mismo pico existe en la nube y no en local.
    """
    project = f"panel-paridad-{uuid.uuid4().hex[:8]}"
    ahora = datetime.now(timezone.utc)
    spans: list[Span] = []
    for h in range(144):
        spans += _traza(
            project,
            ahora - timedelta(hours=144 - h),
            modelo="gpt-5.6-luna",
            coste=0.00016,
            tokens=500,
            paso="paso-responder",
        )
    for i in range(6):
        spans += _traza(
            project,
            ahora - timedelta(hours=10) + timedelta(minutes=i * 8),
            modelo="gpt-5.6-terra",
            coste=0.0496,
            tokens=20_000,
            paso="paso-experimento",
        )
    store.insert_spans(spans)
    clickhouse.insert_spans(spans)
    try:
        aqui = panel.build(store, project, window)
        alli = panel.build(clickhouse, project, window)

        assert aqui.reading.verdict == alli.reading.verdict
        assert aqui.reading.headline == alli.reading.headline
        assert len(aqui.buckets) == len(alli.buckets)
        for a, b in zip(aqui.buckets, alli.buckets, strict=True):
            assert a.start == b.start
            assert a.traces == b.traces
            assert a.cost_usd == pytest.approx(b.cost_usd, rel=1e-9)
            assert a.is_spike == b.is_spike

        assert len(aqui.spikes) == len(alli.spikes) == 1
        assert [c.text for c in aqui.spikes[0].causes] == [
            c.text for c in alli.spikes[0].causes
        ]

        for a, b in zip(aqui.per_execution, alli.per_execution, strict=True):
            assert a.label == b.label
            assert (a.value is None) == (b.value is None)
            if a.value is not None:
                assert a.value == pytest.approx(b.value, rel=1e-9)
    finally:
        clickhouse.delete_project(project)


def test_la_duracion_por_ejecucion_es_de_traza_no_suma_de_spans(store, window):
    """Los spans se solapan: sumarlos daría un número que no es la espera de nadie."""
    project = "duracion"
    ahora = datetime.now(timezone.utc)
    spans = _traza(
        project,
        ahora - timedelta(hours=2),
        modelo="gpt-5.6-luna",
        coste=0.001,
        tokens=100,
        paso="p",
        ms=1000,
    )
    # Tres hijos solapados de medio segundo cada uno dentro de la misma traza.
    for _ in range(3):
        gemelo = spans[1].model_copy(deep=True)
        gemelo.span_id = uuid.uuid4().hex[:16]
        spans.append(gemelo)
    store.insert_spans(spans)

    vista = panel.build(store, project, window)
    duracion = next(m for m in vista.per_execution if m.label == "Duración por ejecución")
    # Un segundo de principio a fin, no los 2,5 s que suman los spans por separado.
    assert duracion.value == pytest.approx(1000, rel=0.05)


def test_una_traza_con_error_se_cuenta_una_vez(store, window):
    project = "errores"
    ahora = datetime.now(timezone.utc)
    spans = _traza(
        project, ahora - timedelta(hours=2), modelo="gpt-5.6-luna", coste=0.001,
        tokens=100, paso="p",
    )
    for s in spans:
        s.status = "error"
    store.insert_spans(spans)
    tramos = store.timeseries(project, window, panel.bucket_minutes_for(window.days))
    assert sum(b.error_traces for b in tramos) == 1


def test_las_herramientas_se_ven_en_los_hechos_del_tramo(store):
    """La atribución por herramienta nueva depende de esto, y es fácil de romper."""
    project = "hechos"
    ahora = datetime.now(timezone.utc)
    spans = _traza(
        project, ahora - timedelta(hours=1), modelo="gpt-5.6-luna", coste=0.001,
        tokens=100, paso="p",
    )
    herramienta = spans[0].model_copy(deep=True)
    herramienta.span_id = uuid.uuid4().hex[:16]
    herramienta.parent_span_id = spans[0].span_id
    herramienta.type = "tool"
    herramienta.name = "buscar_tarifa"
    herramienta.tool = ToolAttributes(name="buscar_tarifa", arguments={})
    spans.append(herramienta)
    store.insert_spans(spans)

    hechos = store.window_facts(project, ahora - timedelta(days=1), ahora)
    assert "buscar_tarifa" in hechos.tools
    assert "gpt-5.6-luna" in hechos.models
    assert hechos.traces == 1
