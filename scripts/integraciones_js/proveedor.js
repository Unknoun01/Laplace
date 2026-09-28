// Proveedor falso con la forma documentada de Anthropic y OpenAI. Uso fijo y conocido
// para poder comparar lo que llega a Laplace: 176 nuevos + 1024 de caché, 12 de salida.
const http = require("http");
const TEXTO = ["Una maleta ", "de mano."];
const peticiones = [];

function sse(res, eventos, conNombre = true) {
  res.writeHead(200, { "content-type": "text/event-stream" });
  for (const e of eventos) {
    if (conNombre && e.type) res.write(`event: ${e.type}\n`);
    res.write(`data: ${JSON.stringify(e)}\n\n`);
  }
  if (!conNombre) res.write("data: [DONE]\n\n");
  res.end();
}

function anthropic(req, res, cuerpo) {
  const model = cuerpo.model;
  const uso = { input_tokens: 176, output_tokens: 12, cache_read_input_tokens: 1024, cache_creation_input_tokens: 0 };
  if (!cuerpo.stream) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({
      id: "msg_1", type: "message", role: "assistant", model,
      content: [{ type: "text", text: TEXTO.join("") }],
      stop_reason: "end_turn", stop_sequence: null, usage: uso,
    }));
  }
  sse(res, [
    { type: "message_start", message: { id: "msg_1", type: "message", role: "assistant", model, content: [], stop_reason: null, stop_sequence: null, usage: { ...uso, output_tokens: 1 } } },
    { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
    ...TEXTO.map((t) => ({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: t } })),
    { type: "content_block_stop", index: 0 },
    { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 12 } },
    { type: "message_stop" },
  ]);
}

function chat(req, res, cuerpo) {
  const model = cuerpo.model;
  const usage = { prompt_tokens: 1200, completion_tokens: 12, total_tokens: 1212, prompt_tokens_details: { cached_tokens: 1024 } };
  if (!cuerpo.stream) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({
      id: "chatcmpl-1", object: "chat.completion", created: 1770000000, model,
      choices: [{ index: 0, message: { role: "assistant", content: TEXTO.join("") }, finish_reason: "stop", logprobs: null }],
      usage,
    }));
  }
  const base = { id: "chatcmpl-1", object: "chat.completion.chunk", created: 1770000000, model };
  const trozos = TEXTO.map((t, i) => ({ ...base, choices: [{ index: 0, delta: i ? { content: t } : { role: "assistant", content: t }, finish_reason: null }] }));
  trozos.push({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
  if (cuerpo.stream_options && cuerpo.stream_options.include_usage) trozos.push({ ...base, choices: [], usage });
  sse(res, trozos, false);
}

function responses(req, res, cuerpo) {
  const model = cuerpo.model;
  const respuesta = {
    id: "resp_1", object: "response", created_at: 1770000000, status: "completed", model,
    output: [{ type: "message", id: "msg_1", status: "completed", role: "assistant", content: [{ type: "output_text", text: TEXTO.join(""), annotations: [] }] }],
    parallel_tool_calls: true, tool_choice: "auto", tools: [],
    usage: { input_tokens: 1200, input_tokens_details: { cached_tokens: 1024 }, output_tokens: 12, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 1212 },
  };
  if (!cuerpo.stream) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify(respuesta));
  }
  let n = 0;
  sse(res, [
    { type: "response.created", sequence_number: n++, response: { ...respuesta, status: "in_progress", output: [], usage: null } },
    { type: "response.output_item.added", sequence_number: n++, output_index: 0, item: { type: "message", id: "msg_1", status: "in_progress", role: "assistant", content: [] } },
    ...TEXTO.map((t) => ({ type: "response.output_text.delta", sequence_number: n++, item_id: "msg_1", output_index: 0, content_index: 0, delta: t, logprobs: [] })),
    { type: "response.output_item.done", sequence_number: n++, output_index: 0, item: respuesta.output[0] },
    { type: "response.completed", sequence_number: n++, response: respuesta },
  ]);
}

http.createServer((req, res) => {
  let datos = "";
  req.on("data", (d) => (datos += d));
  req.on("end", () => {
    const cuerpo = datos ? JSON.parse(datos) : {};
    peticiones.push({ url: req.url, model: cuerpo.model, stream: !!cuerpo.stream });
    console.log(req.method, req.url, cuerpo.model, cuerpo.stream ? "stream" : "");
    if (req.url.endsWith("/messages")) return anthropic(req, res, cuerpo);
    if (req.url.endsWith("/chat/completions")) return chat(req, res, cuerpo);
    if (req.url.endsWith("/responses")) return responses(req, res, cuerpo);
    res.writeHead(404); res.end("{}");
  });
}).listen(8200, () => console.log("proveedor falso en 8200"));
