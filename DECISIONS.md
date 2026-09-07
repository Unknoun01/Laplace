# DECISIONS

Registro de decisiones tomadas sobre la marcha. Regla: lo no especificado en el
documento de contexto se resuelve con la opción más simple y estándar, y se anota aquí.

Formato: `YYYY-MM-DD — Decisión — Motivo — Alternativas descartadas`.

---

## 2026-09-06 — Fase 0 + arranque de Fase 1

### D-001 — Los tipos compartidos viven en el SDK (`laplace.schema`), y el backend depende del SDK
El contrato de traza es Pydantic v2 dentro de `packages/sdk-python/laplace/schema.py`.
El backend lo instala como dependencia de path (`-e ../../packages/sdk-python`).
**Motivo:** una sola fuente de verdad; es exactamente la razón por la que el repo es
un monorepo. **Descartado:** duplicar los modelos en backend (deriva garantizada) y
crear un cuarto paquete `packages/schema` (ceremonia innecesaria para un dev solo).

### D-002 — Pydantic v2 es dependencia del SDK
El SDK debe ser "ligero", pero `schema.py` sólo se importa en la ruta de lectura/tests,
no en el camino caliente de emisión de spans (que usa constantes de string de
`laplace/semconv.py`, sin dependencias). **Descartado:** dataclasses puras (perdemos
validación y serialización gratis en el backend).

### D-003 — Los mensajes se guardan como JSON en `gen_ai.input.messages` / `gen_ai.output.messages`
Es la dirección actual de las convenciones semánticas GenAI de OpenTelemetry (mensajes
como atributo de span serializado, opt-in por privacidad). **Descartado:** el estilo
indexado de OpenInference (`llm.input_messages.0.message.role`), que multiplica el
número de atributos por mensaje y complica la ingesta.

### D-004 — Transporte OTLP/HTTP con codificación protobuf
El exportador oficial de Python (`opentelemetry-exporter-otlp-proto-http`) sólo habla
protobuf de forma estable. El backend decodifica con `opentelemetry-proto`.
**Consecuencia buena:** cualquier app ya instrumentada con OTel puede apuntar a Laplace
sin usar nuestro SDK. **Descartado:** OTLP/JSON (no soportado por el exportador estable),
formato propio (perderíamos la compatibilidad gratis).

### D-005 — El coste se calcula en el backend, en la ingesta, y se guarda desglosado por span
El SDK nunca calcula precios: quedarían congelados en la versión instalada por el usuario.
Se guardan `cost_input_usd`, `cost_output_usd`, `cost_total_usd` y un flag `cost_estimated`
por span. **Motivo:** el panel de ahorro (Norte B) necesita el desglose por paso, no el
total de la traza.

### D-006 — La tabla de precios es un JSON versionado en el repo
`apps/backend/laplace_backend/pricing/model_prices.json`, cargado al arranque y
recargable en caliente. Actualizarlo es un PR de una línea. **Descartado:** tabla en
Postgres desde el día uno (más operación, sin beneficio hasta que haya varios clientes;
el loader ya está preparado para leer overrides de Postgres cuando haga falta).

### D-007 — `dedup_hash` se calcula en la ingesta, no en el SDK
Hash estable de (tipo, nombre, modelo, entrada normalizada). Dos spans con el mismo hash
dentro de una traza son una repetición. **Motivo:** alimenta la detección de bucles
(Fase 2) y el diagnóstico (Fase 3) sin coste en el cliente, y se puede recalcular si
cambiamos la definición.

### D-008 — Sin vista materializada de rollup en Fase 1: `GROUP BY` sobre `spans FINAL`
Primero se diseñó una MV `spans → traces` (AggregatingMergeTree) para que la lista de
trazas no dependiera del volumen de spans. Se descartó: una vista materializada dispara
en cada inserción, así que un reintento del exportador OTLP (que ocurre en timeouts y
5xx, cuando la escritura puede haber cuajado a medias) **duplicaría el coste sumado** en
el rollup, y el coste es justo el número que vendemos. `spans` es `ReplacingMergeTree`
por `(project_id, trace_id, span_id)`: los duplicados colapsan y las consultas usan
`FINAL`, lo que es correcto por construcción. La MV vuelve cuando el volumen lo exija y
haya un mecanismo de idempotencia en la ingesta.

### D-008b — La lista de trazas no filtra por ventana temporal por defecto
Un proyecto con poco tráfico mostraría una lista vacía si el filtro fuese "últimos 7 días".
Se escanea todo salvo que se pasen `since`/`until`. Primera optimización pendiente cuando
haya volumen.

