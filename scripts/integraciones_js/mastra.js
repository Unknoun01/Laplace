// Mastra, con su propia observabilidad exportando OTLP a Laplace (`@mastra/otel-exporter`
// con el proveedor `custom`). No usa el `NodeTracerProvider` de los demás: Mastra monta
// el suyo, y el proyecto de Laplace es su `serviceName`.
const { Mastra } = require("@mastra/core/mastra");
const { Agent } = require("@mastra/core/agent");
const { Observability } = require("@mastra/observability");
const { OtelExporter } = require("@mastra/otel-exporter");
const { createOpenAI } = require("@ai-sdk/openai");
const { PROVEEDOR } = require("./otel");
const openai = createOpenAI({ apiKey: "sk-de-mentira", baseURL: PROVEEDOR + "/v1" });
const agente = new Agent({ id: "equipaje", name: "equipaje", instructions: "Eres un asistente de equipaje.", model: openai.chat("gpt-5.6-luna") });
const exportador = new OtelExporter({ provider: { custom: { endpoint: "http://localhost:8100/v1/traces", protocol: "http/protobuf" } } });
const mastra = new Mastra({
  agents: { equipaje: agente },
  observability: new Observability({ configs: { otel: { serviceName: "js-mastra", exporters: [exportador] } } }),
});
(async () => {
  const a = mastra.getAgent("equipaje");
  const r = await a.generate("¿Cuánto equipaje puedo llevar?");
  console.log("mastra generate:", r.text);
  const s = await a.stream("¿Cuánto equipaje puedo llevar?");
  let t = ""; for await (const p of s.textStream) t += p;
  console.log("mastra stream:", t);
  await mastra.shutdown?.();
  await exportador.shutdown?.();
})();
