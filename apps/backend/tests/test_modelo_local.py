"""Las integraciones, contra un modelo que corre en tu máquina. Coste cero, red real.

## Qué añade esto sobre lo que ya había

`test_proveedores_reales.py` usa los clientes reales de OpenAI y Anthropic con el
**transporte HTTP falseado**: el cuerpo de la respuesta lo escribimos nosotros. Eso ya
ejercita el parche, el parseo y el lector de SSE, pero el cuerpo sigue siendo nuestro.

Aquí hay un servidor al otro lado. Ollama expone la API de OpenAI en
`http://localhost:11434/v1`; al cliente real se le pasa ese `base_url` y una clave
ficticia, y el SDK publicado habla por HTTP con un modelo que genera texto de verdad.
Lo que se recorre entero, con nada falseado:

* la construcción del cliente y el parche sobre la clase que de verdad usa;
* una petición HTTP real, con sus cabeceras, su cuerpo y su código de estado;
* el parseo de un cuerpo que **no hemos escrito nosotros**;
* el lector de SSE sobre un flujo que llega troceado por la red, no de un `bytes`;
* el recuento de tokens tal y como lo reporta el servidor;
* el span, la ingesta y el contrato.

Cómo preparar el entorno: `docs/tests-con-modelo-local.md`. Sin servidor, todo esto se
salta solo con un motivo que dice qué arrancar.

## QUÉ NO QUEDA VERIFICADO AQUÍ. LÉELO ANTES DE CITAR ESTE FICHERO

Estas pruebas en verde **no validan el modelo de coste**. Cuatro agujeros, y ninguno es
un detalle:

1. **Un modelo local no factura.** No existe ninguna factura contra la que cuadrar los
   tokens que guardamos. Lo único que se comprueba es que el span dice lo mismo que
   *reportó el servidor*, no que eso sea lo que alguien cobró. La promesa de este
   producto —«tus números son los del proveedor»— sólo la comprueban las pruebas vivas
   de `test_proveedores_reales.py`, y ésas siguen esperando una clave.
2. **La caché es sólo de lectura, y con reglas que no son las de facturación.** Ollama
   reutiliza el prefijo del prompt y lo reporta en `prompt_tokens_details.cached_tokens`,
   con la forma de OpenAI, así que la **lectura** de caché sí se ejercita aquí de verdad
   (`test_local_la_cache_de_prefijo_llega_al_span`). Pero no reporta **escrituras**
   (`cache_write_tokens`) ni el reparto 5 min / 1 h, y cachea cualquier prefijo repetido,
   no a partir de 1.024 tokens y en bloques como OpenAI. Esa otra mitad sigue probándose
   con transporte falso y con los fixtures simulados de `test_modelo_local_simulado.py`,
   que están marcados como lo que son. Hasta el 18 de septiembre esto decía «no hay caché
   real»: era falso y lo destapó el agente de ejemplo (D-105).
3. **El tokenizador local cuenta distinto.** El recuento que devuelve el servidor es el
   de su propio tokenizador sobre su propio vocabulario. No se parece al de `o200k` ni
   al de Anthropic, así que de aquí no se puede deducir ninguna cifra en dólares. Por
   eso ninguna prueba de este fichero comprueba un importe: sólo comprueba **de dónde
   sale el número y cómo queda marcado**.
4. **Sólo se cubre OpenAI.** Ollama habla la API de OpenAI; no existe servidor local que
   hable la de Anthropic, y escribir un traductor nosotros sería volver a probar contra
   un doble propio. La integración de Anthropic se queda con el transporte falso y con
   las pruebas vivas.

Traducido a lo que importa: **que esto salga verde no significa que el coste esté
validado contra facturación.** No lo está.
"""

from __future__ import annotations

import asyncio

import modelo_local as local
import pytest
from helpers import exporter, span_llm

openai = pytest.importorskip("openai", reason="el extra [openai] no está instalado")

from laplace.integrations import openai as oi  # noqa: E402

from laplace_backend.pricing import get_price_table  # noqa: E402

#: Un prompt corto y aburrido: el tiempo de estas pruebas es el de generar, y un modelo
#: de 0,5B en CPU va a unos pocos tokens por segundo.
PREGUNTA = [{"role": "user", "content": "Di la palabra hola y nada más."}]
MAX_TOKENS = 16