### D-009 — Los payloads se guardan completos y sin truncar
Prompts, respuestas, argumentos y salidas de tools se guardan en crudo. El SDK expone
`capture_content=False` para desactivarlo en entornos sensibles, y la retención se
configurará por proyecto (TTL de ClickHouse) más adelante. **Motivo:** el diagnóstico
automático (Norte A) necesita el texto real.

### D-010 — Sin auth en la Fase 1; Clerk cuando llegue el cloud
El modo local no tiene cuentas por diseño. Para el cloud se usará **Clerk** (mejor DX,
organizaciones y RBAC de serie). La ingesta ya acepta `Authorization: Bearer <api_key>`
y la tabla `api_keys` existe en Postgres, pero la validación está desactivada mientras
no haya multi-tenant real. **Descartado:** Supabase Auth (arrastra el resto de Supabase).

### D-011 — Postgres guarda metadatos y las tablas reservadas de Fases 3–4
`projects`, `api_keys`, `annotations`, `trace_diagnoses`, `datasets`, `dataset_items`.
Anotaciones y diagnósticos son mutables y relacionales: no encajan en ClickHouse.
Se crean ya (vacías) para no migrar el esquema entero después.

### D-012 — Frontend: Next.js (App Router) + TypeScript + Tailwind, sin librería de componentes
Server Components para leer del backend, un único cliente para el árbol interactivo.
**Descartado:** shadcn/ui y similares en el MVP (dependencias y ruido para dos pantallas).

### D-013 — Empaquetado Python con Hatchling; gestor JS: npm
Lo más estándar y con menos configuración. `packages/sdk-python` se publica a PyPI de
forma independiente (`hatch build` dentro de esa carpeta, sin arrastrar el monorepo).

### D-014 — Nombres de span: convención GenAI para LLM, nombre propio para el resto
Los spans `llm` se llaman `<operación> <modelo>` (ej. `chat gpt-4o-mini`), que es la
convención de OTel y además lee bien en el árbol. Los `tool`, `agent` y `chain` llevan
el nombre de la función o el que dé el usuario, y la operación va en
`gen_ai.operation.name` (`execute_tool`, `invoke_agent`). **Motivo:** poner
`execute_tool buscar_vuelos` como nombre visible ensucia la pantalla principal sin
aportar nada; el dato sigue estando en su atributo estándar.

### D-015 — El almacenamiento vive detrás de un `Protocol` (`SpanStore`)
La implementación de Fase 1 es ClickHouse. El modo local con SQLite (Fase 1, punto 7)
entra por la misma interfaz sin tocar la API. **Motivo:** está comprometido en el plan;
no diseñar contra ello ahora costaría una reescritura después.

### D-016 — El streaming se registra pero todavía no se acumula
Las llamadas con `stream=True` generan span (modelo, parámetros, prompt, latencia hasta
el primer byte) marcado con `laplace.streaming.captured = false`, sin tokens ni contenido
de salida. **Motivo:** acumular chunks requiere envolver el iterador de cada proveedor,
con formas distintas por proveedor y por versión, y no puedo probarlo contra las APIs
reales en esta iteración. Prefiero un hueco documentado a código sin probar en el camino
caliente del agente de un usuario. Es la primera deuda a saldar del SDK.

### D-017 — Límite de 1 MiB por payload, con marca explícita
El contrato pide payloads sin truncar, pero un atributo sin límite revienta el exportador
OTLP. Se recorta a 1 MiB dejando `...[truncado por laplace: N bytes omitidos]`, y se puede
desactivar con `LAPLACE_MAX_PAYLOAD_BYTES=none`. **Motivo:** nunca perder contenido en
silencio; que se vea que se cortó y cuánto.

### D-018 — Python 3.10 como mínimo
Se usa sintaxis `X | None` que Pydantic tiene que resolver en tiempo de ejecución.
3.9 llegó a su fin de vida en octubre de 2025. **Descartado:** `Optional[...]` en todo
el contrato para ganar una versión muerta.

### D-019 — Sin CLI `laplace` todavía
El comando `laplace ui` pertenece al modo local con SQLite (Fase 1, punto 7), que no
entra en esta iteración. No se declara el entry point para no publicar un comando que
no hace nada.

### D-020 — Un cliente de ClickHouse por hilo
`clickhouse-connect` rechaza consultas concurrentes sobre el mismo cliente
("concurrent queries within the same session") y FastAPI sirve las lecturas desde un
pool de hilos: la pantalla principal pide la lista de trazas y la de proyectos a la vez,
lo que reventaba con un 500. El cliente pasa a ser `threading.local`. Como el pool
reutiliza hilos, son unos pocos clientes, no uno por petición. **Descartado:** un lock
(serializa todas las lecturas) y un cliente por petición (reconexión en cada llamada).

### D-021 — Postgres se publica en el puerto 5433 del host
5432 suele estar ocupado por un Postgres instalado en la máquina del desarrollador, y
apuntar sin querer a su base de datos es la peor primera impresión posible (pasó en la
primera ejecución de este repo). Dentro de la red de compose sigue siendo 5432.

