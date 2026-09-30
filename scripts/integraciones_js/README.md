# Agentes en Node contra Laplace

El banco con el que se prueban las integraciones de TypeScript de `docs/typescript.md`
(D-165 y D-170). Siete agentes, cada uno a su proyecto, llaman a un **proveedor falso** con la
forma documentada de Anthropic y OpenAI (sin clave ni gasto) y mandan sus trazas a
`laplace ui`. El uso es fijo —1.200 de entrada con 1.024 de caché y 12 de salida— para
comparar con lo que llega.

| Agente | Integración | Proyecto |
|---|---|---|
| `oi-anthropic.js` | OpenInference con `@anthropic-ai/sdk` (`create`, en streaming y `messages.stream()`) | `js-oi-anthropic` |
| `ol-anthropic.js` | OpenLLMetry (Traceloop) con `@anthropic-ai/sdk` | `js-ol-anthropic` |
| `vercel.js` | AI SDK de Vercel 7 (`generateText` y `streamText` con OpenAI Responses, OpenAI Chat y Anthropic) | `js-vercel` |
| `langchain.js` | LangChain.js con OpenInference (`ChatOpenAI` y `ChatAnthropic`, `invoke` y `stream`) | `js-langchain` |
| `langgraph.js` | LangGraph.js: un grafo de dos nodos con el mismo prompt de sistema, que tienen que salir como dos pasos | `js-langgraph` |
| `openai-agents.js` | El Agents SDK de OpenAI con OpenInference (`run` y en streaming, por la Responses API) | `js-openai-agents` |
| `mastra.js` | Mastra con su exportador de OpenTelemetry (`generate` y `stream`) | `js-mastra` |

```bash
cd scripts/integraciones_js
npm install                     # versiones fijadas en package.json
node proveedor.js &             # el proveedor falso, en el 8200
laplace ui --port 8100 --no-browser --db /tmp/js.db &
npm run agentes
python verificar.py             # sale con 1 y dice qué falta si algo no llega
```

`verificar.py` exige de cada llamada al modelo: modelo, tokens con la caché dentro,
coste medido, el prompt de sistema como primer mensaje y la respuesta en texto. Usa una
base de datos limpia: cuenta las llamadas de cada proyecto.

Al subir de versión alguna de estas librerías, se cambia en `package.json`, se pasa el
banco y se actualizan las versiones de «Probado con» en `docs/typescript.md`.
