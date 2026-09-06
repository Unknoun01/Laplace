# Contrato de datos de una traza

Este documento es normativo. Las tres capas (SDK, backend, web) dependen de esta forma;
un cambio aquí es un cambio coordinado en las tres, en un único commit.

Implementación de referencia: [`packages/sdk-python/laplace/schema.py`](../packages/sdk-python/laplace/schema.py)
(modelos Pydantic) y [`packages/sdk-python/laplace/semconv.py`](../packages/sdk-python/laplace/semconv.py)
(nombres de atributos).

---

## 1. Modelo

Se adopta el modelo de OpenTelemetry sin modificarlo:

```
Trace  (una ejecución completa del agente ante una petición)
└── Span (un paso)
    └── Span (un paso hijo, vía parent_span_id)
```

Un **trace** no es una entidad que se escriba: es la agregación de sus spans. Lo que el
SDK emite son spans; el backend deriva el resumen de la traza (ver §6).

### Tipos de span (`laplace.span.type`)

| Tipo        | Qué representa                                                    |
|-------------|-------------------------------------------------------------------|
| `agent`     | Una unidad de decisión autónoma. Suele ser el span raíz.           |
| `llm`       | Una llamada a un modelo de lenguaje.                               |
| `tool`      | Una llamada a una herramienta / función externa.                   |
| `retrieval` | Una búsqueda en un índice o base vectorial.                        |
| `chain`     | Un paso de orquestación que agrupa a otros sin ser una decisión.   |

El tipo se guarda explícito (no se infiere en consulta) porque casi todos los filtros
del producto empiezan por él.

---

## 2. Atributos por span

Se siguen las **convenciones semánticas GenAI de OpenTelemetry**. Todo lo que no existe
en el estándar vive bajo el prefijo `laplace.*`, nunca inventando nombres dentro de `gen_ai.*`.

### Comunes

| Atributo             | Tipo   | Notas                                             |
|----------------------|--------|---------------------------------------------------|
| `laplace.span.type`  | string | Uno de los cinco tipos de §1.                     |
| `laplace.session.id` | string | Agrupa varias trazas de una misma conversación.   |
| `laplace.user.id`    | string | Usuario final del agente (no el cliente Laplace). |
| `laplace.tags`       | string | JSON array de strings.                            |
| `laplace.metadata`   | string | JSON object libre.                                |

A nivel de **recurso** (todos los spans del proceso): `laplace.project.id`, `service.name`,
`service.version`, `telemetry.sdk.*`.

### Spans de tipo `llm`

| Atributo                            | Tipo     | Notas                                          |
|-------------------------------------|----------|------------------------------------------------|
| `gen_ai.operation.name`             | string   | `chat`, `text_completion`, `embeddings`.       |
| `gen_ai.system`                     | string   | `openai`, `anthropic`, ...                     |
| `gen_ai.request.model`              | string   | **Modelo exacto**, nunca una categoría.        |
| `gen_ai.response.model`             | string   | El que devuelve el proveedor (puede diferir).  |
| `gen_ai.usage.input_tokens`         | int      | Separado de la salida, siempre.                |
| `gen_ai.usage.output_tokens`        | int      | Separado de la entrada, siempre.               |
| `laplace.usage.cached_input_tokens` | int      | Tokens de entrada servidos desde caché.        |
| `laplace.usage.reasoning_tokens`    | int      | Tokens de razonamiento, si el proveedor los da.|
| `gen_ai.request.temperature`        | double   | Idem `.max_tokens`, `.top_p`.                  |
| `gen_ai.response.finish_reasons`    | string[] |                                                |
| `gen_ai.response.id`                | string   |                                                |
| `gen_ai.input.messages`             | string   | **JSON**: mensajes de entrada completos y en crudo. |
| `gen_ai.output.messages`            | string   | **JSON**: respuesta completa y en crudo.       |

> **Por qué input y output por separado y el modelo exacto:** son la base del cálculo
> "este paso costaría 10x menos con otro modelo" del panel de ahorro. Guardar sólo
> `total_tokens`, o una familia de modelo en vez del modelo exacto, hace ese cálculo imposible.

### Spans de tipo `tool`