@pytest.fixture(autouse=True)
def instrumentado():
    oi.instrument()
    yield
    oi.uninstrument()


# ---------------------------------------------------------------------------------
# Sin streaming
# ---------------------------------------------------------------------------------


@local.salta_sin_servidor
def test_local_el_span_dice_lo_mismo_que_la_respuesta_del_servidor():
    """La prueba central: lo que guardamos es lo que el servidor reportó.

    Se compara contra el objeto que devuelve el SDK —no contra números escritos a mano—
    así que vale con cualquier modelo y cualquier longitud de respuesta. Si mañana el
    campo de la respuesta se renombra, nuestro lado se quedaría a `None` y esto muerde.
    """
    respuesta = local.cliente().chat.completions.create(
        model=local.modelo(), messages=PREGUNTA, max_tokens=MAX_TOKENS, temperature=0
    )

    span = span_llm()
    assert span.llm.request_model == local.modelo()
    assert span.llm.response_model == respuesta.model
    assert span.llm.response_id == respuesta.id
    assert span.llm.usage.input_tokens == respuesta.usage.prompt_tokens
    assert span.llm.usage.output_tokens == respuesta.usage.completion_tokens
    assert span.llm.usage.estimated is False, "el servidor ha dado el recuento"
    assert span.llm.finish_reasons == [respuesta.choices[0].finish_reason]
    # Y el texto generado llega entero: de esto vive el explorador y cualquier juez.
    assert span.llm.output_messages[0]["content"] == respuesta.choices[0].message.content
    assert span.llm.input_messages[0]["content"] == PREGUNTA[0]["content"]


@local.salta_sin_servidor
def test_local_un_modelo_sin_tarifa_no_cuesta_cero_sino_no_lo_sabemos():
    """La primera regla del motor de precios, comprobada por fin contra una respuesta
    que no hemos escrito nosotros.

    Un modelo local no está en la tabla de precios y nunca lo estará. Lo que no puede
    pasar es que eso se convierta en un 0 que se suma a los totales: el span tiene que
    quedar marcado como coste desconocido para que la interfaz diga que el total está
    incompleto. Es el mismo camino por el que pasa cualquier modelo nuevo de OpenAI el
    día que sale, así que esto no es un caso de laboratorio.
    """
    if get_price_table().lookup(local.modelo()) is not None:
        pytest.skip(f"«{local.modelo()}» sí está en la tabla de precios: otro caso, otra prueba")

    local.cliente().chat.completions.create(
        model=local.modelo(), messages=PREGUNTA, max_tokens=MAX_TOKENS
    )
    coste = span_llm().llm.cost
    # El cero va acompañado de la marca: es lo que distingue «no cuesta nada» de «no
    # sabemos lo que cuesta», que es toda la diferencia.
    assert coste.unknown is True, "un modelo sin tarifa cuesta «no lo sabemos»"
    assert coste.rate == "", "no se puede citar una tarifa que no existe"
    assert coste.total_usd == 0.0


@local.salta_sin_servidor
def test_local_el_objeto_que_recibe_el_usuario_sigue_siendo_el_del_sdk():
    """Observar no puede cambiar lo observado. En el camino sin streaming devolvemos la
    respuesta original tal cual, y hay que comprobarlo: si algún día se envolviera
    «para enriquecerla», el código del usuario dejaría de ver el tipo que espera."""
    respuesta = local.cliente().chat.completions.create(
        model=local.modelo(), messages=PREGUNTA, max_tokens=MAX_TOKENS
    )
    assert isinstance(respuesta, openai.types.chat.ChatCompletion)


@local.salta_sin_servidor
def test_local_el_cliente_asincrono_tambien_esta_instrumentado():
    """El parche envuelve dos clases, `Completions` y `AsyncCompletions`, y el resto de
    las pruebas sólo usa la primera. La asíncrona es además la que usan casi todos los
    frameworks de agentes, así que romperla se notaría justo donde más duele."""

    async def llamar():
        cliente = local.cliente_async()
        try:
            return await cliente.chat.completions.create(
                model=local.modelo(), messages=PREGUNTA, max_tokens=MAX_TOKENS
            )
        finally:
            await cliente.close()

    respuesta = asyncio.run(llamar())
    span = span_llm()
    assert span.llm.usage.output_tokens == respuesta.usage.completion_tokens
    assert span.llm.usage.estimated is False


