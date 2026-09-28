# Agentes en TypeScript

Todavía no hay SDK de Laplace para Node. No hace falta para empezar: la ingesta es OTLP
estándar y entiende las dos familias de instrumentación para LLM que ya existen en
TypeScript, **OpenInference** (Arize) y **OpenLLMetry** (Traceloop) (D-136). Esta guía
dice cómo apuntarlas a Laplace y qué se ve con cada una.

Probado el 26 de septiembre de 2026 con Node 24, `openai` 7.23.0,
`@opentelemetry/sdk-trace-node` 2.11.0, los exportadores OTLP 0.222.0,
`@arizeai/openinference-instrumentation-openai` 4.2.7 y `@traceloop/node-server-sdk`
0.27.0, contra `laplace ui` en local. Lo que no está en esa lista no se ha probado.

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

**Qué se ve:** modelo, tokens, coste, mensajes y árbol, y los `withWorkflow` /
`withTask` como pasos.

**Qué no se ve:** con OpenAI, la versión 0.27.0 **no manda los tokens leídos de caché**
aunque la respuesta los traiga. Laplace no tiene cómo saberlo, así que cobra toda la
entrada a tarifa entera: en un agente con caché, el coste sale **por encima** de la
factura. Si usas OpenAI y la caché importa, usa OpenInference.

## Lo que conviene saber

- **Los pasos.** Laplace distingue un paso de otro por las instrucciones (el prompt de
  sistema) y por desde dónde se llama. Con estos instrumentadores sólo llega lo primero:
  dos pasos con el mismo prompt de sistema se agrupan juntos aunque cuelguen de spans
  distintos. Con el SDK de Python no pasa, porque `@observe` escribe el camino.
- **Gestión de prompts, `laplace.guard` y el resto de ayudas del SDK de Python** no
  existen en TypeScript. Llegarán con un paquete fino (`init`, `observe`, `getPrompt`)
  cuando esté reservado el nombre en npm.
- **Sin probar:** Anthropic por estas dos vías, el AI SDK de Vercel
  (`experimental_telemetry`) y LangChain.js. Deberían llegar, porque todos emiten
  atributos de una de las familias que la ingesta entiende, pero nadie lo ha
  comprobado; si lo pruebas y algo falta, es un fallo nuestro.
