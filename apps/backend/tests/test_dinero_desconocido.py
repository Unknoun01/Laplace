"""El guardia: un coste desconocido no puede llegar a pantalla como un cero.

Ha salido tres veces, en tres pantallas distintas, y las tres con la misma forma: el
código preguntaba «¿el coste es 0?» donde la pregunta era «¿sabemos cuánto cuesta?».

    · El hallazgo de repetición decía «No gasta tokens de más» sobre 104 llamadas al
      modelo, porque el coste de las copias sobrantes salía a 0 al no haber tarifa.
    · El Panel enseñaba «Gasto total: 0 $» y «Coste por ejecución: 0 $», sin marca, con
      el 100 % de las llamadas sin tarifa.
    · El inicio ponía un «$0» enorme y el aviso de total incompleto debajo, que es
      exactamente el patrón que D-073 prohibió para la proyección: el número se lee
      antes que el aviso.

Tres parches habrían dejado la puerta abierta a la cuarta. Este fichero es la puerta:

1. **Un guardia que lee los modelos** de la API y exige que toda cifra en dólares venga
   acompañada de algo que diga si se puede afirmar. Una cifra nueva sin compañera no
   pasa de aquí.
2. **Tres pruebas de comportamiento** sobre tráfico sin tarifa —un agente con modelos
   locales, que es el caso de cualquier estudiante— que exigen que ninguna de las tres
   pantallas dé un número de dinero, y que en su lugar digan por qué y enseñen lo que
   sí está medido.

Lo que este fichero NO hace: mirar el HTML. Que la pantalla pinte lo que el contrato
dice se comprueba con los tipos de la web y con el ojo; lo que se puede blindar aquí es
que el contrato nunca ofrezca un cero pelado.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage
from pydantic import BaseModel

from laplace_backend import (
    api,
    api_ajustes,
    api_evals,
    api_prompts,
    coverage,
    dinero,
    insights,
    panel,
    presupuesto,
)
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=20)

MODULOS = (api, api_ajustes, api_evals, api_prompts, coverage, insights, panel, presupuesto)


# ---------------------------------------------------------------------------------
# 1. El guardia que lee los modelos
# ---------------------------------------------------------------------------------


def _modelos_de_respuesta() -> list[type[BaseModel]]:
    vistos: dict[str, type[BaseModel]] = {}
    for modulo in MODULOS:
        for _, obj in inspect.getmembers(modulo, inspect.isclass):
            if issubclass(obj, BaseModel) and obj is not BaseModel:
                vistos[f"{obj.__module__}.{obj.__name__}"] = obj
    return list(vistos.values())


def test_toda_cifra_en_dolares_viaja_con_algo_que_diga_si_se_puede_afirmar():
    """El guardia. Si alguien añade un `_usd` a un modelo de la API sin una compañera
    que diga si la cifra es afirmable, esto se pone en rojo y hay que decidir qué pasa
    cuando no hay tarifa. Que es justo la decisión que las tres veces anteriores no se
    tomó.

    Las compañeras válidas están en `dinero.COMPANERAS`, cerrada a propósito: añadir una
    nueva tiene que verse en el diff.
    """
    sospechosos = []
    for modelo in _modelos_de_respuesta():
        campos = set(modelo.model_fields)
        dinero_ = {c for c in campos if c.endswith("_usd")}
        if not dinero_:
            continue
        if not any(
            campo == companera or campo.endswith(f"_{companera}")
            for campo in campos
            for companera in dinero.COMPANERAS
        ):
            sospechosos.append(f"{modelo.__module__}.{modelo.__name__}: {sorted(dinero_)}")
    assert not sospechosos, (
        "cifras en dólares sin nada que diga si se pueden afirmar:\n  " + "\n  ".join(sospechosos)
    )


def test_el_guardia_pillaria_un_modelo_nuevo_sin_compañera():
    """La prueba de la prueba: un modelo inventado con dinero y sin compañera tiene que
    ser detectado por el mismo criterio. Sin esto, el guardia podría estar pasando por
    vacío y nadie lo sabría."""

    class Inventado(BaseModel):
        total_usd: float = 0.0

    campos = set(Inventado.model_fields)
    assert not any(
        campo == companera or campo.endswith(f"_{companera}")
        for campo in campos
        for companera in dinero.COMPANERAS
    )


# ---------------------------------------------------------------------------------
# 2. El comportamiento, sobre tráfico sin ninguna tarifa
# ---------------------------------------------------------------------------------


def _span_sin_tarifa(project: str, trace: str, *, paso: str, clave: str, sitio: str,
                     entrada: int, salida: int, i: int) -> Span:
    """Una llamada a un modelo local: tokens medidos, tarifa inexistente."""
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name="chat qwen2.5:0.5b",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=400),
        duration_ms=400.0,
        step_key=clave,
        step_label=paso,
        step_site=sitio,
        step_hint="Clasifica el ticket.",
        dedup_hash="siempre-el-mismo",
    )
    span.llm = LLMAttributes(
        request_model="qwen2.5:0.5b",
        usage=TokenUsage(input_tokens=entrada, output_tokens=salida),
        # Lo que el motor de precios produce cuando el modelo no está en la tabla: no es
        # gratis, es que no se sabe.
        cost=Cost(total_usd=0.0, unknown=True),
    )
    return span


@pytest.fixture
def almacen_sin_tarifas(tmp_path):
    """Doce ejecuciones de un agente con modelo local, repitiendo un paso tres veces."""
    store = SQLiteStore(tmp_path / "sin-tarifa.db")
    store.migrate()
    spans = []
    for t in range(12):
        for repeticion in range(3):
            spans.append(
                _span_sin_tarifa(
                    "local", f"tr-{t}", paso="clasificar", clave="k-clasificar",
                    sitio="atender > clasificar", entrada=120, salida=6,
                    i=t * 10 + repeticion,
                )
            )
    store.insert_spans(spans)
    return store


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(hours=1), until=AHORA + timedelta(hours=1), days=1)


def test_el_inicio_no_ensena_un_cero_de_dinero_cuando_no_hay_tarifas(almacen_sin_tarifas):
    """Lo que la pantalla necesita para no poner un «$0» grande: un motivo."""
    vista = insights.overview(almacen_sin_tarifas, "local", _ventana())

    assert vista.llm_calls == 36
    assert vista.unknown_cost_spans == 36
    assert vista.cost_unavailable, "sin ninguna tarifa no hay cifra que dar, hay un motivo"
    assert "tarifa" in vista.cost_unavailable
    # Y lo que sí se mide sigue ahí: es lo que se enseña en lugar del dinero.
    assert vista.input_tokens == 36 * 120
    assert vista.traces == 12


def test_el_panel_no_da_cifras_de_dinero_cuando_no_hay_tarifas(almacen_sin_tarifas):
    tablero = panel.build(almacen_sin_tarifas, "local", _ventana())

    monetarias = [m for m in [*tablero.per_execution, *tablero.totals] if m.unit == "money"]
    assert monetarias, "el panel tiene métricas de dinero; si no, este test no mide nada"
    for metrica in monetarias:
        assert metrica.value is None, f"«{metrica.label}» da una cifra sin tarifas conocidas"
        assert metrica.unavailable, f"«{metrica.label}» no dice por qué no hay cifra"
    # Las que no son dinero siguen dando su número: tokens y pasos están medidos.
    tokens = next(m for m in tablero.per_execution if m.unit == "tokens")
    assert tokens.value and tokens.value > 0


def test_un_hallazgo_sin_tarifa_dice_los_tokens_y_no_dice_que_no_gasta(almacen_sin_tarifas):
    """El fallo exacto: 24 llamadas de más al modelo descritas como «no gasta tokens de
    más» porque su coste salía a cero."""
    hallazgos = insights.detect(almacen_sin_tarifas, "local", _ventana())
    repeticion = next(f for f in hallazgos if f.kind == "repeticion")

    assert "no gasta tokens de más" not in repeticion.summary.lower()
    assert repeticion.window_waste_tokens == 24 * 126, "dos copias de más por ejecución"
    assert repeticion.cost_unavailable, "tiene que decir por qué no hay dinero"
    assert repeticion.window_waste_usd == 0.0
    assert repeticion.cost_is_floor is True


# ---------------------------------------------------------------------------------
# 3. Bucles: el mismo paso dando vueltas sin llegar a nada
# ---------------------------------------------------------------------------------


def _vuelta(project: str, trace: str, intento: int, salida: str, i: int) -> Span:
    """Una vuelta de un sondeo: lo único que cambia entre llamadas es el contador."""
    from laplace_backend.ingest.otlp import dedup_hash, loop_hash

    entrada = [{"role": "user", "content": f"Intento {intento} de 6. ¿Seguimos esperando?"}]
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name="chat qwen2.5:0.5b",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=500),
        duration_ms=500.0,
        step_key="k-esperar",
        step_label="esperar_confirmacion",
        step_site="atender > esperar_confirmacion",
        dedup_hash=dedup_hash("llm", "chat", "qwen2.5:0.5b", entrada),
        loop_hash=loop_hash("llm", "chat", entrada),
        loop_out_hash=loop_hash(
            "llm", "chat", [{"role": "assistant", "content": salida}], ignorar_numeros=False
        ),
    )
    span.llm = LLMAttributes(
        request_model="qwen2.5:0.5b",
        usage=TokenUsage(input_tokens=60, output_tokens=4),
        cost=Cost(total_usd=0.0, unknown=True),
        input_messages=entrada,
        output_messages=[{"role": "assistant", "content": salida}],
    )
    return span


def test_un_bucle_con_contador_de_intentos_se_detecta(tmp_path):
    """El caso que la repetición exacta no puede ver.

    Seis vueltas por ejecución en las que lo único que cambia es «Intento N de 6».
    Para `dedup_hash` son seis llamadas distintas y no hay nada que decir; para
    cualquiera que las mire es el mismo bucle atascado (D-109).
    """
    store = SQLiteStore(tmp_path / "bucle.db")
    store.migrate()
    spans = []
    for t in range(8):
        for intento in range(6):
            spans.append(
                _vuelta("local", f"b-{t}", intento, "SEGUIR", i=t * 20 + intento)
            )
    store.insert_spans(spans)

    hallazgos = insights.detect(store, "local", _ventana())
    bucles = [f for f in hallazgos if f.kind == "bucle"]
    assert len(bucles) == 1, "las seis vueltas son un solo bucle, no seis hallazgos"

    bucle = bucles[0]
    assert "6 vueltas" in bucle.title or "hasta 6" in bucle.title
    assert bucle.window_waste_tokens == 8 * 5 * 64, "cinco vueltas de más por ejecución"
    assert bucle.window_waste_usd == 0.0
    assert bucle.cost_unavailable, "sin tarifa, se dice por qué no hay dinero"


def test_un_bucle_que_avanza_no_se_señala(tmp_path):
    """El contraejemplo que evita el falso positivo, y que es trabajo legítimo: un
    agente que procesa ocho pedidos distintos hace ocho llamadas parecidas —sólo
    cambia un número— pero cada una produce un resultado distinto. Eso no es un bucle
    atascado, es un bucle que trabaja."""
    store = SQLiteStore(tmp_path / "trabaja.db")
    store.migrate()
    spans = []
    for t in range(8):
        for item in range(6):
            spans.append(
                _vuelta("local", f"ok-{t}", item, f"pedido {item} procesado", i=t * 20 + item)
            )
    store.insert_spans(spans)

    hallazgos = insights.detect(store, "local", _ventana())
    assert [f for f in hallazgos if f.kind == "bucle"] == []


# ---------------------------------------------------------------------------------
# 4. Las reglas de dinero, funcionando sin tarifa
# ---------------------------------------------------------------------------------


def _llamada(project: str, trace: str, *, paso: str, modelo: str, entrada: int, salida: int,
             ms: float, i: int, cacheados: int = 0) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name=f"chat {modelo}",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=ms),
        duration_ms=ms,
        step_key=f"k-{paso}",
        step_label=paso,
        step_site=f"atender > {paso}",
        dedup_hash=uuid.uuid4().hex[:16],
        loop_hash=uuid.uuid4().hex[:16],
    )
    span.llm = LLMAttributes(
        request_model=modelo,
        usage=TokenUsage(input_tokens=entrada, output_tokens=salida,
                         cached_input_tokens=cacheados),
        cost=Cost(total_usd=0.0, unknown=True),
    )
    return span


def test_el_modelo_caro_se_detecta_por_tiempo_cuando_no_hay_tarifa(tmp_path):
    """La regla 2 tenía que callarse con modelos locales para siempre: necesita precios
    y un modelo local no los tiene. Pero lo que la hace útil —«este paso responde cuatro
    palabras y se lo lleva el modelo gordo»— se puede medir en tiempo, que sí se mide
    siempre (D-108).

    Aquí `traducir` contesta 4 tokens con el modelo lento, y el proyecto ya usa otro
    más rápido para otra cosa. La recomendación sale del propio tráfico del usuario, no
    de una lista nuestra de modelos.
    """
    store = SQLiteStore(tmp_path / "caro.db")
    store.migrate()
    spans = []
    for t in range(10):
        spans.append(_llamada("local", f"c-{t}", paso="traducir", modelo="qwen2.5:7b",
                              entrada=80, salida=4, ms=4000, i=t * 10))
        spans.append(_llamada("local", f"c-{t}", paso="redactar", modelo="qwen2.5:0.5b",
                              entrada=90, salida=50, ms=400, i=t * 10 + 1))
    store.insert_spans(spans)

    hallazgos = insights.detect(store, "local", _ventana())
    caros = [f for f in hallazgos if f.kind == "modelo_caro"]
    assert len(caros) == 1, "sólo el paso corto con el modelo lento"
    caro = caros[0]
    assert "traducir" in caro.title
    assert "qwen2.5:0.5b" in caro.summary, "propone un modelo que el usuario ya usa"
    assert caro.window_waste_usd == 0.0
    assert caro.window_waste_ms > 0
    assert caro.cost_unavailable


def test_el_contexto_fijo_mira_lo_que_no_esta_cacheado_y_no_si_hay_cache(tmp_path):
    """La regla 3 se apagaba en cuanto veía un token de caché. Contra OpenAI eso la
    dejaba muerta —cachea sola por encima de 1.024 tokens— y contra Ollama también
    (D-105). La pregunta útil es cuánto del contexto fijo **no** se está cacheando.

    Aquí cada llamada manda 3.000 tokens fijos y el servidor sólo sirve 300 de caché:
    queda el 90 % sin cachear y hay algo que decir.
    """
    store = SQLiteStore(tmp_path / "contexto.db")
    store.migrate()
    spans = [
        _llamada("local", f"x-{t}", paso="redactar", modelo="qwen2.5:1.5b",
                 entrada=3000, salida=40, ms=900, i=t * 10, cacheados=300)
        for t in range(12)
    ]
    store.insert_spans(spans)

    hallazgos = insights.detect(store, "local", _ventana())
    contexto = [f for f in hallazgos if f.kind == "contexto_fijo"]
    assert len(contexto) == 1, "con caché parcial la regla tiene que seguir hablando"
    assert contexto[0].window_waste_tokens == 12 * 3000 - 12 * 300
    assert contexto[0].cost_unavailable


def test_una_llamada_fallida_no_apaga_la_regla_del_contexto_fijo(tmp_path):
    """El suelo de entrada se calculaba con `MIN(input_tokens)` sobre todas las
    llamadas. Una sola que falló durante una caída del proveedor trae 0 tokens, y con
    ella el mínimo caía a cero y la regla se apagaba para ese paso en toda la ventana.
    """
    store = SQLiteStore(tmp_path / "fallida.db")
    store.migrate()
    spans = [
        _llamada("local", f"y-{t}", paso="redactar", modelo="qwen2.5:1.5b",
                 entrada=3000, salida=40, ms=900, i=t * 10)
        for t in range(12)
    ]
    rota = _llamada("local", "y-rota", paso="redactar", modelo="qwen2.5:1.5b",
                    entrada=0, salida=0, ms=50, i=500)
    rota.status = "error"
    rota.status_message = "APIConnectionError"
    spans.append(rota)
    store.insert_spans(spans)

    hallazgos = insights.detect(store, "local", _ventana())
    assert [f for f in hallazgos if f.kind == "contexto_fijo"], (
        "una llamada caída no puede dejar mudo al paso entero"
    )


# ---------------------------------------------------------------------------------
# 4. El motivo tiene que ser cierto, no sólo existir
# ---------------------------------------------------------------------------------
#
# El guardia de arriba comprueba que una cifra de dinero venga con algo que diga si se
# puede afirmar. No comprueba que ese algo diga la verdad, y por ese hueco se coló lo
# siguiente: la regla del modelo caro mandaba por la misma puerta dos situaciones
# distintas —«no está en la tabla de precios» y «está, pero ya es el más barato y no
# tiene con qué compararse»— y escribía el primer mensaje para las dos. Resultado: el
# producto afirmando que no conoce la tarifa de un modelo cuyo coste pinta en las otras
# diez pantallas. Un usuario que lea eso deja de creerse la tabla entera (D-114).


def _llamada_con_tarifa(project: str, trace: str, *, paso: str, modelo: str, entrada: int,
                        salida: int, ms: float, i: int) -> Span:
    """Como `_llamada`, pero de un modelo que sí está en la tabla de precios."""
    span = _llamada(project, trace, paso=paso, modelo=modelo, entrada=entrada,
                    salida=salida, ms=ms, i=i)
    span.llm.cost = Cost(total_usd=0.004, input_usd=0.003, output_usd=0.001, unknown=False)
    return span


@pytest.fixture
def almacen_todo_con_tarifa(tmp_path):
    """Un proyecto donde **todos** los modelos tienen precio conocido.

    `iterar` usa el más barato de la tabla, que por serlo no tiene alternativa con la
    que compararse; `clasificar` usa uno más caro y más rápido. Es la situación que
    hacía hablar al producto de tarifas desconocidas sin que faltara ninguna.
    """
    store = SQLiteStore(tmp_path / "con-tarifa.db")
    store.migrate()
    spans = []
    for t in range(8):
        spans.append(
            _llamada_con_tarifa("tienda", f"c-{t}", paso="iterar", modelo="gpt-5.6-luna",
                                entrada=200, salida=4, ms=900, i=t * 10)
        )
        spans.append(
            _llamada_con_tarifa("tienda", f"c-{t}", paso="clasificar",
                                modelo="gpt-5.6-terra", entrada=200, salida=6, ms=300,
                                i=t * 10 + 3)
        )
    store.insert_spans(spans)
    return store


def test_un_motivo_de_no_saber_el_dinero_tiene_que_ser_cierto(almacen_todo_con_tarifa):
    """El guardia nuevo, y el que generaliza.

    Sobre tráfico donde no falta ni una tarifa, ninguna frase del producto puede decir
    que falta. Barre `cost_unavailable` y `summary` de todos los hallazgos contra la
    lista cerrada de `dinero.AFIRMACIONES_DE_SIN_TARIFA`: si alguien escribe una forma
    nueva de decirlo, la añade ahí y este barrido la mira desde el primer día.
    """
    hallazgos = insights.detect(almacen_todo_con_tarifa, "tienda", _ventana())
    assert hallazgos, "sin hallazgos este barrido no mira nada"

    mentiras = [
        (f.id, texto)
        for f in hallazgos
        for texto in (f.cost_unavailable, f.summary)
        if texto and dinero.afirma_no_tener_tarifa(texto)
    ]
    assert mentiras == [], (
        "todos los modelos de este proyecto están en la tabla de precios, así que "
        "ninguna frase puede decir lo contrario. Dicen: "
        f"{mentiras}"
    )


def test_el_mas_barato_de_la_tabla_no_es_un_modelo_sin_tarifa(almacen_todo_con_tarifa):
    """El caso concreto, dicho con sus dos nombres.

    `gpt-5.6-luna` no tiene alternativa **porque ya es el más barato**, no porque no
    tenga precio. Las dos cosas apagan la versión de dinero de la regla y son
    razones opuestas: una es un hueco de nuestra tabla y la otra es una respuesta.
    """
    from laplace_backend.pricing import get_price_table

    assert get_price_table().lookup("gpt-5.6-luna") is not None, (
        "esta prueba se apoya en que el modelo esté en la tabla; si deja de estar, "
        "hay que elegir otro, no relajar la aserción"
    )

    hallazgos = insights.detect(almacen_todo_con_tarifa, "tienda", _ventana())
    iterar = [f for f in hallazgos if f.kind == "modelo_caro" and "iterar" in f.title]
    assert iterar, "el paso lento con salida corta tiene que salir señalado por tiempo"

    hallazgo = iterar[0]
    assert not dinero.afirma_no_tener_tarifa(hallazgo.cost_unavailable), (
        f"dice que no hay tarifa de un modelo que sí la tiene: {hallazgo.cost_unavailable!r}"
    )
    assert not dinero.afirma_no_tener_tarifa(hallazgo.summary), (
        f"lo mismo en el resumen de la tarjeta: {hallazgo.summary!r}"
    )
    # Y lo que sí es cierto se dice: no hay con qué comparar el precio.
    assert "barato" in hallazgo.cost_unavailable or "comparar" in hallazgo.cost_unavailable
