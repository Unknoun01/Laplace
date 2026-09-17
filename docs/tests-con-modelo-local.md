# Probar las integraciones contra un modelo local, sin gastar dinero

Esta guía se sigue de arriba abajo sin pensar. Al final tendrás un modelo corriendo en
tu portátil y las pruebas de integración de Laplace hablando con él a través del SDK de
verdad de OpenAI, por HTTP de verdad, con coste cero.

## Antes de nada: qué consigues y qué no

**Qué consigues.** El camino entero menos la facturación: cliente real de OpenAI, el
parche de Laplace sobre la clase que el cliente usa de verdad, una petición HTTP con un
servidor al otro lado, el parseo de un cuerpo que no hemos escrito nosotros, el lector de
SSE sobre un flujo troceado por la red, la creación del span, la ingesta y el contrato.

**Qué NO consigues, y esto importa más que lo anterior:**

1. **Un modelo local no factura.** No hay ninguna factura contra la que cuadrar los
   tokens que Laplace guarda. Lo que se comprueba es que el span dice lo mismo que
   *reportó el servidor*, no que eso sea lo que alguien cobró.
2. **No hay caché real.** Un servidor local no sirve tokens desde caché ni los reporta.
   El tramo de caché —el más delicado del cálculo de coste— se ejercita con contadores
   inventados, en un fichero aparte marcado como simulado.
3. **El tokenizador local cuenta distinto.** Su vocabulario no es el de OpenAI ni el de
   Anthropic, así que de estas pruebas no sale ninguna cifra en dólares que signifique
   nada. Ninguna de ellas comprueba un importe.
4. **Sólo se cubre OpenAI.** Ollama habla la API de OpenAI. No hay servidor local que
   hable la de Anthropic, y escribir el traductor nosotros sería volver a probar contra
   un doble propio.

Dicho de una vez: **que esto salga verde no significa que el modelo de coste esté
validado contra facturación.** Eso lo comprueban las pruebas vivas de
`apps/backend/tests/test_proveedores_reales.py`, que siguen esperando una clave real.

## Por qué Ollama y no LM Studio

Las dos valen y las dos exponen la API de OpenAI. Ollama gana por cuatro motivos, todos
del mismo tipo: esto tiene que arrancar sin que nadie toque una ventana.

- **Es un servicio, no una aplicación.** Al instalarlo se queda escuchando en el 11434.
  LM Studio es una interfaz gráfica en la que hay que abrir la app, ir a la pestaña del
  servidor y cargar el modelo a mano antes de que responda nada.
- **Todo se hace por línea de comandos**, así que estos pasos se pueden copiar, pegar y
  meter en un CI. Con LM Studio habría que documentar dónde hacer clic, y eso caduca con
  cada rediseño.
- **Carga el modelo cuando llega la petición.** No hay un paso previo de «cargar en
  memoria» que se pueda olvidar, que es el fallo típico de la primera vez con LM Studio:
  el servidor responde 404 y parece que las pruebas están mal.
- **Un modelo se pide por su nombre** (`qwen2.5:0.5b`) y el nombre es el mismo en
  cualquier máquina, así que la documentación no depende de qué descargó cada uno.

Si aun así prefieres LM Studio, funciona igual: arranca su servidor local y lanza las
pruebas con `LAPLACE_LOCAL_BASE_URL=http://localhost:1234/v1`. Nada del código está
atado a Ollama; lo único que se supone es «un endpoint que habla la API de OpenAI».

## 1. Instalar Ollama

**Windows** (lo que estás usando):

```bash
winget install Ollama.Ollama
```

Si no tienes `winget`, baja el instalador de <https://ollama.com/download> y ejecútalo.
En Windows el instalador deja un servicio en marcha, así que **no hace falta arrancar
nada a mano**: cuando termine, Ollama ya está escuchando.

**macOS:**

```bash
brew install ollama && brew services start ollama
```

**Linux:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Comprueba que está:

```bash
ollama --version
```

## 2. Descargar el modelo

Uno pequeño, que cabe y corre sin GPU:

```bash
ollama pull qwen2.5:0.5b
```

Son unos 400 MB. Es el más pequeño que responde con sentido a un prompt trivial, y es
todo lo que hacen las pruebas: pedirle que diga «hola» con un tope de 16 tokens de
salida.

Si tu máquina va sobrada y prefieres uno mejor, cualquiera de éstos sirve sin cambiar
nada más:

| Modelo            | Tamaño  | Cuándo |
|-------------------|---------|--------|
| `qwen2.5:0.5b`    | ~400 MB | Por defecto. El más rápido en CPU. |
| `llama3.2:1b`     | ~1,3 GB | Si quieres respuestas menos absurdas. |
| `qwen2.5:1.5b`    | ~1 GB   | Punto medio. |

Las pruebas **descubren** el modelo preguntándole al servidor qué tiene
(`GET /v1/models`), así que no hay ningún nombre de modelo escrito en el código de los
tests que pueda quedarse viejo. Si tienes varios, se elige el más pequeño de los
conocidos; para forzar uno concreto, la variable `LAPLACE_LOCAL_MODEL` (ver más abajo).

**Si acabas de instalar Ollama y la terminal dice que no reconoce `ollama`**, cierra la
terminal y abre otra: el instalador añade `ollama` al `PATH`, pero las ventanas que ya
estaban abiertas no se enteran.

## 3. Comprobar que responde

En Windows y macOS ya debería estar en marcha. En Linux, o si el paso siguiente falla,
arráncalo en una terminal aparte y déjala abierta:

```bash
ollama serve
```

