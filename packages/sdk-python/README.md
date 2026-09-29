# laplace-trace

SDK de Python de [Laplace](https://github.com/Unknoun01/Laplace): observabilidad y
optimización de agentes de IA.

Cuando un agente falla, tarda o dispara la factura de tokens, los logs normales no
sirven: no capturan el árbol de decisiones. Laplace lo hace visible — cada paso, qué
prompt se envió, qué respondió el modelo, qué herramienta se llamó, cuántos tokens y
cuánto costó cada nodo.

## Instalación

```bash
pip install laplace-trace
```

## Uso

Una línea:

```python
import laplace

laplace.init(project="mi-agente")
```

### Verlo funcionando en local

```bash
pip install "laplace-trace[ui]"
laplace ui
```

Levanta el producto entero —ingesta, API e interfaz— contra un fichero SQLite en
`~/.laplace`. Sin cuenta, sin servidor y sin Docker; nada sale de tu máquina. Apunta el
SDK ahí con `endpoint="http://127.0.0.1:8100"`.

`laplace demo` manda unas trazas simuladas, en un proyecto aparte, para ver qué detecta
sin escribir código.

A partir de ahí, cada llamada a OpenAI o Anthropic se traza sola. Para que los pasos
propios del agente aparezcan en el árbol, decóralos:

```python
@laplace.observe(type="agent")
def responder(pregunta: str) -> str:
    plan = planificar(pregunta)
    datos = buscar(plan)
    return redactar(datos)

@laplace.observe(type="tool")
def buscar(query: str) -> list[str]:
    ...
```

Tipos de span: `agent`, `llm`, `tool`, `retrieval`, `chain` (por defecto).

### Spans manuales

```python
with laplace.span("planificación", type="chain") as s:
    plan = construir_plan()
    laplace.update_current_span(output=plan)
```

### Proveedores sin integración automática

Un gateway propio, un modelo auto-alojado, cualquier cosa. Emite los mismos atributos
que las integraciones automáticas, así que el coste se calcula igual:

```python
with laplace.llm_span(model="mistral-large-latest", system="mistral_ai",
                      input_messages=mensajes, temperature=0) as llm:
    respuesta = mi_cliente.chat(mensajes)
    llm.record_response(
        output_messages=[respuesta],
        input_tokens=respuesta.usage.in_,
        output_tokens=respuesta.usage.out,
    )
```

### Sesiones y usuarios

```python
laplace.set_context(session_id="conv-42", user_id="u-7")
```

Agrupa varias trazas de una misma conversación y permite ver el coste por usuario final.

### Probar el modelo barato sin escribir código

Cuando el Diagnóstico dice que un paso usa un modelo más caro de lo necesario, guarda
sus ejecuciones en Probar y reenvía sus llamadas reales al modelo barato:

```bash
laplace replay "ab-3f9c2e1a" --modelo gpt-5.6-luna --tope 1 --proyecto mi-agente
```

- **Corre en tu máquina, con tu clave** (la de `OPENAI_API_KEY` o `ANTHROPIC_API_KEY`).
  Laplace dice qué llamadas se pueden reenviar; no guarda claves de proveedor ni gasta
  nada por su cuenta.
- **No pasa del tope.** Antes de cada llamada suma lo peor que puede costar —la entrada
  original con un 30 % de margen y la salida máxima— y si se pasaría, no la hace. Un
  modelo sin tarifa no tiene tope que cumplir, y no se reenvía nada.
- **Pide permiso** antes de gastar: cuántas llamadas, lo que costaron y lo que
  costarán como mucho. `--si` se lo salta; sin nadie delante y sin `--si`, no gasta.
- **Sólo lo que no tiene efectos:** llamadas hoja del paso, sin herramientas, con los
  mensajes en texto. Lo demás se cuenta por motivo.

Deja dos tiradas del conjunto para comparar en Probar: «original», con el coste de
esas mismas llamadas, y la del modelo nuevo. Si el juez de Laplace está encendido,
juzga cada respuesta contra la que dio el original. Desde Python:
`laplace.replay_dataset("ab-3f9c2e1a", model="gpt-5.6-luna", max_usd=1)`.

## Configuración

Todo se puede fijar por entorno, para no tener que tocar el código en cada despliegue.

| Variable                   | Por defecto             | Qué hace                                        |
|----------------------------|-------------------------|-------------------------------------------------|
| `LAPLACE_PROJECT`          | `default`               | Proyecto al que van las trazas.                 |
| `LAPLACE_ENDPOINT`         | `http://localhost:4318` | Base OTLP/HTTP. El SDK añade `/v1/traces`.      |
| `LAPLACE_API_KEY`          | —                       | Se envía como `Authorization: Bearer`.          |
| `LAPLACE_CAPTURE_CONTENT`  | `true`                  | A `false` no se capturan prompts ni respuestas. |
| `LAPLACE_MAX_PAYLOAD_BYTES`| `1048576`               | Límite por payload. `none` = sin límite.        |
| `LAPLACE_DISABLED`         | `false`                 | Apaga la emisión sin tocar el código.           |
| `LAPLACE_EXIT_FLUSH_MS`    | `2000`                  | Tope de espera al envío final, al salir.        |
| `LAPLACE_DEBUG`            | `false`                 | Logs internos del SDK.                          |

En scripts cortos, llama a `laplace.flush()` antes de salir (o deja que lo haga el
`atexit` que registra `init()`).

## Qué emite

Spans de OpenTelemetry con las [convenciones semánticas GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
Lo que el estándar no cubre va bajo `laplace.*`. El contrato completo está en
[`docs/trace-contract.md`](../../docs/trace-contract.md).

Consecuencia práctica: puedes apuntar el SDK a cualquier colector OTel, y cualquier
proceso ya instrumentado con OTel puede exportar a Laplace sin usar este SDK.

## Qué se traza solo

- **OpenAI**: `chat.completions.create` y `.parse`, y la Responses API
  (`responses.create` y `.parse`, la que usa el Agents SDK). Los ayudantes `.stream()`
  de las dos pasan por `create` y también se ven. Por `client.beta`, `chat.completions`
  (es la misma clase) y `responses.create`.
- **Anthropic**: `messages.create`, `.parse` y el gestor `messages.stream()`, y lo mismo
  por `client.beta.messages` (gestión de contexto, compactación, servidores MCP…).
- Todo en síncrono y asíncrono, y en streaming con los tokens que manda el proveedor.
  Si no los manda (Chat sin `stream_options={"include_usage": True}`), se estiman y el
  span queda marcado como estimado.

**Si ya usas OpenInference u OpenLLMetry** (LangGraph, LlamaIndex, CrewAI…), las
llamadas al modelo ya las traza ese instrumentador. Mientras esté activo, Laplace no las
traza otra vez —si no, cada llamada contaría dos veces— y lo dice una vez en el log. Si
ese instrumentador exporta a otro sitio y quieres las llamadas también en Laplace,
`laplace.init(defer_to_others=False)` (o `LAPLACE_DEFER_TO_OTHERS=false`).

## Limitaciones conocidas

- Integraciones automáticas: OpenAI y Anthropic. Para el resto, `laplace.llm_span`, o
  cualquier instrumentación de OpenInference u OpenLLMetry apuntada a Laplace.
- `with_raw_response` se lee entero. `with_streaming_response` no, porque el cuerpo es
  del usuario: ahí los tokens se estiman y se marcan.
- De `client.beta` de OpenAI no se ven las Assistants (`threads.runs`), Realtime,
  ChatKit ni los agentes alojados: no pasan por estas puertas y no se instrumentan.

## Principio

Observar nunca puede romper **ni frenar** lo observado. Cualquier fallo interno del SDK
se traga y se registra en debug: la función instrumentada se ejecuta igual.

Medido con el backend apagado, apuntando a un puerto donde no escucha nadie:

- El agente termina y devuelve el mismo resultado; no se propaga ninguna excepción.
- `@observe` añade **0,24 ms por llamada** (frente a los cientos de milisegundos que
  cuesta cualquier llamada a un modelo).
- Salir del proceso cuesta como mucho `LAPLACE_EXIT_FLUSH_MS` (2 s por defecto). El
  `force_flush` de OpenTelemetry no respeta su propio timeout contra un endpoint muerto
  —se comía 9 s—, así que el envío final va en un hilo demonio con tope propio.

## Licencia

Apache-2.0.
