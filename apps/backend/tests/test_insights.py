"""Pruebas del motor de detección.

Lo que importa aquí no es que las reglas disparen, es que **las cifras sean honestas**:
que no se invente dinero donde no lo hay, que no se cuente dos veces el mismo ahorro, y
que no se prometa un ahorro mayor que el gasto.

Van contra ClickHouse porque las reglas son consultas; se saltan solas si no hay ninguno
escuchando.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage, ToolAttributes

from laplace_backend import insights
from laplace_backend.config import Settings
from laplace_backend.storage.base import RepeatedGroup, Window
from laplace_backend.storage.clickhouse import ClickHouseStore

BASE = datetime.now(timezone.utc) - timedelta(hours=1)


@pytest.fixture(scope="module")
def store() -> ClickHouseStore:
    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; se omiten las pruebas del motor")
    store.migrate()
    return store


@pytest.fixture(scope="module")
def window() -> Window:
    ahora = datetime.now(timezone.utc)
    return Window(since=ahora - timedelta(days=7), until=ahora + timedelta(minutes=5), days=7)


def _span(project, trace, span_id, name, type_, offset_s, **kwargs) -> Span:
    start = BASE + timedelta(seconds=offset_s)
    return Span(
        span_id=span_id,
        trace_id=trace,
        parent_span_id=kwargs.pop("parent", None),
        project_id=project,
        name=name,
        type=type_,
        status="ok",
        start_time=start,
        end_time=start + timedelta(milliseconds=kwargs.pop("ms", 200)),
        duration_ms=kwargs.pop("duration_ms", 200.0),
        dedup_hash=kwargs.pop("dedup_hash", ""),
        **kwargs,
    )


def _llm(model: str, tok_in: int, tok_out: int, cost: float) -> LLMAttributes:
    return LLMAttributes(
        system="openai",
        request_model=model,
        response_model=model,
        usage=TokenUsage(input_tokens=tok_in, output_tokens=tok_out),
        cost=Cost(input_usd=cost / 2, output_usd=cost / 2, total_usd=cost),
    )


# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_con_bucle_de_tool(store: ClickHouseStore):
    """Cinco ejecuciones que repiten una herramienta. Las tools no gastan tokens."""
    project = f"test-tool-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(5):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, f"{t}0" * 8, "agente", "agent", t * 10))
        for i in range(4):
            s = _span(
                project,
                trace,
                f"{t}{i + 1}" * 8,
                "buscar",
                "tool",
                t * 10 + i,
                parent=f"{t}0" * 8,
                dedup_hash="hash-buscar",
                duration_ms=150.0,
            )
            s.tool = ToolAttributes(name="buscar", arguments={"q": "vuelos"})
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_un_bucle_de_herramientas_no_inventa_dinero(store, window, proyecto_con_bucle_de_tool):
    findings = insights.detect(store, proyecto_con_bucle_de_tool, window)
    bucle = next(f for f in findings if f.kind == "repeticion")

    # Repetir una herramienta no gasta tokens: el ahorro en dinero tiene que ser cero.
    assert bucle.window_waste_usd == 0
    assert bucle.costs_money is False
    # La proyección de cero sigue siendo cero, y sin base para proyectar es `None`
    # (D-073). Lo que no puede pasar en ninguno de los dos casos es que aparezca
    # dinero donde no lo hay.
    assert bucle.monthly_saving_usd in (None, 0.0)
    # Y lo que sí se pierde, tiempo, se cuenta: 3 repeticiones de más x 5 trazas x 150 ms.
    assert bucle.window_waste_ms == pytest.approx(3 * 5 * 150, rel=0.01)
    assert "gasta tokens de más" in bucle.summary


def test_el_heroe_no_promete_ahorrar_mas_de_lo_que_se_gasta(
    store, window, proyecto_con_bucle_de_tool
):
    """La promesa no puede pasar del gasto, se proyecte o no.

    Se comprueba sobre lo observado, que existe siempre, y además sobre la proyección
    cuando la hay: son las dos formas de enseñar la misma barra de reparto (D-073).
    """
    resumen = insights.overview(store, proyecto_con_bucle_de_tool, window)
    assert resumen.window_avoidable_usd <= resumen.window_cost_usd
    assert resumen.window_necessary_usd >= 0
    if resumen.projected:
        assert resumen.monthly_avoidable_usd <= resumen.monthly_cost_usd
        assert resumen.monthly_necessary_usd >= 0


# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_con_reintentos(store: ClickHouseStore):
    """Un paso caro que además se reintenta: las dos reglas se pisan si no se descuenta.

    Diez ejecuciones. En cada una, `clasificar` llama a gpt-5.6-terra tres veces con el mismo
    prompt (200 tokens de entrada, 10 de salida, $0,001 cada llamada).
    """
    project = f"test-caro-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(10):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, f"a{t:03d}".ljust(16, "0"), "agente", "agent", t))
        for i in range(3):
            s = _span(
                project,
                trace,
                f"b{t:03d}{i}".ljust(16, "0"),
                "clasificar",
                "llm",
                t,
                parent=f"a{t:03d}".ljust(16, "0"),
                dedup_hash="hash-clasificar",
            )
            s.llm = _llm("gpt-5.6-terra", 200, 10, 0.001)
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_las_reglas_no_cuentan_dos_veces_el_mismo_ahorro(store, window, proyecto_con_reintentos):
    """Regresión: la regla del modelo caro contaba también las llamadas repetidas.

    30 llamadas a $0,001 = $0,03 gastados. La repetición se lleva 20 de esas llamadas
    ($0,02). A la regla del modelo caro sólo le pueden quedar las 10 restantes, así que
    su ahorro no puede acercarse al coste total.
    """
    findings = insights.detect(store, proyecto_con_reintentos, window)
    resumen = insights.overview(store, proyecto_con_reintentos, window)

    repeticion = next(f for f in findings if f.kind == "repeticion")
    modelo = next(f for f in findings if f.kind == "modelo_caro")

    # La repetición se lleva el coste íntegro de las copias sobrantes.
    assert repeticion.window_waste_usd == pytest.approx(0.02, rel=0.02)
    assert repeticion.costs_money is True

    # El modelo caro trabaja sólo sobre la llamada legítima de cada ejecución: como
    # mucho puede ahorrar lo que cuestan esas diez, nunca lo que cuestan las treinta.
    assert modelo.window_waste_usd < 0.01

    # Y la suma sigue sin pasarse del gasto real. Se comprueba sobre el dinero ya
    # gastado, que es donde vive de verdad el invariante: existe con cualquier cantidad
    # de datos, mientras que la proyección puede no existir (D-073).
    total = sum(f.window_waste_usd for f in findings)
    assert total == pytest.approx(resumen.window_avoidable_usd, rel=0.001)
    assert resumen.window_avoidable_usd <= resumen.window_cost_usd


@pytest.fixture(scope="module")
def proyecto_con_reintentos_repartido(store: ClickHouseStore):
    """Los mismos reintentos, pero repartidos en tres días de calendario.

    Mismo dinero, misma patología: lo único que cambia es que ahora **sí** hay base
    para proyectar. Sirve para comprobar que el descuento del solape sigue en pie por
    el otro camino, el que se activa cuando el proyecto lleva días enviando.
    """
    project = f"test-caro-largo-{uuid.uuid4().hex[:8]}"
    inicio = datetime.now(timezone.utc) - timedelta(days=3)
    spans: list[Span] = []
    for t in range(10):
        trace = uuid.uuid4().hex
        momento = inicio + timedelta(hours=t * 7)
        raiz = _span(project, trace, f"c{t:03d}".ljust(16, "0"), "agente", "agent", 0)
        raiz.start_time = momento
        raiz.end_time = momento + timedelta(milliseconds=200)
        spans.append(raiz)
        for i in range(3):
            s = _span(
                project,
                trace,
                f"d{t:03d}{i}".ljust(16, "0"),
                "clasificar",
                "llm",
                0,
                parent=raiz.span_id,
                dedup_hash="hash-clasificar",
            )
            s.start_time = momento + timedelta(seconds=i)
            s.end_time = s.start_time + timedelta(milliseconds=200)
            s.llm = _llm("gpt-5.6-terra", 200, 10, 0.001)
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_el_descuento_del_solape_aguanta_tambien_al_proyectar(
    store, window, proyecto_con_reintentos_repartido
):
    """La batería del doble conteo, ahora por el camino de la proyección (D-064, D-073).

    Con datos suficientes las cifras mensuales existen, y el invariante tiene que
    cumplirse también ahí: la proyección multiplica a todos por lo mismo, así que si
    la suma de ahorros cuadraba en la ventana tiene que cuadrar en el mes. Si algún día
    alguien proyecta un hallazgo con una base distinta de la del héroe, esto lo caza.
    """
    resumen = insights.overview(store, proyecto_con_reintentos_repartido, window)
    assert resumen.projected is True, "el escenario existe para probar la proyección"

    total = sum(f.monthly_saving_usd for f in resumen.findings)
    assert total == pytest.approx(resumen.monthly_avoidable_usd, rel=0.001)
    assert resumen.monthly_avoidable_usd <= resumen.monthly_cost_usd

    # Y la proporción evitable es la misma se mire donde se mire: es la comprobación
    # de que gasto y ahorro han salido de la misma base.
    assert resumen.monthly_avoidable_usd / resumen.monthly_cost_usd == pytest.approx(
        resumen.window_avoidable_usd / resumen.window_cost_usd, rel=1e-9
    )

    # El reparto entre las dos reglas no cambia por proyectar: cada hallazgo mensual
    # es su propio gasto observado extrapolado con los mismos días.
    for hallazgo in resumen.findings:
        if hallazgo.costs_money:
            assert hallazgo.monthly_saving_usd == pytest.approx(
                hallazgo.window_waste_usd / resumen.observed_days * 30, rel=1e-6
            )


def test_la_ficha_dice_lo_mismo_que_la_tarjeta(store, window, proyecto_con_reintentos):
    """Si el detalle recalculase sin descontar el solape, diría otra cifra que el inicio."""
    findings = insights.detect(store, proyecto_con_reintentos, window)
    for finding in findings:
        detalle = insights.detail(store, proyecto_con_reintentos, window, finding.id)
        assert detalle is not None, finding.id
        assert detalle.monthly_saving_usd == finding.monthly_saving_usd
        assert detalle.window_waste_usd == pytest.approx(finding.window_waste_usd)
        assert detalle.title == finding.title
        # La consulta que se enseña es la que se ejecuta, no una copia.
        assert detalle.detection_query.startswith("SELECT")


def test_un_hallazgo_que_ya_no_se_da_devuelve_none(store, window, proyecto_con_reintentos):
    assert insights.detail(store, proyecto_con_reintentos, window, "repeticion:no-existe") is None
    assert insights.detail(store, proyecto_con_reintentos, window, "invento:lo-que-sea") is None


def test_un_proyecto_sin_datos_no_da_hallazgos(store, window):
    assert insights.detect(store, f"vacio-{uuid.uuid4().hex[:8]}", window) == []


# ---------------------------------------------------------------------------------
# Regla 3 — contexto fijo, ahora expresada en el modelo de caché real
# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_sin_cache(store: ClickHouseStore):
    """Cuatro ejecuciones que reenvían 20.000 tokens de instrucciones en cada llamada."""
    project = f"test-cache-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, uuid.uuid4().hex[:16], "agente", "agent", t * 10))
        for i in range(4):
            s = _span(
                project,
                trace,
                uuid.uuid4().hex[:16],
                "razonar",
                "llm",
                t * 10 + i,
                dedup_hash=uuid.uuid4().hex[:16],  # entradas distintas: no es repetición
            )
            s.llm = LLMAttributes(
                system="anthropic",
                request_model="claude-sonnet-5",
                response_model="claude-sonnet-5",
                usage=TokenUsage(input_tokens=20_000 + i * 100, output_tokens=200),
                cost=Cost(total_usd=(20_000 * 2.0 + 200 * 10.0) / 1e6),
            )
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def _contexto(store, project, window):
    hallazgos = [f for f in insights.detect(store, project, window) if f.kind == "contexto_fijo"]
    return hallazgos[0] if hallazgos else None


def test_el_ahorro_de_la_cache_descuenta_lo_que_cuesta_escribirla(
    store, window, proyecto_sin_cache
):
    """No basta con decir «pásalo a caché»: escribirla cuesta 1,25x la entrada.

    Antes se anunciaba la diferencia entera entre tarifa de entrada y tarifa de lectura,
    que es un ahorro que el propio motor de precios sabe que no llega entero.
    """
    hallazgo = _contexto(store, proyecto_sin_cache, window)
    assert hallazgo is not None, "la regla del contexto fijo debería dispararse"

    tabla = insights.get_price_table()
    precio = tabla.lookup("claude-sonnet-5")
    # 16 llamadas en 4 trazas: 12 encontrarían la caché caliente, 4 la escriben.
    bruto = 20_000 * 12 * (precio.input - precio.cached_input) / 1e6
    escritura = 20_000 * 4 * (precio.cache_write - precio.input) / 1e6

    assert hallazgo.window_waste_usd == pytest.approx(bruto - escritura, rel=1e-6)
    assert hallazgo.window_waste_usd < bruto, "el coste de escribir la caché no se descuenta"


def test_la_ficha_del_contexto_fijo_ensena_las_dos_patas_del_calculo(
    store, window, proyecto_sin_cache
):
    hallazgo = _contexto(store, proyecto_sin_cache, window)
    detalle = insights.detail(store, proyecto_sin_cache, window, hallazgo.id)
    assert detalle is not None
    assert "escritura de caché" in detalle.savings_calculation
    assert "conservadora" in detalle.savings_note


@pytest.fixture(scope="module")
def proyecto_con_cache(store: ClickHouseStore):
    """El mismo agente, ya cacheando. La regla no tiene nada que recomendar."""
    project = f"test-cacheon-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        for i in range(4):
            s = _span(
                project, trace, uuid.uuid4().hex[:16], "razonar", "llm", t * 10 + i,
                dedup_hash=uuid.uuid4().hex[:16],
            )
            s.llm = LLMAttributes(
                system="anthropic",
                request_model="claude-sonnet-5",
                response_model="claude-sonnet-5",
                usage=TokenUsage(
                    input_tokens=20_000,
                    output_tokens=200,
                    cached_input_tokens=20_000 if i else 0,
                    cache_write_tokens=0 if i else 20_000,
                ),
                # Estos spans se insertan ya tarifados, como si vinieran de la
                # ingesta: el ahorro por caché es el que habría calculado el motor.
                cost=Cost(
                    total_usd=0.01,
                    cache_saving_usd=(20_000 * (2.0 - 0.2) / 1e6) if i else 0.0,
                ),
            )
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_a_quien_ya_usa_la_cache_no_se_le_recomienda_activarla(store, window, proyecto_con_cache):
    """La promesa de siempre, que no cambia: a quien ya cachea no se le dice que cachee.

    Lo que sí cambió es la conclusión. Antes el hallazgo desaparecía, y con él
    desaparecía un gasto real: **leer de caché se cobra** —un 10 % de la entrada en
    Anthropic— y un prefijo de 20.000 tokens leído once veces sigue siendo una parte
    gorda de la factura. Ahora el hallazgo sale, pero propone lo único que puede bajar
    esa cifra: mandar menos contexto, no cachearlo mejor (D-111).
    """
    hallazgo = _contexto(store, proyecto_con_cache, window)
    assert hallazgo is not None, "el prefijo fijo sigue costando dinero aunque esté cacheado"
    texto = hallazgo.summary.lower()
    assert "cachearlos ahorraría" not in texto, "no se le puede proponer lo que ya hace"
    assert "leerla también se cobra" in texto
    assert "mandando menos" in texto
    # Y la cifra es exactamente lo que cuestan esas lecturas: los tokens servidos de
    # caché que ve el almacén, a la tarifa de lectura de claude-sonnet-5 (0,20 $/1M).
    # Se saca del propio almacén y no de una cuenta a mano, para que siga valiendo si
    # alguien cambia el tamaño del fixture.
    uso = next(
        u
        for u in store.model_usage(proyecto_con_cache, window, min_calls=1)
        if u.cached_input_tokens
    )
    assert hallazgo.window_waste_usd == pytest.approx(
        uso.cached_input_tokens * 0.2 / 1e6, rel=0.02
    )


def test_lo_que_la_cache_ya_ahorra_se_mide_y_llega_al_heroe(store, window, proyecto_con_cache):
    """Dinero medido, no proyectado: sale de tokens reales y de la tabla de tarifas."""
    resumen = insights.overview(store, proyecto_con_cache, window)
    # 12 llamadas × 20.000 tokens leídos, de 2 $/1M a 0,20 $/1M.
    assert resumen.window_cache_saving_usd == pytest.approx(12 * 20_000 * 1.8 / 1e6, rel=1e-6)


@pytest.fixture(scope="module")
def proyecto_caro_y_sin_cache(store: ClickHouseStore):
    """Un paso que dispara las reglas 2 y 3 a la vez: modelo caro Y contexto sin caché.

    Respuestas cortísimas (la regla del modelo caro pide salida media pequeña) sobre un
    prompt fijo enorme (la del contexto fijo pide un suelo de entrada grande).
    """
    project = f"test-solape-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        for i in range(5):
            s = _span(
                project, trace, uuid.uuid4().hex[:16], "clasificar", "llm", t * 10 + i,
                dedup_hash=uuid.uuid4().hex[:16],
            )
            s.llm = LLMAttributes(
                system="openai",
                request_model="gpt-5.6-terra",
                response_model="gpt-5.6-terra",
                usage=TokenUsage(input_tokens=20_000, output_tokens=20),
                cost=Cost(total_usd=(20_000 * 2.0 + 20 * 12.0) / 1e6),
            )
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_dos_reglas_sobre_el_mismo_paso_no_suman_el_mismo_dinero_dos_veces(
    store, window, proyecto_caro_y_sin_cache
):
    """Cambiar de modelo y activar la caché son arreglos que se aplican encadenados.

    Si la regla de la caché siguiera tarifando sobre el modelo caro, los dos hallazgos
    juntos prometerían más ahorro del que el paso llega a costar. Aquí se comprueba lo
    único que de verdad importa: que la suma no pase del gasto real.
    """
    hallazgos = insights.detect(store, proyecto_caro_y_sin_cache, window)
    tipos = {f.kind for f in hallazgos}
    assert {"modelo_caro", "contexto_fijo"} <= tipos, "el escenario tiene que disparar las dos"

    resumen = store.summarize_window(proyecto_caro_y_sin_cache, window)
    prometido = sum(f.window_waste_usd for f in hallazgos)
    assert prometido <= resumen.total_cost_usd, (
        f"las reglas prometen ahorrar {prometido:.6f} de un gasto de "
        f"{resumen.total_cost_usd:.6f}: se está contando dinero dos veces"
    )


def test_la_cache_se_tarifa_sobre_el_modelo_que_recomendamos(
    store, window, proyecto_caro_y_sin_cache
):
    """Y la ficha lo dice, en vez de dejar al usuario sumando dos cifras incompatibles."""
    hallazgo = _contexto(store, proyecto_caro_y_sin_cache, window)
    detalle = insights.detail(store, proyecto_caro_y_sin_cache, window, hallazgo.id)
    assert "gpt-5.6-luna" in detalle.savings_calculation
    assert "contar dos veces" in detalle.savings_calculation


# ---------------------------------------------------------------------------------
# Batería del doble conteo
#
# El mismo fallo —prometer dos veces el mismo dinero— ha aparecido ya por tres caminos
# distintos: dos reglas sobre los mismos tokens, dos reglas sobre el mismo paso, y el
# cruce del descuento quedándose sin pareja al cambiar la clave de agrupación. Cada
# camino deja aquí su caso. Los casos se AÑADEN; ninguno sustituye a otro.
# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_un_paso_dos_entradas(store: ClickHouseStore):
    """Un paso que repite DOS entradas distintas, tres veces cada una, por ejecución.

    Genera dos `dedup_hash` bajo un mismo `step_key`. El mapa de descuento tiene que
    sumar los dos grupos: quedarse con el último —que es lo que hacía el diccionario
    por comprensión— deja la mitad de las repeticiones sin descontar, y la regla del
    modelo caro vuelve a cobrar por ellas.
    """
    project = f"test-dos-hashes-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        for entrada in ("A", "B"):
            for i in range(3):
                span = _span(
                    project, trace, uuid.uuid4().hex[:16],
                    # El nombre que pone la auto-instrumentación: el mismo para todo.
                    "chat gpt-5.6-terra", "llm", t * 10 + i,
                    dedup_hash=f"hash-{entrada}",
                    step_key="paso-extraer",
                    step_label="extraer_datos",
                )
                span.llm = _llm("gpt-5.6-terra", 100, 10, 0.001)
                spans.append(span)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_varios_hashes_del_mismo_paso_se_descuentan_todos(
    store, window, proyecto_un_paso_dos_entradas
):
    """24 llamadas a $0,001: 8 legítimas (dos entradas × cuatro ejecuciones) y 16 de más.

    La repetición se lleva las 16. A la regla del modelo caro sólo le pueden quedar 8.
    """
    findings = insights.detect(store, proyecto_un_paso_dos_entradas, window)
    resumen = store.summarize_window(proyecto_un_paso_dos_entradas, window)

    # Una sola tarjeta: son dos entradas repetidas pero un único paso que se repite,
    # y es lo que el usuario arregla de una vez. El dinero es el de las dos.
    repeticiones = [f for f in findings if f.kind == "repeticion"]
    assert len(repeticiones) == 1
    assert repeticiones[0].window_waste_usd == pytest.approx(0.016, rel=0.02)

    # Aquí la cifra tiene que ser exacta, no «menor que». Con el mapa sobrescribiendo
    # en vez de sumando, el ahorro salía de 1.600 tokens en vez de 800 y aun así se
    # quedaba por debajo del gasto total: un tope holgado no habría notado nada.
    modelo = next(f for f in findings if f.kind == "modelo_caro")
    entrada, salida = 8 * 100, 8 * 10  # sólo las ocho llamadas legítimas
    caro = (entrada * 2.0 + salida * 12.0) / 1e6
    barato = (entrada * 0.2 + salida * 1.2) / 1e6
    assert modelo.window_waste_usd == pytest.approx(caro - barato, rel=1e-6)

    prometido = sum(f.window_waste_usd for f in findings)
    assert prometido <= resumen.total_cost_usd, (
        f"se promete ahorrar {prometido:.6f} de un gasto de {resumen.total_cost_usd:.6f}"
    )


@pytest.fixture(scope="module")
def proyecto_dos_pasos_mismo_nombre(store: ClickHouseStore):
    """Dos pasos distintos que la auto-instrumentación nombra igual (`chat <modelo>`).

    Es el caso normal de quien acaba de instalar Laplace. Uno clasifica con veinte
    tokens de entrada; el otro arrastra un manual de 20.000. Agrupados por nombre, el
    suelo de entrada del grupo son veinte tokens y la regla del contexto fijo no ve
    nada; agrupados por paso, la ve.
    """
    project = f"test-dos-pasos-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        corto = _span(
            project, trace, uuid.uuid4().hex[:16], "chat claude-sonnet-5", "llm", t * 10,
            dedup_hash=uuid.uuid4().hex[:16],
            step_key="paso-clasificar", step_label="clasificar",
        )
        corto.llm = _llm("claude-sonnet-5", 20, 5, 0.0001)
        spans.append(corto)
        for i in range(4):
            largo = _span(
                project, trace, uuid.uuid4().hex[:16], "chat claude-sonnet-5", "llm",
                t * 10 + i + 1,
                dedup_hash=uuid.uuid4().hex[:16],
                step_key="paso-manual", step_label="responder_del_manual",
            )
            largo.llm = _llm("claude-sonnet-5", 20_000, 30, 0.0403)
            spans.append(largo)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_dos_pasos_con_el_mismo_nombre_no_se_mezclan(
    store, window, proyecto_dos_pasos_mismo_nombre
):
    usos = {
        u.key: u
        for u in store.model_usage(proyecto_dos_pasos_mismo_nombre, window, min_calls=1)
    }
    assert set(usos) == {"paso-clasificar", "paso-manual"}
    # Y cada uno conserva su suelo de entrada, que es lo que la regla 3 necesita.
    assert usos["paso-clasificar"].min_input_tokens == 20
    assert usos["paso-manual"].min_input_tokens == 20_000
    assert usos["paso-manual"].name == "responder_del_manual"


def test_el_contexto_fijo_aparece_pese_al_nombre_generico(
    store, window, proyecto_dos_pasos_mismo_nombre
):
    """Agrupando por nombre, la llamada corta hundía el suelo y la regla callaba."""
    hallazgo = _contexto(store, proyecto_dos_pasos_mismo_nombre, window)
    assert hallazgo is not None
    assert "responder_del_manual" in hallazgo.summary
    assert hallazgo.window_waste_usd > 0

    findings = insights.detect(store, proyecto_dos_pasos_mismo_nombre, window)
    resumen = store.summarize_window(proyecto_dos_pasos_mismo_nombre, window)
    prometido = sum(f.window_waste_usd for f in findings)
    assert prometido <= resumen.total_cost_usd


def test_la_ficha_se_encuentra_por_la_clave_del_paso(
    store, window, proyecto_dos_pasos_mismo_nombre
):
    """El identificador del hallazgo lleva la clave, no el nombre, que ya no es único."""
    hallazgo = _contexto(store, proyecto_dos_pasos_mismo_nombre, window)
    assert hallazgo.id == "contexto_fijo:paso-manual:claude-sonnet-5"
    detalle = insights.detail(store, proyecto_dos_pasos_mismo_nombre, window, hallazgo.id)
    assert detalle is not None
    assert detalle.window_waste_usd == pytest.approx(hallazgo.window_waste_usd)


@pytest.fixture(scope="module")
def proyecto_sin_identidad_de_paso(store: ClickHouseStore):
    """Filas anteriores a la identidad de paso: `step_key` vacío, como en producción."""
    project = f"test-legado-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    # Ocho ejecuciones, no cuatro: descontadas las repeticiones quedan ocho llamadas
    # legítimas, que es lo que la regla del modelo caro necesita para llegar a mirar.
    for t in range(8):
        trace = uuid.uuid4().hex
        for i in range(3):
            span = _span(
                project, trace, uuid.uuid4().hex[:16], "extraer", "llm", t * 10 + i,
                dedup_hash="hash-legado",
            )
            span.llm = _llm("gpt-5.6-terra", 100, 10, 0.001)
            spans.append(span)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_las_trazas_ya_guardadas_siguen_descontando_sus_repeticiones(
    store, window, proyecto_sin_identidad_de_paso
):
    """El día del despliegue, todo lo guardado tiene `step_key` vacío.

    Si el cruce del descuento no cayera al nombre del span, ese día entero de datos
    volvería a contar dos veces el ahorro de las repeticiones sin que nada avisara.
    """
    usos = store.model_usage(proyecto_sin_identidad_de_paso, window, min_calls=1)
    assert [u.key for u in usos] == ["extraer"], "sin clave, la identidad es el nombre"

    findings = insights.detect(store, proyecto_sin_identidad_de_paso, window)
    resumen = store.summarize_window(proyecto_sin_identidad_de_paso, window)
    assert sum(f.window_waste_usd for f in findings) <= resumen.total_cost_usd

    # Exacto, por lo mismo que arriba: sin descuento el número sigue cabiendo dentro
    # del gasto, así que un tope holgado dejaría pasar el doble conteo.
    modelo = next(f for f in findings if f.kind == "modelo_caro")
    entrada, salida = 8 * 100, 8 * 10  # una llamada legítima por ejecución
    caro = (entrada * 2.0 + salida * 12.0) / 1e6
    barato = (entrada * 0.2 + salida * 1.2) / 1e6
    assert modelo.window_waste_usd == pytest.approx(caro - barato, rel=1e-6)


@pytest.fixture(scope="module")
def proyecto_manual_reintentado(store: ClickHouseStore):
    """Un paso con contexto fijo enorme que además se reintenta entero.

    Cuatro ejecuciones, cuatro llamadas idénticas por ejecución con 20.000 tokens de
    instrucciones. La regla de repetición dice que tres de cada cuatro sobran. Si la
    regla del contexto fijo sigue contando las dieciséis, promete cachear llamadas que
    la otra regla ya ha dado por eliminadas.
    """
    project = f"test-manual-rep-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    for t in range(4):
        trace = uuid.uuid4().hex
        for i in range(4):
            span = _span(
                project, trace, uuid.uuid4().hex[:16], "chat claude-sonnet-5", "llm",
                t * 10 + i,
                dedup_hash=f"hash-manual-{t}",  # misma entrada dentro de cada ejecución
                step_key="paso-manual", step_label="consultar_manual",
            )
            span.llm = _llm("claude-sonnet-5", 20_000, 300, 0.0430)
            spans.append(span)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_no_se_promete_cachear_llamadas_que_no_deberian_existir(
    store, window, proyecto_manual_reintentado
):
    """Cuarto camino del doble conteo: descontar tokens pero no llamadas.

    `_without_duplicates` recortaba los tokens y dejaba `calls` intacto, así que la
    regla del contexto fijo seguía viendo dieciséis llamadas donde sólo quedaban cuatro
    y prometía el ahorro de cachear doce que la regla de repetición ya había eliminado.
    """
    findings = insights.detect(store, proyecto_manual_reintentado, window)
    resumen = store.summarize_window(proyecto_manual_reintentado, window)

    repeticion = next(f for f in findings if f.kind == "repeticion")
    assert repeticion.window_waste_usd > 0

    # Quitadas las repeticiones queda una llamada por ejecución: dentro de una
    # ejecución no hay ninguna segunda llamada que pueda encontrar la caché caliente,
    # así que la regla del contexto fijo no tiene nada que prometer.
    assert _contexto(store, proyecto_manual_reintentado, window) is None

    prometido = sum(f.window_waste_usd for f in findings)
    assert prometido <= resumen.total_cost_usd, (
        f"se promete ahorrar {prometido:.6f} de un gasto de {resumen.total_cost_usd:.6f}"
    )


def test_el_mapa_de_descuento_suma_los_grupos_que_caen_en_la_misma_clave():
    """Prueba directa, sin base de datos, del mapa que evita el doble conteo.

    Hoy la consulta devuelve un grupo por (paso, modelo), así que dos grupos con la
    misma clave no llegan a darse. Mañana puede volver a agruparse de otra forma —ya ha
    cambiado dos veces— y el diccionario por comprensión que había aquí se quedaba con
    el último grupo en silencio. Se comprueba la propiedad, no el camino por el que hoy
    se llega a ella.
    """
    grupos = [
        RepeatedGroup(
            dedup_hash="a", name="extraer", span_type="llm", model="gpt-5.6-terra",
            step_key="paso-x", extra_spans=2, extra_input_tokens=100, extra_output_tokens=10,
        ),
        RepeatedGroup(
            dedup_hash="b", name="extraer", span_type="llm", model="gpt-5.6-terra",
            step_key="paso-x", extra_spans=3, extra_input_tokens=200, extra_output_tokens=20,
        ),
    ]
    assert insights._duplicate_tokens(grupos) == {("paso-x", "gpt-5.6-terra"): (300, 30, 5)}


def test_el_mapa_de_descuento_ignora_lo_que_no_puede_cruzar():
    """Sin paso o sin modelo no hay pareja posible: restar a ciegas sería peor."""
    grupos = [
        RepeatedGroup(dedup_hash="a", name="buscar", span_type="tool", model="",
                      step_key="paso-y", extra_spans=4, extra_input_tokens=0),
        RepeatedGroup(dedup_hash="b", name="x", span_type="llm", model="gpt-5.6-terra",
                      step_key="", extra_spans=2, extra_input_tokens=50),
    ]
    assert insights._duplicate_tokens(grupos) == {}


# ---------------------------------------------------------------------------------
# La quinta cara del doble conteo: el bucle contra el modelo caro
# ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proyecto_con_bucle_y_modelo_caro(store: ClickHouseStore):
    """Un paso que da vueltas Y usa un modelo con alternativa más barata.

    Las dos reglas miran las mismas llamadas: la del bucle reclama las vueltas de más y
    la del modelo caro reclama la diferencia de tarifa sobre TODOS los tokens del paso,
    vueltas incluidas. El descuento que impide esto existe desde D-061, pero se
    alimenta sólo de las repeticiones exactas: cuando la regla de bucles entró en D-109
    nadie la enchufó, y es la quinta forma que encuentra este proyecto de contar dos
    veces el mismo dinero.
    """
    from laplace_backend.ingest.otlp import loop_hash

    project = f"test-bucle-caro-{uuid.uuid4().hex[:8]}"
    spans: list[Span] = []
    salida = [{"role": "assistant", "content": "SEGUIR"}]
    for t in range(6):
        trace = uuid.uuid4().hex
        spans.append(_span(project, trace, f"a{t}".ljust(16, "0"), "agente", "agent", t * 60))
        for i in range(5):
            entrada = [{"role": "user", "content": f"Vuelta {i} de 5."}]
            s = _span(
                project,
                trace,
                f"b{t}{i}".ljust(16, "0"),
                "chat gpt-5.6-terra",
                "llm",
                t * 60 + i,
                parent=f"a{t}".ljust(16, "0"),
                dedup_hash=f"entrada-{t}-{i}",
                step_key="k-sondear",
                step_label="sondear",
                step_site="agente > sondear",
                loop_hash=loop_hash("llm", "chat", entrada),
                loop_out_hash=loop_hash("llm", "chat", salida, ignorar_numeros=False),
            )
            s.llm = _llm("gpt-5.6-terra", 400, 6, 0.0021)
            s.llm.input_messages = entrada
            s.llm.output_messages = salida
            spans.append(s)
    store.insert_spans(spans)
    yield project
    store.delete_project(project)


def test_el_bucle_y_el_modelo_caro_no_reclaman_los_mismos_tokens(
    store, window, proyecto_con_bucle_y_modelo_caro
):
    """La cifra exacta, no un tope contra el gasto.

    `overview()` acota el evitable con `min(suma, gasto)`, así que un solape se
    convierte en «puedes dejar de pagar el 100 %» y la pantalla lo enseña como una
    buena noticia. Comprobar sólo `suma <= gasto` deja pasar el error mientras quepa
    dentro, que es justo contra lo que avisa STATUS: aquí se comprueba que **el tope no
    llega a morder**.
    """
    hallazgos = insights.detect(store, proyecto_con_bucle_y_modelo_caro, window)
    tipos = {f.kind for f in hallazgos}
    assert "bucle" in tipos, "sin bucle esta prueba no mira el solape"

    resumen = store.summarize_window(proyecto_con_bucle_y_modelo_caro, window)
    prometido = sum(f.window_waste_usd for f in hallazgos)
    assert prometido <= resumen.total_cost_usd, (
        f"se promete más ahorro ({prometido:.6f}) que gasto ({resumen.total_cost_usd:.6f}): "
        "dos reglas están reclamando los mismos tokens"
    )

    vista = insights.overview(store, proyecto_con_bucle_y_modelo_caro, window)
    assert vista.avoidable_ratio < 1.0, (
        "el 100 % evitable sale del tope, no de los datos: el héroe promete que puedes "
        "dejar de pagar tu agente entero"
    )


def test_las_vueltas_de_un_bucle_se_descuentan_del_uso_del_modelo(
    store, window, proyecto_con_bucle_y_modelo_caro
):
    """Y el descuento es de tokens Y de llamadas, como el de la repetición.

    Las llamadas importan por lo mismo que en D-061: la regla del contexto fijo cuenta
    cuántas veces se reenvía el prompt, y prometer cachear llamadas que la otra regla ya
    ha dado por eliminadas es prometer dos veces el mismo ahorro.
    """
    bucles = store.loop_groups(
        proyecto_con_bucle_y_modelo_caro,
        window,
        min_vueltas=insights.MIN_VUELTAS_BUCLE,
        max_salidas=insights.MAX_SALIDAS_BUCLE,
    )
    assert bucles, "la fixture tiene que producir un bucle"
    duplicados = insights._duplicate_tokens(
        store.repeated_groups(
            proyecto_con_bucle_y_modelo_caro, window, min_repeats=insights.MIN_REPEATS
        ),
        bucles,
    )
    assert duplicados, "las vueltas de más tienen que entrar en el descuento"

    usos = store.model_usage(proyecto_con_bucle_y_modelo_caro, window, min_calls=1)
    uso = next(u for u in usos if u.key == "k-sondear")
    neto = insights._without_duplicates(uso, duplicados)
    assert neto.calls == uso.calls - bucles[0].extra_spans
    assert neto.input_tokens == uso.input_tokens - bucles[0].extra_input_tokens
