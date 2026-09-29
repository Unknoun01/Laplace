const { NodeTracerProvider } = require("@opentelemetry/sdk-trace-node");
const { SimpleSpanProcessor } = require("@opentelemetry/sdk-trace-base");
const { resourceFromAttributes } = require("@opentelemetry/resources");
const { OTLPTraceExporter } = require("@opentelemetry/exporter-trace-otlp-proto");

function montar(proyecto, procesadores = null) {
  const exportador = new OTLPTraceExporter({ url: "http://localhost:8100/v1/traces" });
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({ "laplace.project.id": proyecto }),
    spanProcessors: procesadores ? procesadores(exportador) : [new SimpleSpanProcessor(exportador)],
  });
  provider.register();
  return provider;
}
module.exports = { montar, PROVEEDOR: "http://localhost:8200" };
