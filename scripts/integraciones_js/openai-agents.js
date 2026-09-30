// El Agents SDK de OpenAI para TypeScript, con la instrumentación de OpenInference, que
// se engancha a su propio sistema de trazas. Va por la Responses API.
const { montar, PROVEEDOR } = require("./otel");
const provider = montar("js-openai-agents");
const agents = require("@openai/agents");
const { OpenAIAgentsInstrumentation } = require("@arizeai/openinference-instrumentation-openai-agents");
new OpenAIAgentsInstrumentation({ tracerProvider: provider }).manuallyInstrument(agents);
const OpenAI = require("openai");
agents.setDefaultOpenAIClient(new OpenAI({ apiKey: "sk-de-mentira", baseURL: PROVEEDOR + "/v1" }));
const agente = new agents.Agent({ name: "equipaje", instructions: "Eres un asistente de equipaje.", model: "gpt-5.6-luna" });
(async () => {
  const r = await agents.run(agente, "¿Cuánto equipaje puedo llevar?");
  console.log("agents run:", r.finalOutput);
  const s = await agents.run(agente, "¿Cuánto equipaje puedo llevar?", { stream: true });
  let t = ""; for await (const e of s.toTextStream()) t += e; await s.completed;
  console.log("agents stream:", t);
  await agents.getGlobalTraceProvider().forceFlush();
  await provider.forceFlush();
})();