@local.salta_sin_servidor
def test_local_la_cache_de_prefijo_llega_al_span():
    """La lectura de caché, de verdad: nada inventado, ni siquiera el contador.

    Ollama reutiliza el prefijo de un prompt que ya ha visto y lo reporta en
    `prompt_tokens_details.cached_tokens`, igual que OpenAI. Dos llamadas con el mismo
    prompt de sistema largo: la segunda tiene que traer caché, y el span tiene que
    guardarla **dentro** de la entrada, no sumada aparte (D-050). Es el campo por el que
    ya nos equivocamos una vez en la dirección peligrosa.

    Lo que esto NO prueba: escrituras en caché (Ollama no las reporta nunca) ni que la
    caché de OpenAI siga estas reglas. La de Ollama cachea cualquier prefijo repetido;
    la de OpenAI, a partir de 1.024 tokens y en bloques.
    """
    sistema = "Eres el asistente de una tienda. Condiciones de venta: " + "Cláusula. " * 400
    mensajes = [
        {"role": "system", "content": sistema},
        {"role": "user", "content": "Di hola."},
    ]
    cliente = local.cliente()
    cliente.chat.completions.create(model=local.modelo(), messages=mensajes, max_tokens=4)
    exporter.clear()

    respuesta = cliente.chat.completions.create(
        model=local.modelo(), messages=mensajes, max_tokens=4
    )
    detalles = respuesta.usage.prompt_tokens_details
    cacheados = getattr(detalles, "cached_tokens", None) if detalles else None
    if not cacheados:
        pytest.skip("tu servidor local no reporta `cached_tokens`: actualiza Ollama")

    uso = span_llm().llm.usage
    assert uso.cached_input_tokens == cacheados
    assert uso.input_tokens == respuesta.usage.prompt_tokens, "la caché va dentro, no sumada"
    assert uso.uncached_input_tokens == respuesta.usage.prompt_tokens - cacheados
    assert uso.estimated is False


# ---------------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------------

#: El servidor local sólo manda `usage` al final del stream si implementa
#: `stream_options`, y los servidores locales tardaron en hacerlo. Se comprueba de
#: verdad (una generación de un token) en vez de suponerlo por número de versión.
sin_usage_en_streaming = pytest.mark.skipif(
    local.disponible() and not local.reporta_usage_en_streaming(),
    reason=(
        "tu servidor local no manda `usage` al final del stream aunque se le pida con "
        "`stream_options`. Actualiza Ollama; la prueba del camino estimado sí corre."
    ),
)


