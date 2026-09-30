// LangGraph.js con la instrumentación de LangChain de OpenInference: un grafo de dos
// nodos que llaman al modelo con el MISMO prompt de sistema. Tienen que salir como dos
// pasos distintos (el nodo es el sitio del paso, D-141).
const { montar, PROVEEDOR } = require("./otel");
const provider = montar("js-langgraph");
const { LangChainInstrumentation } = require("@arizeai/openinference-instrumentation-langchain");
const inst = new LangChainInstrumentation();
inst.setTracerProvider(provider);
inst.manuallyInstrument(require("@langchain/core/callbacks/manager"));
const { StateGraph, Annotation, START, END } = require("@langchain/langgraph");
const { ChatOpenAI } = require("@langchain/openai");
const { SystemMessage, HumanMessage } = require("@langchain/core/messages");

const modelo = new ChatOpenAI({ model: "gpt-5.6-luna", apiKey: "sk-de-mentira", useResponsesApi: false, configuration: { baseURL: PROVEEDOR + "/v1" } });
const Estado = Annotation.Root({ pregunta: Annotation(), clase: Annotation(), respuesta: Annotation() });
const llamar = async (texto) => (await modelo.invoke([new SystemMessage("Eres un asistente de equipaje."), new HumanMessage(texto)])).content;
const grafo = new StateGraph(Estado)
  .addNode("clasificar", async (s) => ({ clase: await llamar(s.pregunta) }))
  .addNode("responder", async (s) => ({ respuesta: await llamar(s.pregunta) }))
  .addEdge(START, "clasificar").addEdge("clasificar", "responder").addEdge("responder", END)
  .compile();
(async () => {
  const r = await grafo.invoke({ pregunta: "¿Cuánto equipaje puedo llevar?" });
  console.log("langgraph:", r.clase, "/", r.respuesta);
  await provider.forceFlush();
})();
