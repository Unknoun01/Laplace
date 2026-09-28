"""La red: todo hallazgo que el motor sepa encontrar tiene que saber explicarse.

Existe por un fallo concreto. La regla de bucles entró en D-109 como «el diferenciador
del producto desde el principio», se añadió a `detect()` y **nunca se añadió a
`detail()`**. Resultado en pantalla: la tarjeta que más dinero devolvía del proyecto de
demo llevaba a una página que decía «ese problema ya no aparece… **enhorabuena**». El
producto felicitaba al usuario por su hallazgo más caro.

Lo que falló no fue la rama que falta, fue que **nada la exigía**. `detect()` y
`detail()` son dos puertas del mismo catálogo y no había ningún sitio donde estuviera
escrito que hay que cruzar las dos. Una regla nueva se podía entregar entera por la
mitad, y la suite entera en verde.

Por eso aquí hay dos pruebas y no una:

1. **Sobre tráfico de verdad**, que cada hallazgo que sale de `detect()` tenga ficha. Y
   antes de comprobarlo, que el tráfico produzca **todos los tipos**: sin eso la prueba
   pasaría sin haber mirado el que falla, que es exactamente cómo el fallo sobrevivió.
2. **Estructural**, que los tipos que `detail()` reconoce sean los tipos que existen.
   La primera prueba depende de que alguien recuerde sembrar tráfico del tipo nuevo; la
   segunda no depende de que nadie se acuerde de nada. Es el mismo criterio de D-097 con
   las rutas: una regla nueva nace sin ficha y la suite lo dice.

Corre sin red ni almacenes de nube: SQLite en un directorio temporal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import insights
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore

AHORA = datetime.now(timezone.utc) - timedelta(minutes=20)

#: Un modelo que está en la tabla de precios y tiene alternativa más barata: hace falta
#: para que las reglas 2 y 3 puedan disparar con dinero de por medio.
CARO = "gpt-5.6-terra"

#: Y el más barato de la tabla, que por serlo **no tiene alternativa**. Sirve para el
#: otro camino de la regla 2, el que se mide en tiempo.
BARATO = "gpt-5.6-luna"


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(hours=2), until=AHORA + timedelta(hours=2), days=1)


def _span(
    trace: str,
    *,
    paso: str,
    clave: str,
    entrada_tokens: int,
    salida_tokens: int,
    i: int,
    dedup: str,
    entrada_msgs: list[dict] | None = None,
    salida_msgs: list[dict] | None = None,
    bucle: tuple[str, str] | None = None,
    modelo: str = CARO,
    ms: float = 400.0,
) -> Span:
    inicio = AHORA + timedelta(seconds=i)
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id="catalogo",
        name=f"chat {modelo}",
        type="llm",
        status="ok",
        start_time=inicio,
        end_time=inicio + timedelta(milliseconds=ms),
        duration_ms=ms,
        step_key=clave,
        step_label=paso,
        step_site=f"agente > {paso}",
        step_hint="Instrucciones fijas del paso.",
        dedup_hash=dedup,
        loop_hash=bucle[0] if bucle else "",
        loop_out_hash=bucle[1] if bucle else "",
    )
    span.llm = LLMAttributes(
        request_model=modelo,
        usage=TokenUsage(input_tokens=entrada_tokens, output_tokens=salida_tokens),
        cost=Cost(total_usd=0.002, input_usd=0.0015, output_usd=0.0005),
        input_messages=entrada_msgs or [{"role": "user", "content": "hola"}],
        output_messages=salida_msgs or [{"role": "assistant", "content": "ok"}],
    )
    return span


@pytest.fixture
def almacen_con_los_cuatro_tipos(tmp_path):
    """Un proyecto que dispara las cuatro reglas a la vez.

    No es tráfico bonito: es tráfico elegido para que ninguna de las cuatro reglas se
    quede sin ejemplo. Que estén las cuatro es parte de lo que se comprueba, no un
    detalle del montaje.
    """
    from laplace_backend.ingest.otlp import loop_hash

    store = SQLiteStore(tmp_path / "catalogo.db")
    store.migrate()
    spans: list[Span] = []

    # Regla 1 — repetición exacta: el mismo dedup_hash tres veces por traza.
    for t in range(4):
        for copia in range(3):
            spans.append(
                _span(
                    f"rep-{t}",
                    paso="consultar_saldo",
                    clave="k-saldo",
                    entrada_tokens=80,
                    salida_tokens=12,
                    i=t * 30 + copia,
                    dedup="saldo-siempre-igual",
                )
            )

    # Regla 4 — bucle: cinco vueltas por traza, entradas que sólo cambian en el número
    # y una sola salida distinta.
    salida_bucle = [{"role": "assistant", "content": "SEGUIR"}]
    for t in range(6):
        for intento in range(5):
            entrada = [{"role": "user", "content": f"Intento {intento} de 5. ¿Listo ya?"}]
            spans.append(
                _span(
                    f"buc-{t}",
                    paso="esperar_confirmacion",
                    clave="k-esperar",
                    entrada_tokens=90,
                    salida_tokens=4,
                    i=200 + t * 30 + intento,
                    dedup=f"espera-{t}-{intento}",
                    entrada_msgs=entrada,
                    salida_msgs=salida_bucle,
                    bucle=(
                        loop_hash("llm", "chat", entrada),
                        loop_hash("llm", "chat", salida_bucle, ignorar_numeros=False),
                    ),
                )
            )

    # Reglas 2 y 3 — un paso corto, con un suelo grande de entrada y sin caché, que usa
    # un modelo con alternativa más barata. Doce llamadas: pasa el mínimo de las dos.
    # Dos por ejecución: con una sola, la caché no se reutiliza nunca y no hay contexto
    # fijo que recuperar. Antes salía igual, diciendo que el modelo no tenía tarifa, que
    # era falso (D-135).
    for t in range(12):
        spans.append(
            _span(
                f"caro-{t // 2}",
                paso="resumir",
                clave="k-resumir",
                entrada_tokens=3_200,
                salida_tokens=8,
                i=500 + t * 20,
                dedup=f"resumen-{t}",
                entrada_msgs=[{"role": "user", "content": f"Resume el pedido {t}."}],
                salida_msgs=[{"role": "assistant", "content": f"Pedido {t} listo."}],
            )
        )

    # Regla 2, la otra mitad — el mismo tipo de hallazgo por su camino de tiempo: un
    # paso lento que usa el modelo más barato de la tabla, que por serlo no tiene
    # alternativa. Va aquí porque un tipo de hallazgo con dos caminos son dos cosas que
    # explicar, y la segunda se entregó montada sobre la ficha de la primera (D-114).
    for t in range(8):
        spans.append(
            _span(
                f"lento-{t}",
                paso="iterar",
                clave="k-iterar",
                entrada_tokens=200,
                salida_tokens=4,
                i=900 + t * 20,
                dedup=f"iterar-{t}",
                modelo=BARATO,
                ms=900,
            )
        )

    # Regla 5 — Prompts como fuente (D-157): la v2 de «saludo», que es la que corre
    # ahora, cuesta más por ejecución que la v1. Salida larga y entrada corta, para que
    # ninguna otra regla reclame ese dinero.
    for version, coste, desde in ((1, 0.001, 1200), (2, 0.003, 1500)):
        for t in range(6):
            span = _span(
                f"saludo-v{version}-{t}",
                paso="saludar",
                clave=f"k-saludo-v{version}",
                entrada_tokens=150,
                salida_tokens=120,
                i=desde + t * 20,
                dedup=f"saludo-{version}-{t}",
            )
            span.prompt_name = "saludo"
            span.prompt_version = version
            span.llm.cost = Cost(total_usd=coste, input_usd=coste / 2, output_usd=coste / 2)
            spans.append(span)

    store.insert_spans(spans)
    return store


def test_todo_hallazgo_que_el_motor_encuentra_sabe_explicarse(almacen_con_los_cuatro_tipos):
    """El caso concreto, sobre tráfico que produce los cuatro tipos.

    La primera aserción no es decorativa: si el tráfico dejara de producir bucles, el
    bucle de abajo no tendría nada que comprobar y esta prueba volvería a pasar con el
    fallo dentro. Esa es literalmente la forma en la que el fallo original sobrevivió a
    una suite de 333 pruebas.
    """
    ventana = _ventana()
    hallazgos = insights.detect(almacen_con_los_cuatro_tipos, "catalogo", ventana)

    tipos = {f.kind for f in hallazgos}
    assert tipos == set(get_args(insights.FindingKind)), (
        "el tráfico de la fixture tiene que producir los cuatro tipos; si no, esta "
        f"prueba no mira el que falla. Ha producido: {sorted(tipos)}"
    )

    sin_ficha = [
        f.id
        for f in hallazgos
        if insights.detail(almacen_con_los_cuatro_tipos, "catalogo", ventana, f.id) is None
    ]
    assert sin_ficha == [], (
        "estos hallazgos salen en el inicio y su ficha da 404, así que el usuario hace "
        f"clic en un problema real y le decimos que ya no existe: {sin_ficha}"
    )


def test_ninguna_ficha_ensena_un_hueco_donde_va_un_dato(almacen_con_los_cuatro_tipos):
    """Tener ficha no basta: la ficha tiene que estar escrita para ese hallazgo.

    Un tipo de hallazgo con dos caminos —la regla del modelo caro mide en dinero o en
    tiempo según haya con qué comparar— son dos cosas que explicar, y la segunda entró
    montada sobre la ficha de la primera: el paso a seguir decía literalmente «Cambia el
    modelo de ese paso a None» y la detección afirmaba una alternativa más barata que no
    existía. Un `None` en pantalla es la forma que toma aquí una plantilla rellenada con
    datos del otro caso.
    """
    ventana = _ventana()
    store = almacen_con_los_cuatro_tipos
    huecos = []
    for hallazgo in insights.detect(store, "catalogo", ventana):
        ficha = insights.detail(store, "catalogo", ventana, hallazgo.id)
        assert ficha is not None
        textos = {
            "what_happens": ficha.what_happens,
            "why": ficha.why,
            "detection_explanation": ficha.detection_explanation,
            "savings_calculation": ficha.savings_calculation,
            "savings_note": ficha.savings_note,
        }
        for paso in ficha.fix_steps:
            # `code` es opcional y vale `None` cuando el paso no lleva ejemplo: eso es
            # el modelo, no la pantalla. Se normaliza aquí para no cazarnos a nosotros.
            textos[f"paso «{paso.title}»"] = f"{paso.title} {paso.body} {paso.code or ''}"
        for campo, texto in textos.items():
            if "None" in texto:
                huecos.append((hallazgo.id, campo, texto.strip()[:90]))

    assert huecos == [], f"fichas con un hueco sin rellenar: {huecos}"


def test_detail_reconoce_todos_los_tipos_de_hallazgo_que_existen():
    """La red estructural, que no depende de que nadie siembre tráfico del tipo nuevo.

    `DETAILED_KINDS` se deriva de la tabla de despacho de `detail()`, no se escribe a
    mano: no puede quedarse desfasada diciendo que cubre algo que no cubre. Una regla
    nueva en `FindingKind` que no entre en esa tabla pone esto en rojo el mismo día.
    """
    assert insights.DETAILED_KINDS == set(get_args(insights.FindingKind)), (
        "hay tipos de hallazgo que `detect()` puede producir y `detail()` no sabe "
        "reconstruir. Los que faltan: "
        f"{set(get_args(insights.FindingKind)) - insights.DETAILED_KINDS}"
    )


# ---------------------------------------------------------------------------------
# Dos hallazgos distintos no pueden leerse como el mismo
# ---------------------------------------------------------------------------------


@pytest.fixture
def almacen_con_dos_llamantes(tmp_path):
    """La misma función, en bucle, llamada desde dos sitios distintos.

    Es lo que el proyecto de demo enseñaba en el inicio: dos tarjetas con el título
    «consultar_manual» da hasta 6 vueltas sin avanzar y cifras distintas. Son dos
    hallazgos de verdad —D-106 los separó a propósito, y mezclarlos escondía el roto
    detrás del sano— pero el título sólo llevaba el nombre de la función, así que en
    pantalla se leían como un duplicado del producto.
    """
    from laplace_backend.ingest.otlp import loop_hash

    store = SQLiteStore(tmp_path / "llamantes.db")
    store.migrate()
    spans: list[Span] = []
    salida = [{"role": "assistant", "content": "SEGUIR"}]

    for llamante in ("planificar", "revisar"):
        for t in range(6):
            for intento in range(5):
                entrada = [{"role": "user", "content": f"Vuelta {intento} de 5."}]
                spans.append(
                    _span(
                        f"{llamante}-{t}",
                        paso="consultar_manual",
                        # Claves distintas: es lo que hace la identidad por camino.
                        clave=f"k-{llamante}-manual",
                        entrada_tokens=90,
                        salida_tokens=4,
                        i=hash(llamante) % 50 + t * 30 + intento,
                        dedup=f"{llamante}-{t}-{intento}",
                        entrada_msgs=entrada,
                        salida_msgs=salida,
                        bucle=(
                            loop_hash("llm", f"chat-{llamante}", entrada),
                            loop_hash("llm", "chat", salida, ignorar_numeros=False),
                        ),
                    )
                )
                spans[-1].step_site = f"agente > {llamante} > consultar_manual"

    store.insert_spans(spans)
    return store


def test_dos_pasos_homonimos_no_producen_dos_tarjetas_iguales(almacen_con_dos_llamantes):
    """Si dos hallazgos distintos se titulan igual, el producto parece roto.

    Y es peor que parecerlo: el usuario arregla uno, vuelve, ve el otro con el mismo
    texto y concluye que no se ha enterado de su arreglo. El nombre de la función no
    basta desde que la identidad de un paso es el camino de llamada (D-115).
    """
    hallazgos = insights.detect(almacen_con_dos_llamantes, "catalogo", _ventana())
    bucles = [f for f in hallazgos if f.kind == "bucle"]
    assert len(bucles) == 2, f"son dos bucles distintos, uno por llamante: {len(bucles)}"

    titulos = [f.title for f in bucles]
    assert len(set(titulos)) == 2, f"dos tarjetas con el mismo título: {titulos}"
    assert any("planificar" in t for t in titulos), (
        f"el título tiene que decir desde dónde se llama: {titulos}"
    )
    assert any("revisar" in t for t in titulos), (
        f"el título tiene que decir desde dónde se llama: {titulos}"
    )


def test_la_ficha_se_titula_igual_que_la_tarjeta(almacen_con_dos_llamantes):
    """Si la tarjeta y su ficha se titulan distinto, el usuario cree que se equivocó
    de enlace. El desambiguado tiene que valer para los dos caminos, no sólo la lista.
    """
    ventana = _ventana()
    store = almacen_con_dos_llamantes
    for hallazgo in insights.detect(store, "catalogo", ventana):
        ficha = insights.detail(store, "catalogo", ventana, hallazgo.id)
        assert ficha is not None
        assert ficha.title == hallazgo.title, (
            f"la tarjeta dice {hallazgo.title!r} y su ficha {ficha.title!r}"
        )


# ---------------------------------------------------------------------------------
# «Ver las trazas afectadas» y el código de ejemplo
# ---------------------------------------------------------------------------------


def test_cada_hallazgo_sabe_llevar_a_sus_trazas(almacen_con_los_cuatro_tipos):
    """El botón de la ficha filtra por `step_key`, y tiene que encontrar algo.

    Antes buscaba por la etiqueta técnica «paso», que las reglas de repetición y de
    bucle no llevan: el enlace salía con `q=` vacío y enseñaba **todas** las trazas del
    proyecto, justo en los dos tipos que más dinero devuelven.
    """
    from laplace_backend.storage.base import TraceFilter

    store = almacen_con_los_cuatro_tipos
    sin_trazas = []
    for hallazgo in insights.detect(store, "catalogo", _ventana()):
        assert hallazgo.step_key, f"{hallazgo.id} no dice de qué paso es"
        pagina = store.list_traces(
            TraceFilter(project_id="catalogo", step_key=hallazgo.step_key, limit=200)
        )
        ids = {t.trace_id for t in pagina.traces}
        if hallazgo.sample_trace_id not in ids:
            sin_trazas.append(hallazgo.id)
    assert sin_trazas == [], (
        f"el filtro de estos hallazgos no encuentra ni su propia traza de ejemplo: {sin_trazas}"
    )


def test_las_trazas_afectadas_son_las_de_ese_llamante(almacen_con_dos_llamantes):
    """Con dos pasos homónimos, cada hallazgo lleva a sus trazas y no a las del otro.

    Buscar por texto confundía `consultar_manual` con cualquier paso que lo contuviera
    en el nombre, y el agente sano salía como afectado.
    """
    from laplace_backend.storage.base import TraceFilter

    store = almacen_con_dos_llamantes
    bucles = [f for f in insights.detect(store, "catalogo", _ventana()) if f.kind == "bucle"]
    assert len(bucles) == 2
    for bucle in bucles:
        llamante = "planificar" if "planificar" in bucle.title else "revisar"
        pagina = store.list_traces(
            TraceFilter(project_id="catalogo", step_key=bucle.step_key, limit=200)
        )
        ids = [t.trace_id for t in pagina.traces]
        assert ids, f"{bucle.title}: el filtro no encuentra nada"
        ajenas = [i for i in ids if not i.startswith(llamante)]
        assert ajenas == [], f"{bucle.title}: trae trazas del otro llamante {ajenas}"


@pytest.mark.parametrize("fixture", ["almacen_con_los_cuatro_tipos", "almacen_con_dos_llamantes"])
def test_el_codigo_de_ejemplo_no_lleva_el_titulo_dentro(fixture, request):
    """El fragmento que se copia tiene que poder pegarse.

    El título de un paso puede llevar el llamante —«agente → consultar_manual»— o una
    pista del prompt entre comillas, y los dos acababan dentro del Python de ejemplo:
    `respuesta = agente_de_equipaje → consultar_manual(...)`.
    """
    store = request.getfixturevalue(fixture)
    ventana = _ventana()
    malos = []
    for hallazgo in insights.detect(store, "catalogo", ventana):
        ficha = insights.detail(store, "catalogo", ventana, hallazgo.id)
        assert ficha is not None
        for paso in ficha.fix_steps:
            if paso.code and any(c in paso.code for c in ("→", "«", "»")):
                malos.append((hallazgo.id, paso.code.splitlines()[0]))
    assert malos == [], f"código de ejemplo con decoración de título dentro: {malos}"