### D-022 — El coste se guarda y se muestra en USD
Las tarifas de todos los proveedores están en USD y convertir exige una fuente de tipo
de cambio y decidir la fecha de conversión. El discurso de venta habla de euros, así que
la conversión llegará con el panel de ahorro (Fase 2), con el tipo de cambio guardado
junto al importe. Inventar un cambio ahora daría cifras que no cuadran con la factura
del proveedor. El contrato ya lleva el campo `currency` para cuando toque.

### D-023 — El cursor de paginación es `(inicio, trace_id)`, no sólo la fecha
Paginar con `started < before` pierde trazas cuando varias empiezan en el mismo instante,
que es exactamente lo que pasa con agentes lanzados en paralelo. Se compara la tupla
`(started, trace_id)` y se ordena por `started DESC, trace_id DESC`. El cursor viaja como
un string opaco (`<iso>|<trace_id>`) para poder cambiar su forma sin romper clientes.
Lo destapó el test de integración contra ClickHouse, no la revisión a ojo.

### D-024 — Los alias de las agregaciones no pueden llamarse como su columna
`any(project_id) AS project_id` hacía que ClickHouse resolviera el `WHERE` contra el
alias y devolviera `ILLEGAL_AGGREGATION` en cuanto se filtraba por proyecto. Los alias
de traza llevan prefijo (`trace_project_id`, `trace_session_id`, `trace_user_id`) y hay
un test de regresión por cada filtro.

### D-025 — Hay dos niveles de prueba, y el segundo se salta solo
`test_ingest.py` recorre SDK → OTel → protobuf OTLP → contrato sin tocar la base de datos.
`test_clickhouse_store.py` ejecuta el SQL de verdad contra ClickHouse y se salta solo si
no hay ninguno escuchando. **Motivo:** los fallos de SQL (alias, filtros que no se
aplican, paginación) son invisibles para las pruebas en memoria; los dos bugs anteriores
salieron de ahí. Que se salten solas mantiene `pytest` utilizable sin levantar nada.

## 2026-09-06 — Hallazgos de la verificación de la Fase 0-1

### D-026 — `FINAL` sólo donde no cuesta: fuera de la apertura de traza
`SELECT ... FROM spans FINAL WHERE trace_id = ...` obliga a ClickHouse a leer la
partición entera en lugar de los gránulos que el índice selecciona. Medido sobre
800.000 spans: **300.349 filas leídas con `FINAL` frente a 16.384 sin él**, para una
traza de 10 spans. Como es la consulta que se ejecuta en cada clic, se sustituye por
`LIMIT 1 BY span_id` sobre `ingested_at DESC`, que da la misma garantía frente a
reintentos del exportador y conserva la poda. En `list_traces` se mantiene `FINAL`:
ahí la consulta es un escaneo por naturaleza y medido no cuesta nada extra
(34 ms con `FINAL` frente a 53 ms sin él sobre 500.000 filas).

### D-027 — Índice de salto sobre `trace_id`
La clave de ordenación es `(project_id, trace_id, span_id)` y la UI abre trazas sin
saber el proyecto. ClickHouse ya poda razonablemente por la clave primaria cuando hay
pocos proyectos, pero con miles la poda se degrada. El bloom filter sobre `trace_id`
cubre ese caso; el `EXPLAIN indexes=1` confirma que reduce de 41 a 2 gránulos.

### D-028 — El flush de salida tiene un tope de tiempo que se cumple de verdad
`force_flush` de OpenTelemetry no aborta un envío en curso: contra un backend caído se
comía **9,3 segundos** pese a pedirle 3, y encima el `atexit` propio de OTel añadía otro
`shutdown()` bloqueante. Medido: un script trivial tardaba ~9 s en terminar sólo por la
telemetría. Ahora el envío va en un hilo demonio, se espera como mucho
`LAPLACE_EXIT_FLUSH_MS` (2 s por defecto) y el `TracerProvider` se crea con
`shutdown_on_exit=False`. **Motivo:** el SDK promete no ralentizar lo que observa; que
el que falle sea nuestro backend no puede castigar al usuario. Sobrecoste medido de
`@observe`: 0,24 ms por llamada.

### D-029 — Las pruebas borran sus datos al terminar
La base de datos de desarrollo es la misma que el usuario mira en la UI, y cada
ejecución de la suite dejaba un proyecto `test-*` sembrado. La fixture llama ahora a
`delete_project`, que además es el borrado por cliente que el RGPD exigirá cuando haya
cuentas. Aviso: es una mutación, así que sirve para un proyecto, no para un bucle sobre
miles.

