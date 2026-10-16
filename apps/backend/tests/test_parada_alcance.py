"""Hasta dónde llega la parada de Ajustes (D-206).

La parada vive en el proceso del agente: la aplica el SDK de Python antes de cada
llamada al modelo. Con OpenInference u OpenLLMetry activos, las integraciones de Laplace
ceden la traza (D-141), pero la llamada sigue pasando por su parche: ahí también se
mira la parada. Y la copia de las reglas es del proceso, no del contexto: una llamada en
otro hilo, sin copiar el contexto, también se corta.

Los clientes son los reales y lo único falso es el transporte: «cortar» quiere decir
que la petición no sale.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from laplace import GuardExceeded, _control

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
anthropic = pytest.importorskip("anthropic", reason="el extra [anthropic] no está instalado")
from laplace.integrations import anthropic as ai  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402
from test_convivencia import RESPUESTA, _instrumentador_falso  # noqa: E402
from test_plano_control import _laplace_dice  # noqa: E402
from test_proveedores_otras_rutas import _anthropic, _openai  # noqa: E402

MENSAJES = [{"role": "user", "content": "hola"}]


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    ai.instrument()
    _control.reiniciar()
    yield
    _control.reiniciar()
    oi.uninstrument()
    ai.uninstrument()


@pytest.fixture
def otro_instrumentador(monkeypatch):
    otro = _instrumentador_falso("openinference.instrumentation.langchain", monkeypatch)
    otro.instrument()
    yield
    otro.uninstrument()


class _Contador:
    def __init__(self, httpx_mod) -> None:
        self.peticiones = 0
        self._httpx = httpx_mod

    def __call__(self, request):
        self.peticiones += 1
        return self._httpx.Response(200, json=RESPUESTA)


def _openai_sincrono(contador):
    _openai(contador).chat.completions.create(model="gpt-5.6-luna", messages=MENSAJES)


def _openai_asincrono(contador):
    cliente = _openai(contador, asincrono=True)
    asyncio.run(cliente.chat.completions.create(model="gpt-5.6-luna", messages=MENSAJES))


def _anthropic_sincrono(contador):
    _anthropic(contador).messages.create(model="claude-haiku-4-5", max_tokens=10, messages=MENSAJES)


def _anthropic_asincrono(contador):
    cliente = _anthropic(contador, asincrono=True)
    asyncio.run(cliente.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=MENSAJES))


def _anthropic_stream(contador):
    with _anthropic(contador).messages.stream(
        model="claude-haiku-4-5", max_tokens=10, messages=MENSAJES
    ) as stream:
        stream.until_done()


LLAMADAS = {
    "openai": (_openai_sincrono, "httpx"),
    "openai_async": (_openai_asincrono, "httpx"),
    "anthropic": (_anthropic_sincrono, "httpx2"),
    "anthropic_async": (_anthropic_asincrono, "httpx2"),
    "anthropic_stream": (_anthropic_stream, "httpx2"),
}


@pytest.mark.parametrize("llamada", list(LLAMADAS))
def test_parado_no_sale_lo_que_traza_otro_instrumentador(llamada, monkeypatch, otro_instrumentador):
    hacer, modulo = LLAMADAS[llamada]
    contador = _Contador(pytest.importorskip(modulo))
    _laplace_dice(monkeypatch, stopped=True)
    with pytest.raises(GuardExceeded) as corte:
        hacer(contador)
    assert contador.peticiones == 0, "la parada corta aunque la traza la haga otro"
    assert (corte.value.reason, corte.value.source) == ("stopped", "laplace")


def test_en_marcha_lo_que_traza_otro_sale(monkeypatch, otro_instrumentador):
    contador = _Contador(pytest.importorskip("httpx"))
    _laplace_dice(monkeypatch, stopped=False)
    _openai_sincrono(contador)
    assert contador.peticiones == 1


def test_parado_no_sale_lo_que_corre_en_otro_hilo_sin_contexto(monkeypatch):
    contador = _Contador(pytest.importorskip("httpx"))
    _laplace_dice(monkeypatch, stopped=True)
    cortes: list[str] = []

    def en_otro_hilo() -> None:
        try:
            _openai_sincrono(contador)
        except GuardExceeded as exc:
            cortes.append(exc.reason)

    hilo = threading.Thread(target=en_otro_hilo)
    hilo.start()
    hilo.join()
    assert contador.peticiones == 0
    assert cortes == ["stopped"]
