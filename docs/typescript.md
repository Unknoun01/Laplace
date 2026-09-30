# Agentes en TypeScript

Todavía no hay SDK de Laplace para Node. No hace falta para empezar: la ingesta es OTLP
estándar y entiende las dos familias de instrumentación para LLM que ya existen en
TypeScript, **OpenInference** (Arize) y **OpenLLMetry** (Traceloop) (D-136). Esta guía
dice cómo apuntarlas a Laplace y qué se ve con cada una.

Probado el 26 de septiembre de 2026 con Node 24, `openai` 7.23.0,
`@opentelemetry/sdk-trace-node` 2.11.0, los exportadores OTLP 0.222.0,
`@arizeai/openinference-instrumentation-openai` 4.2.7 y `@traceloop/node-server-sdk`
0.27.0, contra `laplace ui` en local.

Probado el 28 de septiembre de 2026 con Node 22, contra `laplace ui` y un proveedor
falso con la forma documentada de cada API (D-165): **Anthropic** (`@anthropic-ai/sdk`
0.129.0) por OpenInference (`@arizeai/openinference-instrumentation-anthropic` 0.2.8) y
por OpenLLMetry 0.27.0; el **AI SDK de Vercel** (`ai` 7.0.122 con `@ai-sdk/otel`
1.0.122, `@ai-sdk/openai` 4.0.80 y `@ai-sdk/anthropic` 4.0.68); y **LangChain.js**
(`@langchain/core` 1.2.13, `@langchain/openai` 1.6.0 y `@langchain/anthropic` 1.5.11,
con `@arizeai/openinference-instrumentation-langchain` 4.1.1). El banco está en
`scripts/integraciones_js` y se puede repetir. Lo que no está en estas listas no se ha
probado.

## Con OpenInference (recomendado para OpenAI)

```bash
npm install openai @opentelemetry/sdk-trace-node @opentelemetry/sdk-trace-base \
  @opentelemetry/resources @opentelemetry/exporter-trace-otlp-proto \
  @arizeai/openinference-instrumentation-openai
```

```js
const { NodeTracerProvider } = require("@opentelemetry/sdk-trace-node");
const { BatchSpanProcessor } = require("@opentelemetry/sdk-trace-base");
const { resourceFromAttributes } = require("@opentelemetry/resources");
const { OTLPTraceExporter } = require("@opentelemetry/exporter-trace-otlp-proto");
const { OpenAIInstrumentation } = require("@arizeai/openinference-instrumentation-openai");

const provider = new NodeTracerProvider({
  // El proyecto de Laplace. Sin él, se usa `service.name`, y si tampoco, «default».
  resource: resourceFromAttributes({ "laplace.project.id": "mi-agente" }),
  spanProcessors: [
    new BatchSpanProcessor(
      new OTLPTraceExporter({
        url: "http://localhost:8100/v1/traces", // `laplace ui` escucha en el 8100
        // En la nube, la clave del proyecto. En local no hace falta.
        headers: { Authorization: `Bearer ${process.env.LAPLACE_API_KEY}` },
      }),
    ),
  ],
});
provider.register();

const instrumentacion = new OpenAIInstrumentation();
instrumentacion.setTracerProvider(provider);
const OpenAI = require("openai");
// Así está probado. Sin esta línea el parcheo depende de cómo se cargue `openai`
// (CommonJS o ESM, antes o después de registrar), y un parche que no llega no avisa.
instrumentacion.manuallyInstrument(OpenAI);
```

Antes de salir de un script corto: `await provider.forceFlush()`.

El exportador puede ser el de protobuf (`exporter-trace-otlp-proto`) o el de JSON
(`exporter-trace-otlp-http`); los dos están probados. Con el de JSON hace falta Laplace
posterior a D-139: antes, los ids de traza en hexadecimal que manda ese exportador se
leían mal.

**Qué se ve:** cada llamada como span de LLM con modelo, tokens de entrada y salida,
**lecturas de caché**, coste, los mensajes enteros y el árbol de la traza. Las reglas de
derroche funcionan igual que con el SDK de Python.

**Con Anthropic** es igual, con `@arizeai/openinference-instrumentation-anthropic`:

```js
const { AnthropicInstrumentation } = require("@arizeai/openinference-instrumentation-anthropic");
const instrumentacion = new AnthropicInstrumentation();
instrumentacion.setTracerProvider(provider);
const Anthropic = require("@anthropic-ai/sdk");
instrumentacion.manuallyInstrument(Anthropic);
```

Se ven `messages.create`, con y sin streaming, y `messages.stream()`, con la caché
leída. Esta instrumentación deja el `system` fuera de los mensajes, en los parámetros de
la llamada; Laplace lo vuelve a poner como mensaje de sistema, que es lo que identifica
el paso (hace falta Laplace posterior a D-165).