### D-030 — Fichero de licencia
Faltaba el `LICENSE` pese a declarar Apache-2.0 en el README y en `pyproject.toml`.
Añadido el texto íntegro. Sin él, "open source" no es una afirmación defendible.

## 2026-09-07 — Interfaz definitiva y motor de detección (Fase 2)

### D-031 — La UI reproduce el mock; se cae Tailwind y se usa CSS propio
El documento de dirección fija tokens, tipografía y densidad, y prohíbe introducir otro
lenguaje visual. Reproducirlo con utilidades de Tailwind obligaba a traducir cada regla
del mock y a vigilar que no se colara una clase ajena al sistema. Se pasa a CSS propio
con las mismas clases del mock (`.card`, `.node`, `.kv`, `.chip`…). **Sustituye a D-012.**
De paso desaparece la clase de fallo que ya nos mordió una vez: clases construidas en
tiempo de ejecución que Tailwind no ve y no genera.

### D-032 — Las pantallas de diagnóstico necesitaban adelantar la Fase 2
El inicio y la ficha no son maquetación: piden reglas de detección reales. Se han
construido tres, deterministas y sin modelo (`apps/backend/laplace_backend/insights.py`):
repetición exacta dentro de una traza, modelo caro para un paso de salida corta, y
contexto fijo reenviado sin caché. El diagnóstico con modelo (Fase 3) rellenará el hueco
`trace_diagnoses` que ya existe; la ficha ya sabe pintarlo cuando llegue.

### D-033 — Las reglas no pueden solaparse: se descuenta el doble conteo
La regla de repetición se lleva el coste íntegro de las copias sobrantes. Si esas copias
son llamadas a un modelo caro, la regla del modelo **no puede volver a contarlas**: al
principio el ahorro total salía por encima del gasto real. Ahora los tokens duplicados se
restan del uso antes de evaluar el resto de reglas, y hay un test de regresión que
comprueba que la suma de hallazgos cuadra con el evitable del héroe y nunca lo supera.
**Motivo:** el ahorro es el número que vende el producto; inflarlo lo invalida entero.

### D-034 — Un hallazgo que no cuesta dinero lo dice, no lo disimula
Repetir una herramienta no consume tokens. La tarjeta de ese hallazgo enseña **tiempo**
en lugar de dinero, se marca como secundaria y baja al final del orden. Alternativa
descartada: atribuirle una parte del coste de las llamadas al modelo de alrededor, que
habría sido una cifra inventada.

### D-035 — No se muestra ninguna métrica de calidad del modelo alternativo
El mock enseñaba un "acierto medido 98,2 %" en la tarjeta del modelo caro. Ese número no
se puede medir sin evaluaciones (Fase 4), así que no se enseña. Lo que sí se afirma es el
ahorro, que es aritmética sobre tokens reales, y se acompaña de un paso explícito:
"comprueba que la calidad aguanta".

### D-036 — El coste se muestra en USD, leyendo la moneda de la API
El mock hablaba en euros. Se mantiene el criterio de D-022: la UI lee `currency` de la
respuesta y formatea con ese símbolo, sin incrustarlo en el componente. Mientras no haya
una fuente de tipo de cambio diaria, la API devuelve USD y eso se ve. El día que exista
conversión por proyecto no habrá que tocar ninguna pantalla.

### D-037 — La consulta que se enseña es la que se ejecuta
"Cómo lo hemos detectado" muestra el SQL real: las consultas del motor viven en
constantes de módulo (`REPEATED_GROUPS_SQL`, `MODEL_USAGE_SQL`) que usan tanto el almacén
como la API. Copiarlas a mano en la interfaz habría acabado en una consulta que dice una
cosa y un backend que hace otra.

### D-038 — El identificador de un hallazgo es determinista, no se guarda
`tipo:clave` (por ejemplo `repeticion:<dedup_hash>`). La ficha se recalcula sobre la misma
ventana. **Consecuencia buscada:** si el usuario arregla el problema y vuelve, la ficha
devuelve 404 y la pantalla dice "ese problema ya no aparece", que es justo lo que
queremos comunicar.

### D-039 — Ordenar por coste no es paginable con cursor
El explorador ordena por coste de serie, que es lo que pide la dirección de interfaz. El
cursor `(inicio, trace_id)` sólo da una secuencia estable en el orden temporal, así que
con orden por coste o duración no se devuelve cursor y la lista enseña las N más caras
del rango, diciéndolo. **Descartado:** paginar por offset, que con datos entrando duplica
y salta filas.

### D-040 — El proyecto y el rango viven en la URL
No en un estado interno ni en una cookie: así una pantalla concreta se puede enlazar y
compartir tal y como se está viendo. El modo Diagnóstico/Avanzado sí va en `localStorage`,
porque es una preferencia de la persona, no del enlace.