@local.salta_sin_servidor
@sin_usage_en_streaming
def test_local_en_streaming_el_span_cuadra_con_lo_que_reporta_el_servidor():
    """En un agente real la mayoría de las llamadas van en streaming, así que este
    camino es el que más tráfico lleva. Los trozos llegan por la red de verdad, troceados
    como los trocee el servidor, y el lector de SSE del SDK es el publicado."""
    trozos = list(
        local.cliente().chat.completions.create(
            model=local.modelo(),
            messages=PREGUNTA,
            max_tokens=MAX_TOKENS,
            temperature=0,
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    # El recuento viene en el último trozo, el que no trae texto.
    uso_servidor = next(t.usage for t in reversed(trozos) if getattr(t, "usage", None))
    texto = "".join(
        t.choices[0].delta.content for t in trozos if t.choices and t.choices[0].delta.content
    )

    span = span_llm()
    assert span.attributes.get("laplace.streaming") is True
    assert span.llm.usage.input_tokens == uso_servidor.prompt_tokens
    assert span.llm.usage.output_tokens == uso_servidor.completion_tokens
    assert span.llm.usage.estimated is False, "el servidor lo ha medido: no es una estimación"
    # El texto se acumula trozo a trozo. Sin esto, una respuesta en streaming no se
    # puede leer en el explorador ni pasársela a un juez.
    assert texto, "el modelo local tiene que haber generado algo"
    assert span.llm.output_messages[0]["content"] == texto


@local.salta_sin_servidor
def test_local_en_streaming_sin_include_usage_se_estima_y_se_marca():
    """Sin `stream_options` el servidor no manda recuento, y no se lo inyectamos: añadir
    un trozo que el código del usuario no espera es justo lo que un SDK de observabilidad
    no puede hacer. Se cuenta por nuestra cuenta y el span queda **marcado como
    estimado**, que es lo que permite a la interfaz no presentarlo como una factura."""
    list(
        local.cliente().chat.completions.create(
            model=local.modelo(),
            messages=PREGUNTA,
            max_tokens=MAX_TOKENS,
            stream=True,
        )
    )
    uso = span_llm().llm.usage
    assert uso.estimated is True
    assert uso.output_tokens > 0


@local.salta_sin_servidor
@sin_usage_en_streaming
def test_local_medido_y_estimado_son_numeros_distintos_y_el_span_lo_distingue():
    """La misma llamada por los dos caminos. Lo que importa no es cuál acierta más —el
    tokenizador del modelo local no se parece al de OpenAI, así que ninguno de los dos
    números vale para calcular dinero— sino que **el span diga de cuál de los dos
    viene**. Sin esa marca, una cifra estimada se presentaría como una medida."""
    cliente = local.cliente()

    list(
        cliente.chat.completions.create(
            model=local.modelo(),
            messages=PREGUNTA,
            max_tokens=MAX_TOKENS,
            temperature=0,
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    medido = span_llm().llm.usage
    exporter.clear()

    list(
        cliente.chat.completions.create(
            model=local.modelo(),
            messages=PREGUNTA,
            max_tokens=MAX_TOKENS,
            temperature=0,
            stream=True,
        )
    )
    estimado = span_llm().llm.usage

    assert medido.estimated is False
    assert estimado.estimated is True
    assert medido.input_tokens > 0 and estimado.input_tokens > 0


@local.salta_sin_servidor
def test_local_abandonar_un_stream_a_medias_cierra_el_span():
    """Un agente que corta la generación en cuanto tiene lo que busca es normal, y el
    span no puede quedarse abierto para siempre por eso. Aquí el socket se cierra de
    verdad a mitad de la respuesta, que es lo que no se puede simular con un `bytes`."""
    flujo = local.cliente().chat.completions.create(
        model=local.modelo(), messages=PREGUNTA, max_tokens=MAX_TOKENS, stream=True
    )
    for _ in flujo:
        break
    flujo.close()

    span = span_llm()
    assert span.status != "error", "abandonar un stream no es un fallo"
    assert span.attributes.get("laplace.streaming") is True


# ---------------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------------


@local.salta_sin_servidor
def test_local_un_error_del_servidor_no_se_traga_y_el_span_queda_en_error():
    """Un código de estado de error **de verdad**, generado por un servidor de verdad, y
    convertido en excepción por el SDK publicado. La excepción tiene que llegar al
    usuario tal cual: observar no puede cambiar lo observado.

    Se provoca pidiendo un modelo que el servidor no tiene, que es además el error más
    común del primer día de cualquiera.
    """
    with pytest.raises(openai.APIStatusError) as fallo:
        local.cliente().chat.completions.create(
            model="modelo-que-no-existe-en-ningun-sitio",
            messages=PREGUNTA,
            max_tokens=MAX_TOKENS,
        )
    assert fallo.value.status_code >= 400

    span = span_llm()
    assert span.status == "error"
    assert span.status_message, "un span en error sin mensaje no sirve para diagnosticar"
    # Y el modelo pedido queda anotado aunque no haya respuesta: es lo que se necesita
    # para saber qué falló.
    assert span.llm.request_model == "modelo-que-no-existe-en-ningun-sitio"


def test_local_una_conexion_que_no_llega_a_ningun_sitio_marca_el_span_en_error():
    """El camino del fallo de red, el único que no necesita servidor: corre siempre.

    Un puerto donde no escucha nadie da un `APIConnectionError` del SDK sin cuerpo de
    respuesta que parsear. Es el caso en el que es más fácil dejar un span abierto —no
    hay nada que volcar en él— y por eso hay que comprobar que se cierra y se marca.
    """
    # Puerto alto reservado por la IANA a uso privado: nadie escucha ahí.
    cliente = local.cliente(base_url="http://127.0.0.1:49151/v1", timeout=2.0)
    with pytest.raises(openai.APIConnectionError):
        cliente.chat.completions.create(model="cualquiera", messages=PREGUNTA, max_tokens=8)

    span = span_llm()
    assert span.status == "error"
    assert span.llm.usage.input_tokens == 0, "no hubo respuesta: no hay tokens que contar"
