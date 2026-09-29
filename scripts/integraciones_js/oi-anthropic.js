// OpenInference con Anthropic: create, create en streaming y messages.stream().
const { montar, PROVEEDOR } = require("./otel");
const provider = montar("js-oi-anthropic");
const { AnthropicInstrumentation } = require("@arizeai/openinference-instrumentation-anthropic");
const inst = new AnthropicInstrumentation();
inst.setTracerProvider(provider);
const Anthropic = require("@anthropic-ai/sdk");
inst.manuallyInstrument(Anthropic);
const cliente = new Anthropic({ apiKey: "sk-de-mentira", baseURL: PROVEEDOR });
const pedir = { model: "claude-haiku-4-5", max_tokens: 64, system: "Eres un asistente de equipaje.", messages: [{ role: "user", content: "¿Cuánto equipaje puedo llevar?" }] };
(async () => {
  const r = await cliente.messages.create(pedir);
  console.log("create:", r.content[0].text);
  let t = "";
  for await (const e of await cliente.messages.create({ ...pedir, stream: true })) if (e.type === "content_block_delta") t += e.delta.text;
  console.log("create stream:", t);
  const m = await cliente.messages.stream(pedir).finalMessage();
  console.log("messages.stream:", m.content[0].text);
  await provider.forceFlush();
})();
