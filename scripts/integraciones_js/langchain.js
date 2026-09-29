// LangChain.js con la instrumentación de OpenInference.
const { montar, PROVEEDOR } = require("./otel");
const provider = montar("js-langchain");
const { LangChainInstrumentation } = require("@arizeai/openinference-instrumentation-langchain");
const CallbackManagerModule = require("@langchain/core/callbacks/manager");
const inst = new LangChainInstrumentation();
inst.setTracerProvider(provider);
inst.manuallyInstrument(CallbackManagerModule);
const { ChatOpenAI } = require("@langchain/openai");
const { ChatAnthropic } = require("@langchain/anthropic");
const { SystemMessage, HumanMessage } = require("@langchain/core/messages");
const msgs = [new SystemMessage("Eres un asistente de equipaje."), new HumanMessage("¿Cuánto equipaje puedo llevar?")];
(async () => {
  const oa = new ChatOpenAI({ model: "gpt-5.6-luna", apiKey: "sk-de-mentira", configuration: { baseURL: PROVEEDOR + "/v1" } });
  const an = new ChatAnthropic({ model: "claude-haiku-4-5", apiKey: "sk-de-mentira", anthropicApiUrl: PROVEEDOR });
  for (const [nombre, m] of [["openai", oa], ["anthropic", an]]) {
    const r = await m.invoke(msgs);
    console.log(nombre, "invoke:", r.content, JSON.stringify(r.usage_metadata));
    let t = ""; for await (const c of await m.stream(msgs)) t += typeof c.content === "string" ? c.content : "";
    console.log(nombre, "stream:", t);
  }
  await provider.forceFlush();
})();