## El cliente de cada ejecución (margen por cliente)

Para el margen por cliente (la pestaña de Clientes), cada ejecución tiene que decir para
qué cliente es. Sin SDK de Laplace para Node no hay `setContext`, pero no hace falta: es
el atributo `laplace.customer.id` en el span que envuelve la ejecución. Basta en uno —la
raíz— y vale con cualquiera de las dos instrumentaciones de esta guía o sin ninguna.

```js
const { trace } = require("@opentelemetry/api");
const tracer = trace.getTracer("mi-agente");

async function atender(cliente, pregunta) {
  return tracer.startActiveSpan("atender", async (span) => {
    span.setAttribute("laplace.span.type", "agent");
    span.setAttribute("laplace.customer.id", cliente); // quien paga por este trabajo
    try {
      return await miAgente(pregunta); // las llamadas al modelo cuelgan de aquí
    } finally {
      span.end();
    }
  });
}
```

Probado el 28 de septiembre de 2026 con Node 22, `@opentelemetry/api` 1.9.1,
`@opentelemetry/sdk-trace-node` 2.11.0 y `@opentelemetry/exporter-trace-otlp-http` 0.222.0
contra `laplace ui`: cada ejecución llega con su cliente y cuenta en su margen (D-163).
Lo mismo vale para `laplace.user.id` y `laplace.session.id`.

## Con OpenLLMetry (Traceloop)

```bash
npm install openai @traceloop/node-server-sdk
```

```js
const traceloop = require("@traceloop/node-server-sdk");
const OpenAI = require("openai");

traceloop.initialize({
  appName: "mi-agente", // es el proyecto de Laplace
  baseUrl: "http://localhost:8100", // Traceloop añade /v1/traces
  apiKey: process.env.LAPLACE_API_KEY, // en local cualquier valor
  instrumentModules: { openAI: OpenAI },
});
```

Con Anthropic, `instrumentModules: { anthropic: Anthropic }` (el módulo de
`@anthropic-ai/sdk`).

**Qué se ve:** modelo, tokens, coste, mensajes y árbol, y los `withWorkflow` /
`withTask` como pasos. Con Anthropic, también la caché leída. OpenLLMetry ya usa las
convenciones GenAI actuales (los mensajes en `parts` y el prompt de sistema aparte, en
`gen_ai.system_instructions`): hace falta Laplace posterior a D-165 para ver los
mensajes y el prompt de sistema.

**Qué no se ve:** en streaming no manda el motivo de parada. Y con OpenAI, la versión
0.27.0 **no manda los tokens leídos de caché**
aunque la respuesta los traiga. Laplace no tiene cómo saberlo, así que cobra toda la
entrada a tarifa entera: en un agente con caché, el coste sale **por encima** de la
factura. Si usas OpenAI y la caché importa, usa OpenInference.

## Con el AI SDK de Vercel

Desde la versión 7 la telemetría no sale sola: se registra una vez la integración de
OpenTelemetry, y cada llamada lleva `telemetry` (antes `experimental_telemetry`, que la
versión 7 ya no lee: con él no llega **nada**).

```bash
npm install ai @ai-sdk/otel @ai-sdk/openai   # o @ai-sdk/anthropic
```

```js
// Primero el `NodeTracerProvider` apuntado a Laplace, como arriba, y después:
const { generateText, registerTelemetry } = require("ai");
const { OpenTelemetry } = require("@ai-sdk/otel");
registerTelemetry(new OpenTelemetry());

const { text } = await generateText({
  model: openai("gpt-5.6-luna"),
  system: "Eres un asistente de equipaje.",
  prompt: pregunta,
  telemetry: { functionId: "equipaje" }, // el nombre de la función, en la traza
});
```

**Qué se ve:** con OpenAI (la Responses API, que es la de por defecto, y Chat) y con
Anthropic, en `generateText` y en `streamText`: modelo, tokens, **caché leída**, coste,
mensajes con el prompt de sistema, motivo de parada, y la llamada colgando de su agente
y su paso (`invoke_agent` → `step 1` → `chat`). Usa las convenciones GenAI actuales, así
que hace falta Laplace posterior a D-165.

## Con LangChain.js

Con la instrumentación de LangChain de OpenInference, sobre el mismo `provider`:

```bash
npm install @arizeai/openinference-instrumentation-langchain
```

```js
const { LangChainInstrumentation } = require("@arizeai/openinference-instrumentation-langchain");
const CallbackManagerModule = require("@langchain/core/callbacks/manager");
const instrumentacion = new LangChainInstrumentation();
instrumentacion.setTracerProvider(provider);
instrumentacion.manuallyInstrument(CallbackManagerModule);
```

