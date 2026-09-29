// El AI SDK de Vercel 7: `telemetry` y la integración de `@ai-sdk/otel`, sin más.
const { montar, PROVEEDOR } = require("./otel");
const provider = montar(process.argv[2] || "js-vercel");
const { generateText, streamText, registerTelemetry } = require("ai");
const { OpenTelemetry } = require("@ai-sdk/otel");
registerTelemetry(new OpenTelemetry());
const { createOpenAI } = require("@ai-sdk/openai");
const { createAnthropic } = require("@ai-sdk/anthropic");
const openai = createOpenAI({ apiKey: "sk-de-mentira", baseURL: PROVEEDOR + "/v1" });
const anthropic = createAnthropic({ apiKey: "sk-de-mentira", baseURL: PROVEEDOR + "/v1" });
const tel = { isEnabled: true, functionId: "equipaje" };
const sistema = "Eres un asistente de equipaje.";
const prompt = "¿Cuánto equipaje puedo llevar?";
(async () => {
  for (const [nombre, modelo] of [["openai", openai("gpt-5.6-luna")], ["openai-chat", openai.chat("gpt-5.6-luna")], ["anthropic", anthropic("claude-haiku-4-5")]]) {
    const r = await generateText({ model: modelo, system: sistema, prompt, telemetry: tel });
    console.log(nombre, "generateText:", r.text, JSON.stringify(r.usage));
    const s = streamText({ model: modelo, system: sistema, prompt, telemetry: tel });
    let t = ""; for await (const p of s.textStream) t += p;
    console.log(nombre, "streamText:", t, JSON.stringify(await s.usage));
  }
  await provider.forceFlush();
})();
