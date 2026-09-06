# @laplace/trace (TypeScript) — pendiente

Aquí irá el SDK de TypeScript. Todavía no existe: la Fase 1 es Python primero
(ver el orden de construcción en el documento de contexto y en el README raíz).

Cuando llegue, tiene que cumplir lo mismo que el de Python:

- Emitir spans de OpenTelemetry con las convenciones semánticas GenAI, no un formato propio.
- Respetar el contrato de [`docs/trace-contract.md`](../../docs/trace-contract.md) al pie
  de la letra: los mismos nombres de atributo, los mismos cinco tipos de span, los tokens
  de entrada y salida por separado y el modelo exacto.
- No calcular coste: eso es trabajo del backend (DECISIONS D-005).
- Instrumentar en una línea, con auto-instrumentación de los SDK de `openai` y
  `@anthropic-ai/sdk`.
- No romper nunca el programa que observa.

Mientras tanto, cualquier proceso de Node instrumentado con OpenTelemetry estándar ya
puede exportar a Laplace apuntando `OTEL_EXPORTER_OTLP_ENDPOINT` al backend: la ingesta
es OTLP puro.
