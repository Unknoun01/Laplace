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

Mientras tanto, un agente en Node se instrumenta con OpenInference u OpenLLMetry y se
apunta a Laplace: la guía, probada, está en [`docs/typescript.md`](../../docs/typescript.md).