**Qué se ve:** `ChatOpenAI` y `ChatAnthropic`, en `invoke` y `stream`, con modelo,
tokens, caché leída, coste y los mensajes con el prompt de sistema. Con `ChatOpenAI` 1.6
(que va por la Responses API) la instrumentación no manda el texto de la respuesta
entre los mensajes; Laplace lo saca del resultado de LangChain (posterior a D-165).

**Qué no cuadra:** en streaming con Anthropic, LangChain.js 1.5 cuenta **un token de
salida de más** por llamada (suma el que anuncia `message_start` al total final).
Laplace guarda lo que dice LangChain: el coste de salida sale un token por encima.

## Con LangGraph.js

LangGraph va sobre LangChain, así que se instrumenta igual que LangChain.js, con
`@arizeai/openinference-instrumentation-langchain` sobre el mismo `provider`.

**Qué se ve:** el grafo entero en una traza (`LangGraph`, y debajo cada nodo), y cada
llamada al modelo con modelo, tokens, caché, coste y mensajes. **Cada nodo es su propio
paso**, aunque dos nodos usen el mismo prompt de sistema: la instrumentación deja el
nombre del nodo en los metadatos y Laplace lo usa como sitio del paso (D-141).

Probado el 30 de septiembre de 2026 con `@langchain/langgraph` 1.4.18.

## Con el Agents SDK de OpenAI

```bash
npm install @openai/agents @arizeai/openinference-instrumentation-openai-agents
```

```js
const agents = require("@openai/agents");
const { OpenAIAgentsInstrumentation } = require("@arizeai/openinference-instrumentation-openai-agents");
new OpenAIAgentsInstrumentation({ tracerProvider: provider }).manuallyInstrument(agents);
```

Se engancha al sistema de trazas del propio SDK. Por defecto **sustituye** al exportador
de OpenAI; con `manuallyInstrument(agents, { exclusiveProcessor: false })` los dos
conviven.

**Qué se ve:** cada ejecución (`Agent workflow`, el agente y cada turno) y cada llamada al
modelo con modelo, tokens, caché, coste, las instrucciones del agente como prompt de
sistema y la respuesta, con `run` y en streaming. Antes de salir de un script:
`await agents.getGlobalTraceProvider().forceFlush()` y después el del `provider`.

Probado el 30 de septiembre de 2026 con `@openai/agents` 0.18.0 y
`@arizeai/openinference-instrumentation-openai-agents` 0.2.15.

## Con Mastra

Mastra trae su propia observabilidad. Se apunta a Laplace con su exportador de
OpenTelemetry, sin `NodeTracerProvider`; el proyecto de Laplace es el `serviceName`:

```bash
npm install @mastra/observability @mastra/otel-exporter @opentelemetry/exporter-trace-otlp-proto
```

```js
const { Observability } = require("@mastra/observability");
const { OtelExporter } = require("@mastra/otel-exporter");

const mastra = new Mastra({
  agents: { equipaje },
  observability: new Observability({
    configs: {
      otel: {
        serviceName: "mi-agente",
        exporters: [
          new OtelExporter({
            provider: {
              custom: { endpoint: "http://localhost:8100/v1/traces", protocol: "http/protobuf" },
            },
          }),
        ],
      },
    },
  }),
});
```

**Qué se ve:** cada ejecución del agente, sus pasos y cada llamada al modelo con modelo,
tokens, caché, coste, mensajes y motivo de parada, con `generate` y `stream`. Mastra no
pone los mensajes en su span de la llamada al modelo sino en el paso que la envuelve;
Laplace los toma de ahí si llegan en el mismo lote, que es lo normal, porque el paso
termina justo después de la llamada. Hace falta Laplace posterior a D-170.

Probado el 30 de septiembre de 2026 con `@mastra/core` 1.72.0, `@mastra/observability`
1.18.2 y `@mastra/otel-exporter` 1.4.3.

## Lo que conviene saber

- **Los pasos.** Laplace distingue un paso de otro por las instrucciones (el prompt de
  sistema) y por desde dónde se llama. Con estos instrumentadores sólo llega lo primero:
  dos pasos con el mismo prompt de sistema se agrupan juntos aunque cuelguen de spans
  distintos. Con el SDK de Python no pasa, porque `@observe` escribe el camino.
- **Gestión de prompts, `laplace.guard` y el resto de ayudas del SDK de Python** no
  existen en TypeScript. Llegarán con un paquete fino (`init`, `observe`, `getPrompt`)
  cuando esté reservado el nombre en npm.
- **Lo del 28 de septiembre se probó contra un proveedor falso, no contra la API
  real.** Responde con la forma documentada, pero una API real puede mandar algún campo
  que no esté ahí; si algo falta, es un fallo nuestro.
- **Sin probar contra las API reales:** nada de lo del 28 y el 30 de septiembre. Espera
  una clave de proveedor.