(Si dice que la dirección ya está en uso, es que ya estaba corriendo: mejor.)

La comprobación que de verdad importa, porque es exactamente lo que hacen las pruebas
para decidir si se saltan o no:

```bash
curl http://localhost:11434/v1/models
```

Tiene que devolver un JSON con una lista `data` y tu modelo dentro. Si devuelve eso, ya
está todo listo. **Ojo si la lista sale vacía** (`"data":[]`): el servidor está en marcha
pero no tiene modelo, y las pruebas se saltarán todas. Falta el paso 2.

## 4. Lanzar las pruebas

`pytest` vive en el entorno virtual del proyecto, no en el Python del sistema. Si escribes
`pytest` y PowerShell dice que no reconoce el término, es eso: activa el entorno en esa
terminal, desde la raíz del repositorio.

```bash
.\.venv\Scripts\Activate.ps1
```

(Si PowerShell se niega a ejecutar el script por la política de ejecución, sáltate la
activación y usa el Python del entorno directamente: sustituye `pytest` por
`.\.venv\Scripts\python.exe -m pytest` en los comandos de abajo.)

```bash
pytest apps/backend/tests/test_modelo_local.py apps/backend/tests/test_modelo_local_simulado.py -v
```

O sólo las que necesitan el servidor, en cualquier parte del repositorio:

```bash
pytest apps/backend/tests -m modelo_local -v
```

Cuenta con que la **primera** llamada tarde bastante: Ollama carga los pesos a memoria
en ese momento. Con `qwen2.5:0.5b` en un portátil sin GPU, la tanda entera es cuestión
de un minuto o dos.

Lo que vas a ver:

- `test_modelo_local.py` — el camino de verdad. Llamada normal, streaming con recuento
  del servidor, streaming sin recuento (que tiene que salir **marcado como estimado**),
  stream abandonado a medias, cliente asíncrono, error del servidor y error de red. Y
  que un modelo sin tarifa **no cuesta cero, cuesta «no lo sabemos»**.
- `test_modelo_local_simulado.py` — la caché, que es lo único que el modelo local no
  puede darnos. Está separado y marcado a propósito. Cuatro de sus seis pruebas no
  necesitan servidor: comprueban que la forma de nuestros bloques de uso simulados es la
  que declaran los modelos Pydantic **de los propios SDK** de OpenAI y Anthropic, y que la
  misma llamada da los mismos tokens por los dos proveedores.

## Variables de entorno

| Variable | Para qué | Por defecto |
|----------|----------|-------------|
| `LAPLACE_LOCAL_BASE_URL` | Dónde escucha el servidor. Para LM Studio, `http://localhost:1234/v1`. | `http://localhost:11434/v1` |
| `LAPLACE_LOCAL_MODEL` | Forzar un modelo en vez de descubrirlo. | se descubre |

En PowerShell una variable se pone antes, en su propia orden, y vale para esa terminal
hasta que la cierres:

```bash
$env:LAPLACE_LOCAL_MODEL = "llama3.2:1b"
```

(En bash y zsh se antepone al comando: `LAPLACE_LOCAL_MODEL=llama3.2:1b pytest ...`.)

No hay variable de clave y no hace falta ninguna: el SDK de OpenAI se niega a
construirse sin `api_key`, así que se le pasa una cadena falsa que el servidor local
ignora. Está en el código con ese nombre —`CLAVE_FICTICIA`— para que se lea de un tirón
lo que es.

## Si algo no va

**«no hay servidor de modelos local escuchando en…» y todo se salta.** Es el
comportamiento correcto cuando no hay servidor, no un fallo. Vuelve al paso 3 y mira que
el `curl` devuelva la lista.

**«el servidor … no tiene ningún modelo».** Falta el paso 2: `ollama pull qwen2.5:0.5b`.

**«tu servidor local no manda `usage` al final del stream».** Tu Ollama es de antes de
que implementara `stream_options`. Actualízalo (`winget upgrade Ollama.Ollama`, o el
instalador otra vez). Mientras, el resto de las pruebas corre: se pierden las tres que
necesitan el recuento del servidor en streaming, y cada una lo dice al saltarse.

**Un `timeout` en la primera prueba.** El primer `create` carga el modelo y en una
máquina lenta puede pasar de los dos minutos que están puestos de tope. Prueba con un
modelo más pequeño, o haz una llamada a mano antes para que quede cargado:

```bash
ollama run qwen2.5:0.5b "hola"
```

**El puerto 11434 ocupado por otra cosa.** Arranca Ollama en otro puerto, en una
terminal aparte que dejas abierta:

```bash
$env:OLLAMA_HOST = "127.0.0.1:11500"; ollama serve
```

Y en la terminal de las pruebas, antes de lanzarlas:

```bash
$env:LAPLACE_LOCAL_BASE_URL = "http://127.0.0.1:11500/v1"
```

## Lo que sigue esperando una clave de verdad

Cuatro pruebas de `test_proveedores_reales.py`, y son las únicas que pueden comprobar lo
que ninguna de las de aquí comprueba: **que los tokens que Laplace guarda son los que el
proveedor dice haber cobrado**. Se encienden así, y cuestan unos céntimos:

```bash
$env:LAPLACE_LIVE_TESTS = "1"; $env:OPENAI_API_KEY = "..."; $env:ANTHROPIC_API_KEY = "..."
```

```bash
pytest apps/backend/tests/test_proveedores_reales.py
```

Hasta que se pongan, el modelo de coste está validado contra la aritmética que
escribimos nosotros y contra los precios publicados, **no contra una factura**.
