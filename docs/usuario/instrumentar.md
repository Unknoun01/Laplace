# Instrumentar tu agente

## Python

```python
import laplace

laplace.init(project="mi-agente")  # endpoint y clave, de LAPLACE_ENDPOINT y LAPLACE_API_KEY
```

A partir de ahí, cada llamada a OpenAI o Anthropic se traza sola: Chat Completions, la
Responses API, `messages.create` y `messages.stream()`, lo que va por `client.beta`, con
streaming o sin él.

Para que los pasos de tu agente aparezcan en el árbol, decóralos:

```python
@laplace.observe(type="agent")
def atender(pregunta: str) -> str:
    ...

@laplace.observe(type="tool")
def buscar_tarifa(ruta: str) -> str:
    ...
```

**Quién es quién.** Para ver el gasto por usuario, por conversación o por cliente que
paga:

```python
laplace.set_context(user_id="u-7", session_id="conv-42", customer_id="acme")
```

`customer_id` es lo que usa la pestaña **Clientes** para decir qué clientes te hacen
perder dinero.

**Otros proveedores** (un gateway propio, un modelo auto-alojado): `laplace.llm_span`
emite los mismos atributos que las integraciones automáticas, y el coste se calcula
igual.

**Un límite por ejecución.** Para que un agente no gaste más de la cuenta ni se quede
dando vueltas:

```python
with laplace.guard(max_usd_per_run=0.50, max_loop=5):
    agente.run(pregunta)
```

Si se pasa, la llamada siguiente no se hace y salta `laplace.GuardExceeded`. El bucle es
el mismo que señala el Diagnóstico: el mismo paso, con la misma entrada, sin avanzar.

Los mismos límites, y un botón para parar todos los agentes del proyecto, se ponen
también en **Ajustes → Tope y parada**, sin tocar el código. El SDK los pide cada 30
segundos; se suman a los del código y manda el más estricto. Sin `guard` en el código,
cada ejecución es el `@observe` más externo. Si Laplace no responde, el agente sigue con
lo último que recibió, o sin límites si nunca recibió nada.

## TypeScript y Node

Todavía no hay SDK de Laplace para Node, y no hace falta: la ingesta entiende
OpenInference, OpenLLMetry, el AI SDK de Vercel, LangChain.js y LangGraph.js, el Agents
SDK de OpenAI y Mastra. Cómo apuntar cada uno a Laplace, y qué se ve con cada uno, está
en [`docs/typescript.md`](../typescript.md).

## Cualquier otra cosa

La ingesta es OTLP estándar (`/v1/traces`, protobuf o JSON). Todo lo que emita las
convenciones GenAI de OpenTelemetry llega con modelo, tokens y coste.

## Si ya usas otro instrumentador

Si tu proceso ya lleva OpenInference u OpenLLMetry (LangGraph, CrewAI, LlamaIndex…),
Laplace no traza otra vez las mismas llamadas, porque contarían dos veces. Si ese
instrumentador exporta a otro sitio y las quieres también aquí:
`laplace.init(defer_to_others=False)`.