| Atributo                    | Tipo   | Notas                                          |
|-----------------------------|--------|------------------------------------------------|
| `gen_ai.operation.name`     | string | `execute_tool`.                                |
| `gen_ai.tool.name`          | string |                                                |
| `gen_ai.tool.call.id`       | string | Correlaciona con el `tool_call` del LLM padre. |
| `gen_ai.tool.description`   | string |                                                |
| `laplace.tool.arguments`    | string | JSON de los argumentos.                        |
| `laplace.tool.output`       | string | JSON o texto de la salida.                     |

### Estado y errores

`status` es uno de `ok`, `error`, `unset`. Las excepciones se registran como *span events*
de OTel (`exception.type`, `exception.message`, `exception.stacktrace`) y se guardan tal cual.

---

## 3. Payloads: completos y en crudo

Prompts, respuestas, argumentos y salidas se guardan **sin truncar**. El diagnóstico
automático (Norte A) razona sobre el texto real: un prompt cortado a 500 caracteres hace
inútil la capa que justifica el producto.

Controles:

- SDK: `laplace.init(capture_content=False)` desactiva la captura de contenido.
- Cloud: retención configurable por proyecto vía TTL de ClickHouse (no implementado en Fase 1).

---

## 4. Coste

El coste **no lo calcula el SDK** (quedaría congelado en la versión instalada por el
usuario). Se calcula en la ingesta a partir de `gen_ai.request.model` + tokens, contra la
tabla de precios del backend (`apps/backend/laplace_backend/pricing/model_prices.json`).

Se guarda **desglosado por span**:

```
cost_input_usd, cost_output_usd, cost_total_usd, cost_estimated (bool)
```

`cost_estimated = true` cuando el modelo no está en la tabla de precios y se ha aplicado
una heurística (o cero). Nunca se muestra un coste inventado como si fuera exacto.

El coste de un nodo del árbol se presenta en dos formas: **propio** (el del span) y
**subárbol** (la suma de sus descendientes). El subárbol se calcula al servir el árbol;
no se almacena.

---

## 5. Detección de repeticiones

Cada span lleva un `dedup_hash`: hash estable de `(tipo, nombre, modelo, entrada normalizada)`.

- Dos spans `tool` con el mismo `dedup_hash` en una traza = la misma llamada repetida.
- Dos spans `llm` con el mismo `dedup_hash` = el mismo prompt reenviado.

Se calcula en la ingesta (D-007) para poder redefinirlo sin tocar los SDK ya instalados.
Es la materia prima de la detección de bucles (Fase 2) y del diagnóstico (Fase 3).

---

## 6. Resumen de traza (derivado)

Una fila por traza, mantenida por una vista materializada de ClickHouse:

```
trace_id, project_id, root_name, start_time, end_time, duration_ms,
span_count, error_count, llm_call_count, tool_call_count,
input_tokens, output_tokens, total_cost_usd, session_id, user_id
```

El `status` de la traza es `error` si `error_count > 0`, y `ok` en caso contrario.

---

## 7. Huecos reservados (Fases 3 y 4)

Existen **ya**, vacíos, para no migrar el esquema entero más adelante.

### `trace_diagnoses` (Postgres) — Norte A

```
trace_id, project_id, created_at, model, cause, explanation, suggestion,
categories[], confidence, estimated_savings_usd, raw
```

Un diagnóstico por traza: causa detectada + sugerencia de arreglo.

### `annotations` (Postgres) — Fase 4

```
id, trace_id, span_id?, source ('human' | 'llm_judge'), verdict ('pass'|'fail'|'unknown'),
score?, label?, comment?, author?, created_at
```

Colgadas de una traza **o** de un span concreto: un veredicto sobre "el paso 3 alucinó"
es tan necesario como uno sobre la ejecución entera.

Ambas se exponen ya en el modelo `Trace` (`diagnosis`, `annotations`), devueltas como
`null` y `[]` mientras no exista la capa que las rellena.

---

## 8. Compatibilidad

Cualquier proceso instrumentado con OpenTelemetry estándar puede exportar a Laplace
(`OTEL_EXPORTER_OTLP_ENDPOINT=http://...:4318`). Los spans sin atributos `gen_ai.*` se
ingieren igual, con tipo `chain` y coste cero: se ven en el árbol, no aportan coste.
