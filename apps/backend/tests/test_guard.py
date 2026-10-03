"""`laplace.guard`: cortar una ejecución que gasta de más o da vueltas sin avanzar.

Las llamadas al modelo van por el cliente real de OpenAI con un transporte falso que
cuenta las peticiones: «cortar» quiere decir que la petición **no sale**, y eso sólo se
puede comprobar desde el otro lado del cable.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from laplace import GuardExceeded, guard, observe
from laplace._guardia import huella
from laplace.pricing import get_price_table

from laplace_backend.ingest.otlp import loop_hash

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
import httpx  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402

MODELO = "gpt-5.6-luna"


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    yield
    oi.uninstrument()


def _respuesta(texto: str = "Una maleta de mano.", modelo: str = MODELO) -> dict:
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1770000000,
        "model": modelo,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": texto},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
    }


class _Proveedor:
    """Un transporte falso que cuenta cuántas peticiones le llegan de verdad."""

    def __init__(self, cuerpo: dict | None = None, textos: list[str] | None = None) -> None:
        self.peticiones = 0
        self.cuerpo = cuerpo or _respuesta()
        self.textos = textos

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones += 1
        if self.textos:
            return httpx.Response(200, json=_respuesta(self.textos[self.peticiones - 1]))
        return httpx.Response(200, json=self.cuerpo)

    def cliente(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key="sk-de-mentira", http_client=httpx.Client(transport=httpx.MockTransport(self))
        )


def _preguntar(cliente: openai.OpenAI, texto: str = "¿Qué llevo en el avión?") -> None:
    cliente.chat.completions.create(model=MODELO, messages=[{"role": "user", "content": texto}])


def _coste_de_una() -> float:
    coste = get_price_table().compute(MODELO, input_tokens=1000, output_tokens=1000)
    assert not coste.unknown, "la prueba necesita un modelo con tarifa"
    return coste.total_usd


# ---------------------------------------------------------------------------------
# Gasto
# ---------------------------------------------------------------------------------


def test_pasado_el_gasto_la_llamada_siguiente_no_sale():
    una = _coste_de_una()
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    # Dos llamadas caben por debajo; la segunda lo cruza y la tercera ya no sale.
    with guard(max_usd_per_run=una * 1.5) as g:
        _preguntar(cliente, "uno")
        _preguntar(cliente, "dos")
        with pytest.raises(GuardExceeded) as corte:
            _preguntar(cliente, "tres")
    assert proveedor.peticiones == 2
    assert corte.value.reason == "max_usd_per_run"
    assert corte.value.spent_usd == pytest.approx(2 * una)
    assert g.spent_usd == pytest.approx(2 * una)
    assert g.exceeded is corte.value


def test_el_gasto_es_el_de_la_tabla_de_precios_con_cache():
    """Lo que cuenta el límite es lo que pone la traza: la caché, a su tarifa."""
    cuerpo = _respuesta()
    cuerpo["usage"]["prompt_tokens_details"] = {"cached_tokens": 800}
    proveedor = _Proveedor(cuerpo)
    with guard(max_usd_per_run=100) as g:
        _preguntar(proveedor.cliente())
    esperado = get_price_table().compute(
        MODELO, input_tokens=1000, output_tokens=1000, cached_input_tokens=800
    )
    assert g.spent_usd == pytest.approx(esperado.total_usd)
    assert g.spent_usd < _coste_de_una()


def test_en_streaming_cuenta_lo_que_costo_al_terminar_el_stream():
    """Un stream no tiene recuento hasta el último trozo: se cobra cuando acaba."""
    from test_proveedores_reales import SSE_OPENAI

    peticiones = []

    def sse(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        return httpx.Response(
            200, content=SSE_OPENAI.encode(), headers={"content-type": "text/event-stream"}
        )

    cliente = openai.OpenAI(
        api_key="sk-de-mentira", http_client=httpx.Client(transport=httpx.MockTransport(sse))
    )
    una = (
        get_price_table()
        .compute(MODELO, input_tokens=1200, output_tokens=12, cached_input_tokens=1024)
        .total_usd
    )
    with guard(max_usd_per_run=una * 1.5) as g:
        for texto in ("uno", "dos"):
            list(
                cliente.chat.completions.create(
                    model=MODELO,
                    messages=[{"role": "user", "content": texto}],
                    stream=True,
                    stream_options={"include_usage": True},
                )
            )
        with pytest.raises(GuardExceeded):
            cliente.chat.completions.create(
                model=MODELO, messages=[{"role": "user", "content": "tres"}], stream=True
            )
    assert len(peticiones) == 2
    assert g.spent_usd == pytest.approx(2 * una)


def test_cortada_una_vez_no_se_puede_seguir_gastando():
    """Un agente que captura la excepción y sigue no vuelve a llegar al proveedor."""
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=_coste_de_una() / 2):
        _preguntar(cliente)
        for _ in range(3):
            with pytest.raises(GuardExceeded):
                _preguntar(cliente, "otra cosa distinta")
    assert proveedor.peticiones == 1


def test_un_modelo_sin_tarifa_no_cuenta_como_gratis(caplog):
    proveedor = _Proveedor(_respuesta(modelo="modelo-que-nadie-conoce-9"))
    cliente = proveedor.cliente()
    with caplog.at_level(logging.WARNING, logger="laplace"), guard(max_usd_per_run=1e-9) as g:
        for i in range(3):
            cliente.chat.completions.create(
                model="modelo-que-nadie-conoce-9",
                messages=[{"role": "user", "content": f"pregunta {'x' * i}"}],
            )
    # No se ha podido sumar: ni se corta como si costase, ni se dice que costó cero.
    assert proveedor.peticiones == 3
    assert g.spent_usd == 0
    assert g.unknown_cost_models == ["modelo-que-nadie-conoce-9"]
    assert g.cost_complete is False
    avisos = [r for r in caplog.records if "no tiene tarifa" in r.getMessage()]
    assert len(avisos) == 1, "se avisa una vez, no en cada llamada"


def test_fuera_de_un_guard_no_se_corta_nada():
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=_coste_de_una() / 2):
        _preguntar(cliente)
    for _ in range(3):
        _preguntar(cliente)
    assert proveedor.peticiones == 4


def test_como_decorador_cada_llamada_es_una_ejecucion_nueva():
    proveedor = _Proveedor()
    cliente = proveedor.cliente()

    @guard(max_usd_per_run=_coste_de_una() * 1.5)
    def atender(pregunta: str) -> None:
        _preguntar(cliente, pregunta)
        _preguntar(cliente, pregunta + " y además")

    atender("uno")
    atender("dos")
    assert proveedor.peticiones == 4


def test_dos_guard_anidados_mandan_los_dos():
    una = _coste_de_una()
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_usd_per_run=una * 2.5) as fuera:
        _preguntar(cliente, "a")
        with guard(max_usd_per_run=una * 10):
            _preguntar(cliente, "b")
            _preguntar(cliente, "c")
            # El de dentro tiene sitio; el de fuera, no.
            with pytest.raises(GuardExceeded):
                _preguntar(cliente, "d")
    assert proveedor.peticiones == 3
    assert fuera.spent_usd == pytest.approx(3 * una)


# ---------------------------------------------------------------------------------
# Bucles
# ---------------------------------------------------------------------------------


def test_un_paso_que_da_vueltas_sin_avanzar_se_corta():
    """El caso de D-109: el mismo paso, sólo cambia el contador, y la misma salida."""
    vueltas = []

    @observe(type="tool")
    def estado_almacen(pedido: str, intento: int) -> str:
        vueltas.append(intento)
        return "pendiente"

    with guard(max_loop=3), pytest.raises(GuardExceeded) as corte:
        for intento in range(1, 10):
            estado_almacen("A-1001", intento)
    assert vueltas == [1, 2, 3], "la cuarta vuelta no llega a ejecutarse"
    assert corte.value.reason == "max_loop"
    assert corte.value.step == "estado_almacen"
    assert corte.value.repeats == 3


def test_un_paso_que_avanza_no_se_corta():
    """Seis pedidos distintos con seis salidas distintas no es un bucle (D-109)."""

    @observe(type="tool")
    def procesar(pedido: int) -> str:
        return f"pedido {pedido} procesado"

    with guard(max_loop=2):
        hechos = [procesar(n) for n in range(6)]
    assert len(hechos) == 6


def test_dos_salidas_que_se_alternan_siguen_siendo_no_avanzar():
    """«Pendiente» y «reintentando» alternándose es dar vueltas igual."""
    vueltas = []

    @observe(type="tool")
    def consultar(intento: int) -> str:
        vueltas.append(intento)
        return "pendiente" if intento % 2 else "reintentando"

    with guard(max_loop=4), pytest.raises(GuardExceeded):
        for intento in range(10):
            consultar(intento)
    assert len(vueltas) == 4


def test_una_llamada_al_modelo_repetida_sin_avanzar_no_sale():
    """El reintento ciego: la misma petición, la misma respuesta, una y otra vez."""
    proveedor = _Proveedor()
    cliente = proveedor.cliente()
    with guard(max_loop=2), pytest.raises(GuardExceeded) as corte:
        for intento in range(5):
            _preguntar(cliente, f"Devuelve JSON válido (intento {intento})")
    assert proveedor.peticiones == 2
    assert corte.value.step == f"chat {MODELO}"


def test_una_llamada_al_modelo_que_avanza_no_se_corta():
    textos = [f"resumen del capítulo {n}" for n in range(5)]
    proveedor = _Proveedor(textos=textos)
    cliente = proveedor.cliente()
    with guard(max_loop=2):
        for n in range(5):
            _preguntar(cliente, f"Resume el capítulo {n}")
    assert proveedor.peticiones == 5


def test_en_asincrono_tambien():
    vueltas = []

    @observe(type="tool")
    async def esperar(intento: int) -> str:
        vueltas.append(intento)
        return "todavía no"

    @guard(max_loop=2)
    async def agente() -> None:
        for intento in range(5):
            await esperar(intento)

    with pytest.raises(GuardExceeded):
        asyncio.run(agente())
    assert len(vueltas) == 2


def test_en_asincrono_un_paso_que_avanza_no_se_corta():
    @observe(type="tool")
    async def leer(pagina: int) -> str:
        return f"página {pagina} leída"

    @guard(max_loop=2)
    async def agente() -> list[str]:
        return [await leer(n) for n in range(6)]

    assert len(asyncio.run(agente())) == 6


def test_cortada_por_bucle_tampoco_se_puede_seguir_con_otra_cosa():
    """El corte es de la ejecución entera, no del paso: un agente que captura la
    excepción y prueba por otro lado sigue cortado."""
    proveedor = _Proveedor()
    cliente = proveedor.cliente()

    @observe(type="tool")
    def consultar(intento: int) -> str:
        return "pendiente"

    with guard(max_loop=2):
        with pytest.raises(GuardExceeded):
            for intento in range(5):
                consultar(intento)
        with pytest.raises(GuardExceeded) as otra:
            _preguntar(cliente, "algo completamente distinto")
    assert proveedor.peticiones == 0
    assert otra.value.reason == "max_loop"


def test_la_huella_es_la_misma_que_la_de_la_regla_de_bucles():
    """Si una de las dos cambia de criterio, el guard cortaría lo que el Diagnóstico no
    señala, o dejaría pasar lo que sí."""
    valores = [
        {"pedido": "A-1001", "intento": 3},
        [{"role": "user", "content": "Hola   Mundo 42"}],
        "texto con\tespacios y 7 números 8",
        None,
    ]
    for valor in valores:
        for ignorar in (True, False):
            assert huella("tool", "paso", valor, ignorar_numeros=ignorar) == loop_hash(
                "tool", "paso", valor, ignorar_numeros=ignorar
            )


def test_sin_limites_no_es_un_guard():
    with pytest.raises(ValueError):
        guard()
    with pytest.raises(ValueError):
        guard(max_usd_per_run=0)
    with pytest.raises(ValueError):
        guard(max_loop=1)


def test_el_backend_y_el_sdk_cobran_con_el_mismo_motor():
    """El motor de precios vive en el SDK para que el guard cuente lo mismo que la traza.
    El backend no puede tener otro."""
    import laplace.pricing as del_sdk

    import laplace_backend.pricing as del_backend

    assert del_backend is del_sdk
