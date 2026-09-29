// OpenLLMetry (Traceloop) con Anthropic.
const traceloop = require("@traceloop/node-server-sdk");
const Anthropic = require("@anthropic-ai/sdk");
traceloop.initialize({ appName: "js-ol-anthropic", baseUrl: "http://localhost:8100", apiKey: "local", disableBatch: true, instrumentModules: { anthropic: Anthropic }, silenceInitializationMessage: true });
const cliente = new Anthropic({ apiKey: "sk-de-mentira", baseURL: require("./otel").PROVEEDOR });
const pedir = { model: "claude-haiku-4-5", max_tokens: 64, system: "Eres un asistente de equipaje.", messages: [{ role: "user", content: "¿Cuánto equipaje puedo llevar?" }] };
(async () => {
  await traceloop.withWorkflow({ name: "atender" }, async () => {
    const r = await cliente.messages.create(pedir);
    console.log("create:", r.content[0].text);
    let t = "";
    for await (const e of await cliente.messages.create({ ...pedir, stream: true })) if (e.type === "content_block_delta") t += e.delta.text;
    console.log("create stream:", t);
  });
  await traceloop.forceFlush();
})();
