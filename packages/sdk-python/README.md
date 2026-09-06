# laplace-trace

SDK de Python de [Laplace](https://github.com/laplace-dev/laplace): observabilidad y
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
| `LAPLACE_DEBUG`            | `false`                 | Logs internos del SDK.                          |

En scripts cortos, llama a `laplace.flush()` antes de salir (o deja que lo haga el
`atexit` que registra `init()`).

## Qué emite

Spans de OpenTelemetry con las [convenciones semánticas GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
Lo que el estándar no cubre va bajo `laplace.*`. El contrato completo está en
[`docs/trace-contract.md`](../../docs/trace-contract.md).

Consecuencia práctica: puedes apuntar el SDK a cualquier colector OTel, y cualquier
proceso ya instrumentado con OTel puede exportar a Laplace sin usar este SDK.

## Limitaciones conocidas

- **Streaming**: las llamadas con `stream=True` se registran como span (latencia,
  modelo, parámetros, prompt) pero todavía no se acumulan los tokens ni el contenido
  de la respuesta. El span queda marcado con `laplace.streaming.captured = false`.
- Integraciones automáticas: OpenAI y Anthropic. Para el resto, `laplace.llm_span`.

## Principio

Observar nunca puede romper lo observado. Cualquier fallo interno del SDK se traga y se
registra en debug: la función instrumentada se ejecuta igual.

## Licencia

Apache-2.0.
