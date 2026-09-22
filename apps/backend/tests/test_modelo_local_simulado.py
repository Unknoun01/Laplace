"""SIMULADO. Tokens cacheados: lo único que un modelo local no puede darnos.

Todo lo de este fichero lleva números **inventados por nosotros**. Está aparte de
`test_modelo_local.py` a propósito y el nombre lo dice, porque mezclar las dos cosas es
exactamente cómo se acaba creyendo que algo está verificado cuando no lo está.

## Por qué hay que simular

El tramo más delicado del cálculo de coste es la caché: OpenAI y Anthropic la reportan
con criterios distintos —uno la mete dentro de `prompt_tokens`, el otro la deja fuera de
`input_tokens`—, la escritura se cobra por encima de la entrada, y Anthropic además
reparte la escritura entre cinco minutos y una hora a precios distintos (D-050, D-101).

Un servidor local da **la mitad**: Ollama reporta lecturas de caché de prefijo en
`cached_tokens`, y eso ya se prueba de verdad en `test_modelo_local.py`. Lo que no da
nunca son escrituras (`cache_write_tokens`), el reparto 5 min / 1 h ni la forma de
Anthropic. Eso no se puede ejercitar sin falsear la respuesta.

## Cómo se simula, y qué sigue siendo de verdad en cada parte

**§1 — Conformidad de forma.** Los bloques de uso simulados se validan contra los
modelos Pydantic **de los propios SDK** y se exige que cada clave que usamos sea un
campo *declarado* por ellos. Esto último importa más de lo que parece: los modelos de
OpenAI y Anthropic llevan `extra="allow"`, así que un campo mal escrito
—`cached_token`, `cache_creation_tokens`— pasaría la validación sin protestar y se
leería como `None` para siempre. Esta sección no falsea nada: lo que comprueba es que
nuestra idea de la forma coincide con la que el SDK declara. Corre siempre.

**§2 — El mismo gasto por los dos proveedores.** Los dos bloques simulados describen la
*misma* llamada contada como la cuenta cada proveedor, y se exige que el contrato los
normalice a los mismos números. Es la promesa de D-050 —«el mismo agente no cuesta
distinto según el proveedor»— comprobada de frente y no dos veces por separado. Clientes
reales, parseo real, transporte falso. Corre siempre.

**§3 — Inyección sobre el servidor local.** Aquí hay una llamada de verdad a un modelo
de verdad por HTTP, y lo único que se falsea son los tres contadores de caché, que se
inyectan en la respuesta antes de que el SDK la parsee. Se ejercita el camino entero
—red, parseo del SDK, acumulación, span, ingesta, precios— con los campos de caché
presentes. Se salta sin servidor local.

## QUÉ SIGUE SIN VERIFICAR, TAMBIÉN AQUÍ

* **No hay caché real en ninguna parte de este fichero.** Los contadores son nuestros
  (la lectura de caché real vive en `test_modelo_local.py`, no aquí).
  Que el span los recoja bien no dice nada sobre si el proveedor los reporta así hoy.
* **Ninguna cifra en dólares de aquí está validada contra una factura.** La aritmética
  del motor de precios se comprueba en `test_pricing.py` con tarifas escritas a mano;
  que esas tarifas sean las que cobran es cosa de `model_prices.json` y de su fecha de
  verificación, no de estas pruebas.
* **La forma de la respuesta se valida contra el SDK, no contra la API.** Si OpenAI
  añadiera mañana un campo de caché nuevo, el SDK tardaría en declararlo y esto seguiría
  en verde. El único aviso real de eso son las pruebas vivas con clave.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import httpx2
import modelo_local as local
import pytest
from helpers import exporter, span_llm

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")
anthropic = pytest.importorskip("anthropic", reason="el extra [anthropic] no está instalado")

from laplace.integrations import anthropic as ai  # noqa: E402
from laplace.integrations import openai as oi  # noqa: E402

# ---------------------------------------------------------------------------------
# Los bloques de uso simulados. Un solo sitio, para que no haya dos versiones de la
# «forma exacta» circulando por el repositorio.
# ---------------------------------------------------------------------------------

#: La misma llamada, contada como la cuenta cada proveedor. Los números están elegidos
#: para que las dos versiones describan lo mismo y se pueda comparar:
#:
#:   entrada facturable total ... 1.200   (76 nuevos + 1.024 de caché + 100 escritos)
#:   leídos de caché ............ 1.024
#:   escritos en caché .......... 100     (60 a cinco minutos + 40 a una hora)
#:   salida ..................... 12
ENTRADA_TOTAL = 1_200
NUEVOS = 76
CACHEADOS = 1_024
ESCRITOS_5M = 60
ESCRITOS_1H = 40
ESCRITOS = ESCRITOS_5M + ESCRITOS_1H
SALIDA = 12

#: OpenAI: `prompt_tokens` es el total facturable de entrada y **ya incluye** lo leído
#: de caché y lo escrito. El desglose va en `prompt_tokens_details`.
USAGE_OPENAI_SIMULADO: dict[str, Any] = {
    "prompt_tokens": ENTRADA_TOTAL,
    "completion_tokens": SALIDA,
    "total_tokens": ENTRADA_TOTAL + SALIDA,
    "prompt_tokens_details": {
        "cached_tokens": CACHEADOS,
        "cache_write_tokens": ESCRITOS,
        "audio_tokens": 0,
    },
    "completion_tokens_details": {
        "reasoning_tokens": 0,
        "audio_tokens": 0,
        "accepted_prediction_tokens": 0,
        "rejected_prediction_tokens": 0,
    },
}

#: Anthropic: `input_tokens` son **sólo los nuevos**. La caché va aparte, y la escritura
#: se reparte entre cinco minutos y una hora porque se cobran a precios distintos
#: (1,25x y 2x la entrada).
USAGE_ANTHROPIC_SIMULADO: dict[str, Any] = {
    "input_tokens": NUEVOS,
    "output_tokens": SALIDA,
    "cache_read_input_tokens": CACHEADOS,
    "cache_creation_input_tokens": ESCRITOS,
    "cache_creation": {
        "ephemeral_5m_input_tokens": ESCRITOS_5M,
        "ephemeral_1h_input_tokens": ESCRITOS_1H,
    },
}

MODELO_OPENAI = "gpt-5.6-luna"
MODELO_ANTHROPIC = "claude-haiku-4-5"


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    ai.instrument()
    yield
    oi.uninstrument()
    ai.uninstrument()


# ---------------------------------------------------------------------------------
# §1 — Conformidad de forma contra los modelos de los propios SDK
# ---------------------------------------------------------------------------------


def _campos_no_declarados(modelo: type, datos: dict[str, Any], camino: str = "") -> list[str]:
    """Claves de `datos` que el modelo Pydantic del SDK **no declara**, recursivamente.

    No vale con `model_validate`: los modelos de los dos SDK llevan `extra="allow"`, así
    que aceptan cualquier campo inventado sin decir nada. Un `cached_token` en singular
    pasaría la validación, se leería como `None` en la integración y el coste saldría por
    debajo del real sin que nadie se enterara. Que es el fallo de D-101 otra vez.
    """
    sobran: list[str] = []
    declarados = modelo.model_fields
    for clave, valor in datos.items():
        if clave not in declarados:
            sobran.append(f"{camino}{clave}")
            continue
        if isinstance(valor, dict):
            anidado = _tipo_anidado(declarados[clave].annotation)
            if anidado is not None:
                sobran += _campos_no_declarados(anidado, valor, f"{camino}{clave}.")
    return sobran


def _tipo_anidado(anotacion: Any) -> type | None:
    """El modelo Pydantic que hay dentro de una anotación `X | None`, si lo hay."""
    candidatos = [anotacion, *getattr(anotacion, "__args__", ())]
    for candidato in candidatos:
        if isinstance(candidato, type) and hasattr(candidato, "model_fields"):
            return candidato
    return None


def test_simulado_la_forma_de_openai_es_la_que_declara_su_sdk():
    sobran = _campos_no_declarados(openai.types.CompletionUsage, USAGE_OPENAI_SIMULADO)
    assert not sobran, f"campos que el SDK de OpenAI no declara: {sobran}"
    uso = openai.types.CompletionUsage.model_validate(USAGE_OPENAI_SIMULADO)
    assert uso.prompt_tokens_details.cached_tokens == CACHEADOS
    # El campo que faltaba por leer hasta D-101. Si el SDK lo quitara, esto avisa.
    assert uso.prompt_tokens_details.cache_write_tokens == ESCRITOS


def test_simulado_la_forma_de_anthropic_es_la_que_declara_su_sdk():
    sobran = _campos_no_declarados(anthropic.types.Usage, USAGE_ANTHROPIC_SIMULADO)
    assert not sobran, f"campos que el SDK de Anthropic no declara: {sobran}"
    uso = anthropic.types.Usage.model_validate(USAGE_ANTHROPIC_SIMULADO)
    assert uso.cache_read_input_tokens == CACHEADOS
    assert uso.cache_creation.ephemeral_5m_input_tokens == ESCRITOS_5M
    assert uso.cache_creation.ephemeral_1h_input_tokens == ESCRITOS_1H


def test_simulado_un_campo_mal_escrito_lo_pillaria_la_comprobacion_de_forma():
    """La prueba de la prueba. Sin esto, las dos de arriba podrían estar pasando por
    vacías —un fallo en `_campos_no_declarados` las dejaría siempre en verde— y nadie lo
    sabría. El campo de abajo es el error real que se cometería: el singular."""
    roto = dict(USAGE_OPENAI_SIMULADO, prompt_tokens_details={"cached_token": 10})
    sobran = _campos_no_declarados(openai.types.CompletionUsage, roto)
    assert sobran == ["prompt_tokens_details.cached_token"]
    # Y se confirma que el SDK, por su cuenta, lo habría tragado sin protestar: esto es
    # lo que hace falta la comprobación de arriba.
    assert openai.types.CompletionUsage.model_validate(roto).prompt_tokens_details is not None


# ---------------------------------------------------------------------------------
# §2 — La misma llamada, por los dos proveedores, tiene que dar los mismos números
# ---------------------------------------------------------------------------------

RESPUESTA_OPENAI = {
    "id": "chatcmpl-simulada",
    "object": "chat.completion",
    "created": 1770000000,
    "model": MODELO_OPENAI,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Una maleta de mano."},
            "finish_reason": "stop",
            "logprobs": None,
        }
    ],
    "usage": USAGE_OPENAI_SIMULADO,
}

RESPUESTA_ANTHROPIC = {
    "id": "msg_simulada",
    "type": "message",
    "role": "assistant",
    "model": MODELO_ANTHROPIC,
    "content": [{"type": "text", "text": "Una maleta de mano."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": USAGE_ANTHROPIC_SIMULADO,
}


def _uso_openai_simulado():
    # Las dos llamadas de la comparación van en el mismo test, así que cada una parte de
    # un exportador limpio: `span_llm()` exige que haya exactamente un span de LLM.
    exporter.clear()
    cliente = openai.OpenAI(
        api_key=local.CLAVE_FICTICIA,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=RESPUESTA_OPENAI))
        ),
    )
    cliente.chat.completions.create(
        model=MODELO_OPENAI, messages=[{"role": "user", "content": "hola"}]
    )
    return span_llm().llm.usage


def _uso_anthropic_simulado():
    exporter.clear()
    cliente = anthropic.Anthropic(
        api_key=local.CLAVE_FICTICIA,
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=RESPUESTA_ANTHROPIC))
        ),
    )
    cliente.messages.create(
        model=MODELO_ANTHROPIC, max_tokens=64, messages=[{"role": "user", "content": "hola"}]
    )
    return span_llm().llm.usage


def test_simulado_la_misma_llamada_da_los_mismos_tokens_en_los_dos_proveedores():
    """D-050 de frente. Los dos proveedores reportan lo mismo con criterios distintos, y
    el contrato tiene que dejarlos en el mismo sitio: si no, el mismo agente costaría
    distinto según a quién se lo pidas, y comparar dos proveedores —que es media razón
    de ser de Laplace— no significaría nada.

    Las pruebas de `test_proveedores_reales.py` comprueban cada lado por separado contra
    números escritos a mano; esto compara los dos lados entre sí, que es donde el error
    de criterio se vería.
    """
    de_openai = _uso_openai_simulado()
    de_anthropic = _uso_anthropic_simulado()

    assert de_openai.input_tokens == de_anthropic.input_tokens == ENTRADA_TOTAL
    assert de_openai.output_tokens == de_anthropic.output_tokens == SALIDA
    assert de_openai.cached_input_tokens == de_anthropic.cached_input_tokens == CACHEADOS
    assert de_openai.uncached_input_tokens == de_anthropic.uncached_input_tokens == NUEVOS
    # Lo escrito en caché suma lo mismo por los dos lados. El reparto 5 min / 1 h sólo
    # lo publica Anthropic: OpenAI no distingue duración, así que todo va a la corta.
    escritos_openai = de_openai.cache_write_tokens + de_openai.cache_write_1h_tokens
    escritos_anthropic = de_anthropic.cache_write_tokens + de_anthropic.cache_write_1h_tokens
    assert escritos_openai == escritos_anthropic == ESCRITOS
    assert de_anthropic.cache_write_1h_tokens == ESCRITOS_1H


# ---------------------------------------------------------------------------------
# §3 — Inyección de los contadores de caché sobre una llamada local de verdad
# ---------------------------------------------------------------------------------


class TransporteQueInyectaCache(httpx.BaseTransport):
    """Deja pasar la petición al servidor local y añade caché a la respuesta.

    La llamada, la generación y el parseo son de verdad; lo inventado son tres números.
    Se inyecta en el transporte y no con un proxy aparte por una razón de honestidad:
    así se ve en el código que el bloque de caché lo ponemos nosotros, en vez de
    esconderlo detrás de un salto de red que lo hiciera parecer del servidor.

    La respuesta se lee entera y se devuelve ya en memoria, también en streaming. Eso
    cambia el troceado —el SDK recibe el SSE de golpe—, así que el reparto real de los
    trozos por la red es lo que ejercita `test_modelo_local.py`, no esto.
    """

    def __init__(self) -> None:
        self._real = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        respuesta = self._real.handle_request(request)
        try:
            respuesta.read()
            cuerpo, tipo = respuesta.content, respuesta.headers.get("content-type", "")
        finally:
            respuesta.close()

        if respuesta.status_code == 200 and "text/event-stream" in tipo:
            cuerpo = self._inyectar_sse(cuerpo)
        elif respuesta.status_code == 200:
            cuerpo = self._inyectar_json(cuerpo)

        # Cabeceras nuevas a propósito: conservar `content-length` o `content-encoding`
        # de la original dejaría al cliente intentando descomprimir un cuerpo ya
        # descomprimido y de otro tamaño.
        return httpx.Response(
            respuesta.status_code, content=cuerpo, headers={"content-type": tipo}
        )

    @staticmethod
    def _parchear(uso: dict[str, Any]) -> dict[str, Any]:
        """Añade a lo que reportó el servidor un prefijo cacheado y otro escrito.

        Se suma en vez de sustituir para que la parte nueva siga siendo la que contó el
        servidor: así la aritmética de `uncached_input_tokens` se comprueba contra un
        número que no hemos elegido nosotros.
        """
        nuevos = int(uso.get("prompt_tokens") or 0)
        uso["prompt_tokens"] = nuevos + CACHEADOS + ESCRITOS
        uso["total_tokens"] = uso["prompt_tokens"] + int(uso.get("completion_tokens") or 0)
        # Las mismas claves que valida §1: si alguien las cambia aquí, esa sección grita.
        uso["prompt_tokens_details"] = {
            "cached_tokens": CACHEADOS,
            "cache_write_tokens": ESCRITOS,
        }
        return uso

    def _inyectar_json(self, cuerpo: bytes) -> bytes:
        try:
            datos = json.loads(cuerpo)
        except ValueError:
            return cuerpo
        if isinstance(datos, dict) and isinstance(datos.get("usage"), dict):
            datos["usage"] = self._parchear(datos["usage"])
            return json.dumps(datos).encode()
        return cuerpo

    def _inyectar_sse(self, cuerpo: bytes) -> bytes:
        salida = []
        for linea in cuerpo.decode("utf-8", "replace").split("\n"):
            prefijo = "data: "
            if linea.startswith(prefijo) and linea[len(prefijo) :].strip() not in ("", "[DONE]"):
                try:
                    datos = json.loads(linea[len(prefijo) :])
                except ValueError:
                    salida.append(linea)
                    continue
                if isinstance(datos.get("usage"), dict):
                    datos["usage"] = self._parchear(datos["usage"])
                    linea = prefijo + json.dumps(datos)
            salida.append(linea)
        return "\n".join(salida).encode()


@local.salta_sin_servidor
def test_simulado_la_cache_inyectada_llega_al_span_sin_contarse_dos_veces():
    """El camino entero con campos de caché presentes: red real, generación real, parseo
    del SDK real, y sólo los contadores inventados.

    Lo que se comprueba es el criterio del contrato (D-050): `prompt_tokens` de OpenAI
    **incluye** los cacheados, así que la entrada no se infla sumándolos aparte, y lo que
    queda como entrada nueva es lo que contó el servidor.
    """
    cliente = local.cliente(transport=TransporteQueInyectaCache())
    respuesta = cliente.chat.completions.create(
        model=local.modelo(), messages=[{"role": "user", "content": "Di hola."}], max_tokens=16
    )

    uso = span_llm().llm.usage
    assert uso.cached_input_tokens == CACHEADOS
    assert uso.cache_write_tokens == ESCRITOS
    assert uso.input_tokens == respuesta.usage.prompt_tokens, "el span dice lo que llegó"
    # Y la resta cuadra contra el número que puso el servidor, no contra uno nuestro.
    nuevos_del_servidor = respuesta.usage.prompt_tokens - CACHEADOS - ESCRITOS
    assert nuevos_del_servidor > 0
    assert uso.uncached_input_tokens == nuevos_del_servidor
    assert uso.estimated is False


@local.salta_sin_servidor
@pytest.mark.skipif(
    local.disponible() and not local.reporta_usage_en_streaming(),
    reason="tu servidor local no manda `usage` en streaming: no hay bloque donde inyectar",
)
def test_simulado_la_cache_inyectada_tambien_llega_por_el_camino_de_streaming():
    """El mismo campo, por el camino que más tráfico lleva en un agente real. Arreglar
    la caché sólo en el no-streaming fue el fallo de D-101, así que aquí van los dos."""
    cliente = local.cliente(transport=TransporteQueInyectaCache())
    list(
        cliente.chat.completions.create(
            model=local.modelo(),
            messages=[{"role": "user", "content": "Di hola."}],
            max_tokens=16,
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    uso = span_llm().llm.usage
    assert uso.cached_input_tokens == CACHEADOS
    assert uso.cache_write_tokens == ESCRITOS
    assert uso.uncached_input_tokens == uso.input_tokens - CACHEADOS - ESCRITOS
    assert uso.estimated is False
