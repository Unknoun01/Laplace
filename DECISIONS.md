# DECISIONS

Registro de decisiones tomadas sobre la marcha. Regla: lo no especificado en el
documento de contexto se resuelve con la opción más simple y estándar, y se anota aquí.

Formato: `YYYY-MM-DD — Decisión — Motivo — Alternativas descartadas`.

**Por tema:** [`docs/decisiones-indice.md`](docs/decisiones-indice.md), generado con
`python scripts/indice_decisiones.py` (una prueba exige que esté al día).

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

## 2026-09-07 — Deuda que invalidaba las cifras (§1) y modo dual real (§2)

### D-041 — Los precios se verifican contra la página oficial, con fuente y fecha
La tabla anterior salía de mi conocimiento, sin respaldo. Verificada contra
`developers.openai.com/api/docs/pricing` y `platform.claude.com/docs/en/about-claude/pricing`
el 2026-09-07. **Estaba gravemente desactualizada**: le faltaban las familias GPT-5 y
GPT-6 enteras, y tenía a Opus 4 como modelo vigente a 15/75 $ cuando el Opus actual
cuesta 5/25 $. El fichero declara ahora `version`, un bloque `sources` con URL y fecha
de verificación, y cada modelo dice de qué fuente sale. Cada cálculo guarda la tarifa
aplicada (`<modelo> @ <versión>`), visible en modo avanzado.

### D-042 — Resolver por prefijo no puede saltar de versión
`claude-opus-4-5` empieza por `claude-opus-4`, así que la resolución ingenua le habría
aplicado **el triple** de tarifa en silencio. Ahora un prefijo sólo se acepta si lo que
sobra es un snapshot con fecha (`-20250929`, `-2024-07-18`) o una palabra (`-latest`);
un `-5` o un `.7` son otra versión y no heredan nada. Consecuencia buscada: un modelo
futuro que no esté en la tabla sale como desconocido en lugar de como barato.

### D-043 — Un modelo sin tarifa cuesta "no lo sabemos", no cero
`Cost.estimated` se convierte en `Cost.unknown`. Un 0 silencioso se suma a los totales y
los corrompe sin que nadie se entere. El span se marca, la traza cuenta cuántos pasos
tiene sin tarifa, el resumen del proyecto lista los modelos implicados, y el inicio dice
"este total está incompleto" en lugar de enseñar una cifra que parece completa. Hay un
test que falla si aparece en las trazas un modelo que no está en la tabla.

### D-044 — Streaming: se acumulan tokens, contenido y coste
El span de streaming ya no es un registro vacío. Se envuelve el stream en un proxy que
delega por `__getattr__` (los clientes exponen atributos propios que el código del
usuario puede estar usando), soporta iterar, `with` y `close`, y cierra el span aunque
quien lo consume lo abandone a medias. Anthropic manda los recuentos en `message_start`
y `message_delta`; OpenAI sólo si la petición lleva `stream_options={"include_usage":
True}`.

### D-045 — No se inyecta `stream_options` en la petición del usuario
Añadirlo daría recuentos exactos, pero cambia la forma del stream que recibe el usuario:
aparece un chunk final con `choices` vacío que su código puede no esperar. Un SDK de
observabilidad no puede permitirse eso. Sin recuento se estima (≈4 caracteres por token),
y el span queda marcado con `laplace.usage.estimated`, que la interfaz enseña: no es lo
mismo un coste que sale de la factura que uno que sale de dividir caracteres.

### D-046 — Se proyecta sobre los días observados, no sobre los que pide el selector
Un proyecto que lleva dos horas enviando trazas no tiene siete días de datos aunque el
rango diga «7 días». Se proyecta sobre el intervalo real entre el primer y el último
span, y si es menos de 24 h el inicio lo dice en la propia cifra. Además, cuando el
ahorro estimado pasa del 60 % del gasto, se presenta con cautela explícita en lugar de
como promesa: un 91 % de desperdicio no se lo cree nadie aunque los números salgan.

### D-047 — El modo avanzado se OCULTA en diagnóstico, no se muestra en avanzado
El mecanismo original (`.pro { display:none }` más una regla por cada tipo de caja para
volver a enseñarlo) perdía por especificidad contra cualquier componente que fijara su
propio `display`: el botón "Exportar traza en JSON" se colaba en modo Diagnóstico, que
es justo el bug que había que arreglar. Ahora la regla es
`body:not([data-mode="pro"]) .pro { display: none !important }`: el elemento conserva su
display natural y ocultarlo no depende del orden de las reglas. Desaparecen las variantes
`.pro.inline`, `.pro.flex`, `.pro.grid` y `th.pro/td.pro`.

### D-048 — Qué añade Avanzado en cada pantalla
Antes de dar una pantalla por terminada: ¿qué cambia al pulsar Avanzado?
- **Inicio:** fila de métricas técnicas, línea técnica de cada tarjeta, cálculo del ahorro.
- **Ficha:** cómo se ha detectado con la consulta real, la traza completa, atributos del
  paso señalado, pasos de arreglo con detalles de implementación, exportación.
- **Explorador:** columnas de modelos, tokens de entrada y salida por separado e id
  completo; filtros por modelo, por coste mínimo y por `session.id`.
- **Traza:** `span_id` en la cabecera, id completo, modelos, atributos crudos `gen_ai.*`
  y `laplace.*`, timings exactos, si los tokens fueron medidos o estimados, y exportación.

### D-049 — Las piezas compartidas de los tests viven en `helpers.py`, no en `conftest.py`
Los módulos de prueba necesitan *importar* el exportador en memoria y el ayudante de
ingesta, no sólo recibirlos como fixture, y `conftest.py` no es importable entre tests
sin convertir el directorio en paquete. `conftest.py` se queda con las fixtures.

### D-050 — `input_tokens` es el total facturable, con la caché dentro
Los proveedores no cuentan igual: OpenAI incluye los tokens servidos desde caché en
`prompt_tokens`, y Anthropic los devuelve aparte en `cache_read_input_tokens` y
`cache_creation_input_tokens`. Guardar cada uno como venga haría que el mismo agente
costara distinto según con quién hablara. El contrato fija un criterio único —
`gen_ai.usage.input_tokens` es el total facturable de entrada, y los campos
`laplace.usage.cached_input_tokens`, `cache_write_tokens` y `cache_write_1h_tokens` dicen
qué parte del total fue cada cosa— y la normalización la hace cada integración del SDK.

### D-051 — Ante un metro de facturación que no se puede determinar, se cobra el estándar y se dice
Contexto largo, residencia de datos y modo rápido son metros con tarifa propia. Algunos
se leen de la petición (`speed="fast"`, `inference_geo`, `service_tier`) y se aplican;
otros no se ven desde el SDK. El tramo de contexto largo es el caso feo: OpenAI publica
las tarifas pero **no** el número de tokens a partir del cual entran. Se cobra el
estándar, se marca el span como tarifa asumida con el motivo, y la interfaz presenta la
cifra como un suelo. Es la interpretación conservadora: la que produce menos ahorro. La
alternativa —elegir el tramo caro— habría engordado nuestro número de ahorro con un
umbral inventado.

### D-052 — El sufijo de prefijo válido es una lista cerrada de palabras, no cualquier palabra
El arreglo anterior aceptaba como snapshot cualquier `-palabra`, lo que dejaba pasar
`-pro`, `-mini`, `-nano` y `-cyber`: `gpt-5.5-pro` cuesta seis veces `gpt-5.5`, y un
futuro `gpt-5.6-terra-mini` habría heredado en silencio la tarifa de `gpt-5.6-terra`. Se
aceptan sólo fechas, `-vN` y `latest|preview|beta|stable|exp`. Un sufijo desconocido deja
el modelo como coste desconocido, que es el lado por el que hay que equivocarse. Hay un
test que recorre la tabla buscando pares en esa relación.

### D-053 — Auditoría de identificadores: no había ninguno inventado, faltaban cinco reales
El encargo pedía eliminar los IDs que no aparecieran en la documentación oficial. Al
recorrer la tabla entrada por entrada contra las dos páginas de precios no se encontró
ninguno inventado: **no se ha eliminado nada**. El ejemplo que motivaba la revisión,
`claude-opus-4-5`, sí existe y sigue publicado a 5/25 $. Lo que sí faltaban eran cinco
modelos reales de OpenAI que la tabla no recogía —`gpt-5.6-cyber`, `gpt-5.5-cyber`,
`gpt-3.5-turbo-0125`, `gpt-3.5-turbo-instruct`, `davinci-002` y `babbage-002`—, cuyo
coste salía como desconocido. Se han añadido. A los dos modelos `cyber` no se les asigna
alternativa barata: están especializados en ciberseguridad y no son intercambiables con
un generalista, así que recomendar el cambio sería recomendar otro resultado.

### D-054 — El descuento de lote y el recargo regional viven en `sources`, no en cada modelo
Las dos páginas los publican como regla uniforme (50 % de descuento en lote, 1,1x en
residencia de datos), no como columna por modelo. Repetir el número en cincuenta entradas
sería cincuenta sitios donde equivocarse. Las escrituras de caché sí van por modelo,
porque la tabla de Anthropic las publica una a una; se han derivado de los
multiplicadores documentados (1,25x y 2x) y el generador comprueba que coinciden con los
valores publicados.

### D-055 — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
Anunciar la diferencia entera entre tarifa de entrada y tarifa de lectura es prometer un
ahorro que el propio motor de precios sabe que no llega entero: la primera llamada de
cada ejecución paga 1,25× por escribir la caché. Se descuenta una escritura por traza, lo
que además supone que la caché **no** sobrevive de una ejecución a la siguiente. Si
aguanta más, el usuario ahorrará más de lo que le dijimos, que es la dirección correcta
del error.

### D-056 — El agente de ejemplo usa modelos vigentes y genera contexto fijo
`gpt-4o`/`gpt-4o-mini` eran varias generaciones anteriores y hacían que las cifras se
leyeran como un ejercicio de museo. Ahora compara `gpt-5.6-terra` con `gpt-5.6-luna`, que
son diez veces uno del otro. Se ha añadido una sexta patología —un manual de 20.000
tokens reenviado en cada llamada sin marcarlo como cacheable— porque la tercera regla no
llegaba a dispararse nunca con los datos que generaba la demo, y una regla que no se
puede ver funcionando cuenta como no hecha.

### D-057 — Cambiar de modelo y activar la caché se cuentan encadenados, no sumados
Las reglas 2 y 3 pueden dispararse sobre el mismo paso. Tarifando las dos sobre el modelo
caro, en la demo prometían 470 $ + 368 $ sobre un gasto de 525 $: el tope
`min(suma, gasto)` lo tapaba, pero el dinero estaba contado dos veces. Ahora, cuando a un
paso ya se le recomienda un modelo más barato, el ahorro de la caché se calcula **con las
tarifas del modelo nuevo**. Son dos arreglos que se aplican uno detrás del otro, así que
la suma es exacta; y si el usuario sólo cachea sin cambiar de modelo, ahorrará más de lo
que le dijimos. La ficha explica el encadenado en vez de dejar dos cifras incompatibles.
Hay un test que comprueba que la suma de hallazgos de un paso no pasa de lo que ese paso
cuesta.

### D-058 — Los días observados los usa todo el motor, no sólo el héroe
El gasto total se proyectaba sobre los días con datos reales y el ahorro sobre los días
del selector. Con una hora de datos en una ventana de siete días, eso multiplica el total
por 720 y el ahorro por 4,3: las dos cifras del inicio dejan de ser comparables y la barra
de reparto miente. `detect()` y `detail()` proyectan ahora sobre los mismos días
observados que `overview()`.

### D-059 — ~~Las reglas agrupan por (nombre del paso, modelo)~~ — resuelto en D-060
Quedó anotado como límite conocido: `llm_span` y las integraciones automáticas nombran
el span `chat <modelo>`, así que todas las llamadas a un mismo modelo caían en el mismo
grupo aunque fueran pasos distintos. El agente de ejemplo lo esquivaba pasando `name=`,
que es exactamente lo que nadie hace en código real. Sustituido por D-060.

### D-060 — Un paso es «llamada hecha desde el mismo sitio y con las mismas instrucciones»
La identidad de un paso ya no es el nombre del span. Se calcula en la ingesta, como el
`dedup_hash`, y tiene dos mitades porque ninguna basta sola:

- **Desde el mismo sitio**: el span que envuelve la llamada, que el SDK captura leyendo
  el span activo justo antes de abrir el de LLM (`laplace.step.parent`). Cubre a quien
  decora sus funciones con `@observe`, que es la mitad de la documentación.
- **Con las mismas instrucciones**: huella del prompt de sistema más los nombres de las
  herramientas declaradas. Cubre a quien no decora nada: sus llamadas cuelgan todas del
  mismo span, o de ninguno, y sin esto seguirían mezcladas.

Si no hay ninguna de las dos —payloads desactivados y sin decorar— se cae al nombre del
span, que es lo que había antes. No se inventa una identidad que no se puede sostener.

El riesgo conocido es el contrario: un prompt de sistema con datos variables genera una
huella distinta por llamada y parte un paso en muchos. Entonces las reglas se quedan
calladas por falta de llamadas, que es el lado seguro. Mezclar da diagnósticos falsos;
partir da silencio.

Consecuencia visible: los hallazgos ya no se titulan `chat gpt-5.6-terra`. Se titulan
con el nombre de la función que hace la llamada, sin que nadie tenga que nombrar nada.

### D-061 — El descuento anti-doble-conteo cruza por paso, con vuelta al nombre
El mapa que impide contar dos veces el mismo ahorro cruza `RepeatedGroup` con
`ModelUsage`. Al cambiar la clave de agrupación, ese cruce se quedó sin pareja y las
repeticiones volvieron a contarse dos veces: lo cazó el test de regresión del solape, que
es justo para lo que está. Ahora las dos partes cruzan por `step_key`.

Y una segunda mitad que no es teórica: **el día del despliegue, todo lo ya guardado tiene
`step_key` vacío**. Las dos consultas caen al nombre del span cuando falta la clave, de
modo que esas trazas se comportan exactamente como antes en lugar de juntarse todas bajo
una clave vacía y dejar el descuento sin pareja durante una ventana entera.

### D-062 — Las repeticiones se detectan por entrada pero se reportan por paso
La señal sigue siendo `dedup_hash`: misma entrada, misma llamada. Pero un agente que
reintenta lo hace con cada pregunta de cada usuario, así que agrupar el informe por
entrada llenaba el panel de tarjetas idénticas —una por pregunta— diciendo todas lo
mismo. En el agente sin instrumentar eran once tarjetas para tres problemas. Se agrupa
por (paso, modelo), se enseña el número total y se guarda la entrada más repetida como
evidencia. Por modelo también: el mismo paso con dos modelos son dos problemas con dinero
distinto, y mezclarlos dejaría las repeticiones de uno sin descontar del otro.

### D-063 — El descuento quita llamadas, no sólo tokens
Cuarta forma que ha encontrado este proyecto de contar dos veces el mismo dinero.
`_without_duplicates` recortaba los tokens y dejaba `calls` intacto, así que la regla del
contexto fijo seguía viendo dieciséis llamadas donde sólo quedaban cuatro y prometía el
ahorro de cachear doce que la regla de repetición ya había dado por eliminadas. También
corregía mal la media de tokens de salida, que es el umbral de la regla del modelo caro.

### D-064 — La batería del doble conteo se amplía, nunca se sustituye
El mismo fallo ha aparecido por cuatro caminos distintos: dos reglas sobre los mismos
tokens, dos reglas sobre el mismo paso, el cruce del descuento sin pareja, y el descuento
de tokens sin llamadas. Cada uno deja su caso en `test_insights.py` y ninguno sustituye a
otro. Dos exigencias sobre esos tests:

1. **Las cifras se comprueban exactas, no con un tope.** Un `assert x < gasto_total` deja
   pasar el doble conteo mientras quepa dentro del gasto, que es casi siempre.
2. **Se comprueba que muerden.** Se rompe el motor a propósito de cuatro maneras y se
   verifica que cada una tumba al menos un test. Un test de regresión que no puede fallar
   es peor que no tenerlo, porque da confianza sin darla.

El caso del mapa de descuento se comprueba además directamente sobre `_duplicate_tokens`,
sin base de datos: la agrupación ya ha cambiado dos veces y la propiedad tiene que
sobrevivir a la próxima.

### D-065 — Dos agentes de ejemplo, y el que manda es el mal instrumentado
`agente_ejemplo.py` está instrumentado con cuidado y sirve para enseñar el producto.
`agente_sin_instrumentar.py` es lo que sale de leer diez líneas del README con prisa: un
`@observe` en la entrada, las funciones internas sin decorar y ningún `name=` en ninguna
parte. Es el que decide si el arreglo está hecho, porque es el primer usuario. Al ejemplo
cuidado se le han quitado los `name=` que le quedaban: una demo que esquiva el problema
no demuestra nada.

### D-066 — La traducción de fila a modelo se comparte entre los dos almacenes
`row_to_span` y `row_to_summary` vivían dentro del almacén de ClickHouse. Si el de
SQLite hubiera tenido las suyas, las dos copias habrían empezado a separarse el primer
día que alguien añadiera un campo, y el modo local habría dejado de enseñar lo mismo sin
que nadie lo notara. Están en `storage/_rows.py`, junto con la lista de columnas, y las
usan los dos. Las filas llegan como diccionarios, con las etiquetas que puso la propia
consulta: por posición, añadir una columna en medio desplaza el resto en silencio, y ya
pasó una vez.

### D-067 — «Cómo lo hemos detectado» enseña la consulta del almacén que respondió
La ficha de un problema muestra la consulta que se ha ejecutado, no una de ejemplo. El
motor la importaba directamente del módulo de ClickHouse, así que en modo local habría
enseñado una consulta que nadie ejecutó —y que ni siquiera correría, porque SQLite no
tiene `uniqExact` ni `argMax`—. Ahora se la pide al almacén, que es quien sabe cuál usó.
De paso, eso rompe la última dependencia del motor con ClickHouse.

### D-068 — Los drivers de la nube son un extra, no una dependencia
`clickhouse-connect` y `psycopg` pasan a `laplace-backend[cloud]`, con imports
perezosos. Quien instala `laplace-trace[ui]` para mirar su agente en el portátil no
tiene por qué descargar dos drivers de bases de datos que no va a arrancar. Simétrico:
`fastapi` y `uvicorn` no entran en el SDK por defecto, porque un SDK de trazas no puede
arrastrar un servidor web a la imagen de producción de nadie.

### D-069 — La interfaz se sirve desde el mismo origen que la API, y se exporta a HTML
Es la decisión que hace posible el modo local, y toca a las dos mitades del producto.

Las páginas pasan de componentes de servidor a componentes de cliente que piden sus
datos por `fetch`. Con eso, `next build` con `output: "export"` produce HTML estático que
`laplace ui` sirve desde Python: **la misma aplicación**, sin necesitar Node en la
máquina de quien la instala. Un `pip install` no puede exigir una cadena de herramientas
de JavaScript.

Consecuencias, todas asumidas a propósito:

* **Un solo origen.** En local, Python sirve la interfaz y la API. En la nube, Next
  reescribe `/api` y `/health` hacia el backend. Así no hay ninguna URL de backend
  incrustada en el build, ni CORS que abrir, ni una variable que se olvide en un
  despliegue y deje la pantalla en blanco.
* **Las rutas con identificador pasan a parámetro de consulta**: `/traza?id=…` y
  `/problema?id=…`. Una exportación estática no puede generar páginas para trazas que
  todavía no existen.
* **La exportación en JSON se arma en el navegador** con la traza ya cargada, en vez de
  con una ruta de servidor sólo para poner una cabecera.
* Se pierde el renderizado en servidor. Para una herramienta que mira lo que acaba de
  pasar y no cachea nada, el intercambio sale a cuenta: se gana que el producto quepa
  en un `pip install`.

El HTML exportado lo mete en el paquete `python scripts/build_ui.py`, que es un paso de
publicación. En el repositorio no está versionado: `laplace ui` lo busca en
`apps/web/out` cuando corre desde el árbol de código.

**Revisada el 8 de septiembre de 2026 y confirmada.** La pregunta era si merecía la pena
recuperar el renderizado en servidor. No: mantener una sola aplicación pesa más que el
SSR en una herramienta que mira lo que acaba de pasar y no cachea nada, y exigir Node
para un `pip install` rompería el modo local, que es la puerta de entrada al producto.
No se revierte.

### D-070 — `laplace demo` manda datos inventados, y lo dice tres veces
Quien acaba de instalar no tiene todavía un agente instrumentado, y una pantalla vacía no
enseña qué hace el producto. `laplace demo` emite ocho trazas con las tres patologías que
Laplace detecta. Van a un proyecto llamado `demo`, salen por la misma ingesta que
cualquier traza real —no hay una vía especial— y tanto el comando como la pantalla de
estado vacío avisan de que son datos simulados. La alternativa era enseñar un panel vacío
en el primer minuto, que es la peor primera impresión posible.

### D-071 — SQLite deduplica por clave primaria, no por motor de fusión
El exportador OTLP reintenta en timeouts y 5xx. En ClickHouse eso lo resuelve
`ReplacingMergeTree` más `LIMIT 1 BY`; en SQLite lo resuelve `INSERT OR REPLACE` sobre
`(project_id, trace_id, span_id)`, que es más simple y exacto desde el primer momento.
Es la única diferencia de comportamiento entre los dos almacenes, y va a favor del local.

### D-072 — La demo no puede sembrar el generador de números aleatorios
`random.seed(0)` en el ejemplo hacía deterministas los identificadores de traza de
OpenTelemetry, que salen de `random.getrandbits`. Dos ejecuciones de `laplace demo`
producían las mismas trazas, indistinguibles entre proyectos. Se quitó, y de paso la
ficha de una traza pide su proyecto al leerla: un identificador de traza es único dentro
de un proyecto, no entre proyectos.


## 2026-09-08 — La proyección deja de mentir, y las alertas a Slack (§3)

### D-073 — Por debajo de un día de datos no se proyecta a mes; se enseña lo gastado
Era lo más urgente que quedaba abierto: con una hora de datos, el panel anunciaba
**468 $/mes**. Había un aviso en pantalla, y no bastaba. El número se lee antes que el
aviso, y una cifra absurda en la primera pantalla no se corrige leyendo el párrafo de
debajo: quema la confianza en todo lo demás. Y esa primera pantalla es exactamente el
momento del modo local recién instalado.

El criterio cambia de «proyecta y avisa» a «proyecta sólo cuando puedas»:

* Con menos de `MIN_DAYS_FOR_PROJECTION` (un día) de datos observados,
  `monthly_cost_usd`, `monthly_avoidable_usd`, `monthly_necessary_usd` y el
  `monthly_saving_usd` de cada hallazgo valen **`None`**, no cero. Cero se lee como «no
  cuesta nada», que es lo contrario de lo que queremos decir.
* En su lugar existen siempre `window_cost_usd`, `window_avoidable_usd` y
  `window_necessary_usd`: dinero medido, ya gastado. La pantalla enseña eso y dice desde
  cuándo: «te ha costado 0,65 $ — en menos de un minuto de datos».
* **La ventana usada es visible en los dos casos.** Cuando se proyecta, se dice desde
  cuántos días se extrapola —los observados, no los del selector—; cuando no, se dice
  cuánto se lleva midiendo y cuánto falta para que aparezca la previsión.
* Gasto y ahorro comparten base **siempre**, y ahora también comparten la decisión de
  proyectar o no: `_projection_base()` es el único sitio que la toma. Es la regresión de
  D-058 con dos formas nuevas de repetirse (un total proyectado contra un ahorro
  observado, o al revés), así que tiene su propio test.

Dos efectos secundarios buenos:

* **Se cae el suelo de una hora de `_observed_days`.** Existía para que la división no
  explotase, y de paso convertía «llevas diez segundos de datos» en «llevas una hora»,
  que es una ventana que nadie ha observado. Ahora la división está protegida por la
  puerta de la proyección, así que el número puede decir la verdad.
* **Los hallazgos se ordenan por dinero ya gastado**, no por el proyectado. Da
  exactamente el mismo orden —la proyección multiplica a todos por lo mismo— pero existe
  también cuando no hay proyección.

**Descartado:** dejar sólo el aviso (es lo que ya había y no funciona); proyectar con un
intervalo de confianza (una barra de error sobre una hora de datos es rigor decorativo
sobre una muestra que no lo aguanta); usar el mínimo entre lo proyectado y algún tope
(inventar un techo es inventar otra cifra).

### D-074 — Una alerta por hallazgo y periodo de calma: agrupación *y* silencio
El riesgo de este punto no es no avisar, es avisar de más. Una alerta que se repite se
ignora, y en cuanto se ignora una se ignoran todas. El criterio elegido, que es a la vez
agrupación y periodo de calma:

1. **Un proyecto, un mensaje.** Todos los hallazgos que vencen en el mismo ciclo viajan
   en una sola notificación. Seis mensajes seguidos se leen como spam.
2. **Periodo de calma por hallazgo** (`quiet_hours`, 24 h por defecto). Tras avisar de
   un hallazgo no se vuelve a avisar de él hasta que pase, **aunque el problema siga
   ahí**. Es el freno principal.
3. **Empeorar no reabre el silencio.** «Volvemos a avisar porque ha subido» suena
   razonable y es la puerta trasera por la que regresa la repetición: cualquier cifra
   oscila, así que cualquier umbral de empeoramiento acaba disparando solo.
4. **Un hallazgo sólo se olvida tras un periodo de calma entero sin verse.** Si se
   olvidara en cuanto baja del umbral, uno que baila alrededor del umbral entraría y
   saldría del estado y cada reentrada contaría como «nuevo». Por eso se guarda
   `seen_at` además de `notified_at`.
5. **No hay mensajes de «resuelto».** Son otro mensaje por problema, y el problema ya se
   ve en la pantalla.
6. **El mensaje dice cuántos está callando.** Si no, el silencio se lee como que el
   problema ha desaparecido.

El umbral se compara contra **dinero ya gastado en la ventana**, no contra la proyección
mensual. Con D-073, un proyecto recién instalado no tiene proyección; si el umbral la
mirase, no podría alertar nunca, que es justo cuando más se agradece. Además el dinero
gastado es un hecho y la proyección es una estimación: se dispara sobre el hecho.

Un hallazgo que sólo cuesta tiempo (`costs_money=False`) nunca cruza un umbral en
dólares, así que **no alerta**. Es deliberado: una alerta de coste habla de coste, y sale
en la pantalla con los segundos que tira.

**Descartado:** un mensaje por hallazgo (ruido); avisar en cada ciclo mientras el
problema exista (es literalmente el fallo que hay que evitar); un resumen diario fijo a
una hora (llega tarde para lo nuevo y repite lo viejo); dejar el periodo de calma en el
código (un agente de juguete y uno de producción no tienen el mismo «esto merece que me
despierten», y por eso `min_usd`, `quiet_hours`, `muted` y `muted_kinds` son por
proyecto, en un JSON que se versiona y se revisa en un PR).

### D-075 — Las alertas son el mismo código en local y en la nube; el estado va donde ya va lo mutable
Nada de «en local no tiene sentido». El ciclo de alertas vive en el `lifespan` de la
misma aplicación FastAPI que sirve la API, así que `laplace ui` —que es esa aplicación
con otras variables de entorno— alerta igual que un despliegue con Docker. Se encienden
con las mismas variables (`LAPLACE_ALERTS_*`), y el CLI sólo rellena
`LAPLACE_ALERTS_BASE_URL` con su propio puerto para que el enlace de la alerta abra la
ficha del problema en el Laplace que tienes delante.

Lo único que cambia es dónde se recuerda qué se ha avisado, y cambia igual que todo lo
demás: **SQLite en local, Postgres en la nube**. Si no hay ninguno de los dos, el estado
queda en memoria y se dice por el log que un reinicio puede repetir un aviso; es una
degradación, no un modo de funcionamiento.

Están **apagadas por defecto**. Nada que mande mensajes fuera se enciende solo. Y el
webhook se valida contra `slack.com` antes de cada envío: una errata en la configuración
no puede acabar publicando la factura de alguien en un host cualquiera.

### D-076 — Un hallazgo con coste no fiable se anuncia como suelo, nunca como cifra
La regla conservadora del proyecto, aplicada a lo que sale fuera. Cada hallazgo lleva
ahora sus propios contadores de pasos sin tarifa (`unknown_cost_spans`) y de pasos
cobrados a tarifa asumida (`assumed_rate_spans`), agregados por las mismas consultas que
lo detectan; de ahí sale `cost_is_floor`. Antes esas marcas sólo existían a nivel de
proyecto, así que un hallazgo limpio y uno dudoso se presentaban igual.

Donde eso importa:

* La alerta escribe «al menos 5,00 $», no «5,00 $», y añade por qué: hay pasos cuyo
  modelo no está en la tabla o cuyo metro de facturación no se ha podido confirmar, así
  que el coste real puede ser mayor, nunca menor.
* La tarjeta del inicio antepone `≥` y lleva su etiqueta.
* Un hallazgo con coste incompleto **cuenta menos de lo que vale** para el umbral, así
  que puede quedarse por debajo y no alertar. Se acepta: equivocarse callando es el lado
  correcto.

El descuento anti-doble-conteo (`_without_duplicates`) resta tokens y llamadas pero no
estos contadores. Deja marcado como suelo algún hallazgo que quizá ya no lo sea, que es
otra vez el lado seguro del error.

## 2026-09-08 — El panel (§4) y el modo en vivo

### D-077 — El panel mide por unidad de trabajo, y dice la lectura con palabras
Un panel de líneas con tokens y latencia no aporta nada frente a Grafana, así que este
sólo existe por dos ideas. La primera: **las métricas protagonistas son todas por
ejecución** —coste, tokens, pasos, duración— y los totales van debajo, en letra pequeña
y con la etiqueta «como contexto». Si el gasto sube un 40 % y hay un 40 % más de
ejecuciones no pasa nada; si sube con las mismas ejecuciones, hay degradación. Un panel
de totales pinta las dos cosas igual.

La segunda mitad de la idea es que **la lectura se dice en una frase**, no se deduce de
dos series. `read_out()` compara el periodo con el inmediatamente anterior de la misma
duración y emite un veredicto de seis:

| Situación | Veredicto | Lo que se lee |
|---|---|---|
| Sube el gasto y suben las ejecuciones | `normal` | «Subida acompañada de más ejecuciones: normal.» |
| Sube el gasto, ejecuciones planas | `revisar` | «Subida sin más ejecuciones: revisar.» |
| Suben las dos | `mixto` | Se separa la parte de volumen de la de coste unitario |
| **Bajan las ejecuciones y el gasto no** | `revisar` | La degradación que un panel de totales no ve |
| Baja el coste por ejecución | `mejora` | |
| Baja el gasto sólo por menos tráfico | `estable` | «no una mejora: si el tráfico vuelve, el gasto vuelve» |

Debajo del veredicto van siempre las tres cifras que lo sostienen —gasto, ejecuciones y
coste unitario— con su concordancia: «las ejecuciones **suben** un 40 %», no «sube».
Una frase mal escrita hace dudar de los números que la acompañan.

Un cambio por debajo del 10 % (`MATERIAL_CHANGE`) no es un cambio y no se dice su
porcentaje: sin ese umbral, el panel gritaría «degradación» cada vez que alguien
despliega un prompt un poco más largo. La duración por ejecución es **de traza**, de su
primer span al último, y no suma de spans: los spans se solapan y sumarlos daría un
número que no es la espera de nadie.

**Descartado:** un panel de series temporales al uso (es un Grafana peor); percentiles
por defecto (interesan para latencia, no para lo que cuesta una unidad de trabajo);
mostrar sólo la variación sin las cifras absolutas (un «+40 %» sin saber sobre qué no se
puede accionar).

### D-078 — Un pico se atribuye con datos o se dice que no se sabe
La segunda idea. Un pico sin causa es una alarma sin acción, y una causa inventada es
peor que ninguna. El criterio, entero:

* **Un pico es coste por ejecución, no gasto del tramo.** Una hora punta con el triple
  de tráfico no es una anomalía, es un martes por la mañana; si el criterio fuese el
  gasto, el panel señalaría cada hora de demanda y nadie volvería a mirarlo.
* **La línea base es la mediana** de los tramos con ejecuciones, no la media: una media
  contaminada por el propio pico sube con él y acaba escondiéndolo.
* **Sin al menos seis tramos con datos no se habla de picos.** Con tres, un pico es la
  mitad de la muestra y la mediana no es línea base de nada. Se dice por qué, no se
  esconde la sección.
* **Las causas son comprobables en las trazas**, y todas de la misma forma —«esto está
  aquí y no estaba antes»—: un modelo que aparece por primera vez, una herramienta
  nueva, un paso nuevo, o un paso que se lleva más del 40 % del sobrecoste **comparado
  por ejecución** (en bruto saldría siempre el paso más caro del agente). La versión de
  prompt entra aquí cuando exista la Fase 5, y el hueco ya está en la lista de tipos.
* **Si nada de eso da resultado, se escribe «No identificamos la causa»** y se enlazan
  igualmente las trazas del tramo. Es una respuesta honesta y accionable. Ninguna
  correlación estadística, ningún «coincide con».
* Cuando un paso se lleva más del 95 % del sobrecoste se dice «prácticamente todo» en
  vez de un porcentaje: la parte de un paso puede pasar del 100 % del neto —si otro se
  ha abaratado a la vez— y un «101 %» se lee como un error de cálculo aunque sea cierto.

El enlace de cada pico lleva al explorador con el rango exacto del tramo y orden por
coste: de la alerta al dato en un clic.

### D-079 — Modo en vivo por polling; los WebSockets son una evolución, no un pendiente
El explorador se refresca solo cada cinco segundos, con indicador y botón de pausa. Es
polling contra la misma API de lectura que ya existe. Un socket obliga a un servidor con
estado, a reconexión, a latidos y a un camino distinto en local y en la nube; mirar
trazas es mirar lo que acaba de pasar, y cinco segundos no cambian ninguna decisión.
Cuando haya volumen para que el coste del polling importe, se cambia por debajo sin
tocar la pantalla.

Cuatro detalles que no son opcionales:

* **Sólo con el orden por más recientes y sin cursor.** Sobre un orden por coste, «lo
  nuevo» no va arriba, y añadir filas al principio mentiría sobre el orden pedido. El
  botón, en ese caso, cambia el orden en lugar de desactivarse en silencio.
* **No se encadenan peticiones.** Si una tarda más que el intervalo no se lanza otra
  encima: contra un backend lento, eso convierte una pestaña abierta en una carga.
* **Al pausar se queda lo que ya ha llegado.** Borrarlo castigaría justo a quien ha
  visto algo y quiere mirarlo con calma.
* **Se para al desmontar.** Un intervalo huérfano sigue pidiendo para siempre.

### D-080 — El periodo anterior tiene que ser utilizable, o no hay comparación
Es D-073 otra vez, con otra cara. Comparar el rango actual contra un periodo anterior en
el que el proyecto casi no existía produce «el gasto sube un 193.100 %», que es un
número real y absurdo: sale de dividir entre casi nada, y quien lo lea dejará de creerse
el resto de la pantalla, que sí es cierto. Ese número apareció en la primera prueba del
panel con datos sembrados.

Dos guardas, las dos en `comparable()`: el periodo anterior necesita **al menos 5
ejecuciones** y estar **cubierto por datos al menos a la mitad**. Si no las cumple, todas
las variaciones valen `None` —no cero—, el veredicto es `sin-base` y la pantalla dice el
motivo concreto y que las cifras de abajo son lo observado, medido, sin comparación.

Lo mismo dentro de la serie: un tramo sin ejecuciones tiene coste por ejecución `None`,
se deja **en blanco** en la gráfica y se explica al pie. Pintarlo como cero dibujaría una
bajada del coste que nadie ha tenido.

### D-081 — La interfaz del árbol gana a la copia del paquete, y se dice cuál se sirve
`_ui_dir()` buscaba primero `packages/sdk-python/laplace/ui`. Con ese orden, cualquier
`scripts/build_ui.py` ejecutado alguna vez dejaba una copia que **tapaba en silencio**
todos los `next build` posteriores: se desarrolla contra una interfaz vieja sin ningún
aviso y sólo se nota cuando algo recién escrito no aparece. Costó un rato.

Ahora gana `apps/web/out`. No hay conflicto en producción: dentro de un wheel esa ruta no
existe, así que allí sólo hay un candidato. Y al arrancar se deja escrito por el log qué
directorio se está sirviendo, y un aviso si hay una segunda copia construida sin usar.

### D-082 — La duración de la serie se redondea al milisegundo en SQLite
`julianday()` trabaja en días con coma flotante y a escala de milisegundo deja restos de
microsegundo que ClickHouse, que cuenta con `dateDiff('millisecond')`, no tiene. Nadie
mide una espera en microsegundos, y redondeando, las dos series son idénticas y el test
de paridad del panel puede comparar exacto en vez de con una tolerancia que taparía una
divergencia de verdad.

## 2026-09-12 — Evaluaciones (§5)

### D-083 — El veredicto de persona y el de máquina no se mezclan, y la separación es estructural
Era la condición firme del encargo, y la forma de cumplirla no es una etiqueta bien
pintada: es que **no exista la fila que los mezcla**.

* El coste del juez vive en un bloque `judge` **dentro** de la anotación, y la
  traducción de fila a modelo lo arma sólo si la fuente es de máquina. Una anotación
  humana escribe `NULL` en las seis columnas del juez y, si alguna llegase con valor,
  se ignora: ante una contradicción se cree a la etiqueta de la fuente.
* **La ruta de anotar no tiene campo `source`.** Sólo crea veredictos humanos. El de
  máquina entra por `/api/judge` y por ninguna otra puerta, así que ni un cliente
  equivocado ni un `curl` a mano pueden colar un veredicto de modelo disfrazado de
  persona.
* **El acierto se calcula por separado para cada fuente y nunca se promedia.** La
  comparación A vs B devuelve dos bloques y la pantalla pinta dos bloques. Si un juez
  optimista y unas personas exigentes se promediaran, el número resultante no querría
  decir nada y taparía justo lo que hay que mirar.
* Cuando las dos fuentes se contradicen **se dice en la pantalla**. No es un fallo: es
  el dato más interesante que puede dar esta pestaña, porque significa que el prompt del
  juez y la cabeza de quien anota no entienden igual «bien». Un empate no cuenta como
  contradicción: «no lo sé» no es «lo contrario».
* En la interfaz, el punto del juez va hueco y el de la persona relleno, y el chip del
  juez lleva siempre la palabra «juez» delante.

**Descartado:** un campo `source` en la ruta de anotar con validación (la validación se
olvida; la ausencia del campo no); un acierto «combinado» con el humano como desempate
(es un promedio con otro nombre); guardar el coste del juez en una tabla aparte (se
puede unir mal, y un veredicto sin su coste al lado es medio veredicto).

### D-084 — El almacén de metadatos pasa a tener dos implementaciones de verdad
Hasta ahora, en modo local `build_metadata_store` devolvía el almacén nulo. Se podía:
los huecos de las fases 3 y 4 devolvían vacío y nadie lo notaba. Con las evaluaciones
deja de poderse, porque **anotar es el gesto más básico de la pestaña** y un modo local
que no pudiera anotar sería exactamente la versión recortada que este proyecto lleva
evitando desde D-015.

Así que lo mutable va donde ya va lo mutable de cada modo: **SQLite en local —el mismo
fichero que guarda los spans— y Postgres en la nube**. La traducción de fila a modelo se
comparte entre los dos (D-066 otra vez), y hay un test que siembra lo mismo por los dos
caminos y exige que se lea igual.

El nulo sigue existiendo, pero sólo para lo que de verdad es opcional: si en la nube se
cae Postgres, la ingesta y la lectura de trazas siguen en pie. Y **escribir sobre el
nulo levanta un error explícito que la API traduce a un 503**, en vez de devolver un 200
que haría que alguien anotase veinte trazas y las perdiese todas sin enterarse.

### D-085 — Los casos salen de tráfico real, y se puede ver de dónde
Un conjunto se crea desde un filtro del explorador y se materializa con las trazas que
ese filtro selecciona: cada caso guarda el `trace_id` del que salió, su entrada y lo que
el agente respondió entonces. No hay forma de escribir un caso a mano, y es a propósito:
un conjunto inventado no dice nada sobre el agente de nadie.

El campo se llama `expected` pero **significa «lo que había», no «lo que está bien»**, y
así está escrito en el contrato y en la pantalla. Certificarlo es trabajo de quien anota.

El filtro con el que se formó se guarda y se enseña en modo avanzado, por el mismo
motivo que la consulta de un hallazgo (D-067): un conjunto del que no se sabe cómo se
formó no se puede discutir.

### D-086 — Laplace no ejecuta el agente de nadie: la tirada la corre el SDK
La comparación A vs B necesita que alguien pase el conjunto por las dos versiones. Ese
alguien **no es el backend**. `laplace.run_dataset(conjunto, fn, variant=…)` corre en el
proceso del usuario, con sus claves, sus dependencias y su red; las trazas llegan por la
ingesta normal y lo que se registra aparte es el parte de qué caso produjo qué traza.

Un backend que ejecutase código ajeno necesitaría un sandbox, las credenciales del
usuario y una copia de su entorno: tres problemas grandes que no hace falta resolver
para responder a «¿la versión nueva acierta igual y cuesta menos?».

Consecuencias buenas: el runner no necesita saber nada del agente salvo que es un
callable; las trazas de una evaluación son trazas normales y se ven en el explorador
como cualquier otra; y la asociación caso→traza es **exacta**, no inferida de un
atributo que alguien podría escribir mal.

Dos detalles que no son opcionales: un caso que lanza excepción **se registra como
fallado y no se descarta** —descartarlo haría que una versión que revienta la mitad de
las veces saliera con el mismo acierto que una que funciona—, y el `flush()` va **antes**
de registrar la tirada, porque si el parte llegara primero la comparación buscaría costes
de trazas que aún no existen y daría cero, que se lee como «gratis».

### D-087 — Ningún porcentaje de acierto sin su guarda, y ningún ganador sin margen
La cuarta cara del mismo error: el 468 $/mes desde una hora de datos, el 193.100 %
contra un periodo vacío, y ahora un «94 %» sacado de cuatro casos. Dos guardas:

1. **Por debajo de `MIN_CASES_FOR_RATE` (10 casos con veredicto) no se enseña un
   porcentaje.** Se enseñan los casos en bruto —«3 de 4»— y se dice que no bastan. El
   `value` es `None`, nunca cero: cero significaría «no acierta ninguno».
2. **Por encima, el porcentaje va con su margen**, calculado con el intervalo de Wilson
   al 95 %. El intervalo normal de toda la vida se sale del rango justo en los dos casos
   que más aparecen en un conjunto pequeño: diez de diez da «entre el 100 % y el 100 %»
   y nueve de diez, «entre el 71 % y el 109 %».

Y la que de verdad importa: **en A vs B no se declara un ganador si los márgenes se
solapan**. La respuesta es «no se distinguen con estos casos», y el detalle dice que
hacen falta más casos anotados, no otra lectura de éstos. Declarar ganador sobre una
diferencia que cabe dentro del margen es literalmente cómo se despliega una regresión
creyendo que es una mejora.

**El coste no lleva margen, y eso es deliberado.** El acierto es una muestra; el coste
de una tirada es la factura de lo que se ejecutó, medida paso a paso. Ponerle una barra
de error sería fingir una incertidumbre que no tiene. La pantalla lo dice con esas
palabras, porque la asimetría se nota y sin explicación parecería un descuido.

Dos reglas más sobre qué cuenta como fallo: un caso que **revienta** cuenta como fallo
aunque nadie lo haya anotado, y un caso **sin anotar** no cuenta como fallo —contarlo
convertiría «no lo he mirado» en «está mal», y el acierto bajaría al añadir casos—.

### D-088 — Lo que cuesta juzgar es coste real, se mide igual y se enseña aparte
Si no registráramos lo que gasta nuestro propio juez, sabríamos menos de nuestro gasto
que del del usuario, que sería un chiste malo en un producto que se vende como «te digo
lo que cuesta tu agente».

El coste del juez se calcula con **la misma tabla de precios** con la que medimos el
gasto ajeno, se guarda dentro de la anotación y se enseña en tres sitios: pegado al chip
del veredicto, en la ficha de la tirada y en la comparación. Si el modelo del juez no
está en la tabla, su coste es «no lo sabemos» y no cero (D-043 otra vez).

**Va siempre separado del coste del agente.** Sumarlos haría que la versión que alguien
decidió mirar con más cuidado pareciese más cara de ejecutar, que es justo al revés de
lo que pasó.

El juez está **apagado por defecto** y tiene un tope de trazas por tanda: un juez suelto
sobre diez mil trazas es una factura sorpresa, y este producto existe para que no haya
facturas sorpresa. Su prompt se puede ver en modo avanzado, tal cual se manda, y lleva
versión: dos veredictos emitidos con prompts distintos no son comparables y sin la
versión no habría forma de saberlo tres semanas después.

Una decisión pequeña que evita un error grande: si el juez devuelve algo que no es el
JSON pedido, el veredicto es `unknown`, **no `fail`**. «No he entendido al juez» y «el
agente lo hizo mal» son cosas distintas, y confundirlas metería en el acierto del usuario
un fallo que es nuestro.

### D-089 — Qué añade Avanzado en Evaluaciones
La pregunta de siempre, porque si la respuesta fuera «nada» la pantalla estaría
incompleta. En esta pestaña, el modo avanzado añade:

* **Los márgenes de cada acierto** y de dónde salen: «entre el 79 % y el 98 % (Wilson,
  95 %)». En modo diagnóstico se lee el porcentaje; en avanzado, su incertidumbre.
* **El prompt exacto del juez**, sistema y usuario, tal cual se manda. Un veredicto que
  no se puede auditar no se puede discutir, y el primer «este fail está mal» llega el
  día uno.
* **El coste del juez por veredicto**, con su modelo, su versión de prompt y sus tokens.
* **El filtro con el que se materializó cada conjunto**, y los identificadores de las
  tiradas, del conjunto y de las trazas.
* En la tabla de tiradas, la columna de **lo que costó juzgar** cada una.

## 2026-09-12 — Prompts (§6)

### D-090 — La versión de un prompt se escribe en la traza, y sólo si se ha comprobado
Todo lo que hace que esta pestaña valga la pena depende de una sola cosa: que se sepa
**qué versión produjo cada llamada**. Sin eso, «v8 cuesta 0,004 $ por ejecución» habría
que estimarlo, y una métrica estimada sobre un texto que alguien pudo cambiar a mano no
es una métrica, es una corazonada con decimales.

Así que la versión viaja en el span (`laplace.prompt.name` / `laplace.prompt.version`,
columnas `prompt_name` / `prompt_version`) y la escribe el SDK. Lo que no es obvio es
**cuándo** la escribe: sólo si el texto de esa versión **aparece de verdad** en los
mensajes que se han enviado. La alternativa cómoda era fiarse del último `get_prompt()`
del contexto, y es exactamente la que produce el error silencioso: un agente que pide el
prompt y luego llama al modelo con otro texto —porque lo reescribe, lo concatena o lo
sustituye— le colgaría a esa versión tráfico que no es suyo, y como todas las cifras de
la pestaña son por versión, nadie lo notaría jamás.

La comprobación es una búsqueda de subcadena con espacios normalizados, en el único
punto por el que pasan las dos integraciones (`record_request`). Cuesta microsegundos
sobre una llamada de red de cientos de milisegundos.

**Descartado:** deducirlo del último `get_prompt()` (atribuye tráfico ajeno); hash del
prompt de sistema en vez del número de versión (obliga a buscar el hash en una tabla
para decir «v8», y no distingue dos versiones con el mismo texto); pedirle al usuario
que marque la llamada a mano (se olvida, y lo que se olvida no se mide).

### D-091 — Servir prompts mete a Laplace en el camino caliente, y eso hay que pagarlo
D-086 dice que Laplace no ejecuta el agente de nadie. Servir prompts es la misma
frontera cruzada en la otra dirección: no ejecutamos nada, pero si el agente nos pide el
texto antes de llamar al modelo, **un Laplace caído es una incidencia en producción de
otra empresa**. Un observatorio que pueda tumbar lo observado no vale, así que el SDK se
diseña para que eso no ocurra nunca, en este orden:

1. **Caché con caducidad de un minuto.** No hay una petición por llamada, y un rollback
   tarda como mucho ese minuto en llegar a un proceso ya arrancado. Se dice en la
   respuesta del despliegue, porque un rollback del que no se sabe cuándo hace efecto no
   tranquiliza a nadie.
2. **Si el backend no responde, se sirve la copia guardada aunque esté vencida**, con un
   aviso por el log. Un prompt de hace cinco minutos es infinitamente mejor que una
   excepción en mitad de la petición de un usuario final.
3. **Si no hay copia, el `fallback=` del código**, registrado como versión 0.
4. **Si no hay ni eso, un error explícito** que dice que pases `fallback=`. No hay quinta
   opción: devolver un prompt vacío haría que el agente llamase al modelo sin
   instrucciones, que cuesta dinero y no lo dice nadie.

El tercer punto tiene una consecuencia que no es cosmética: **la reserva se cuenta como
reserva, no como la versión de producción**. Ese tráfico es real y no salió de ninguna
versión guardada; sumarlo a producción falsearía justo la cifra que se está mirando, y
además taparía el dato interesante —que Laplace estuvo caído para ese agente—, que la
pestaña dice con todas las letras cuando pasa.

**Descartado:** un cliente de prompts sin caché (una petición por llamada del agente);
fallar cuando el backend no responde (convierte nuestra caída en la suya); servir un
texto vacío como último recurso (una factura sin instrucciones).

### D-092 — Un pico se atribuye a un prompt por las trazas, nunca por la hora del despliegue
El hueco de la causa `version_prompt` llevaba reservado desde que se escribió la
atribución de picos (D-078). Cerrarlo tenía una forma obvia y mala: cruzar el historial
de despliegues con la hora del pico. «Desplegaste la v8 a las 14:02 y el pico empieza a
las 14:00» se lee como una causa y es una coincidencia temporal; con un equipo que
despliega tres veces al día, acabaría señalando un despliegue en casi todos los picos.
Es literalmente la correlación insinuada que el panel existe para no contar.

Lo que se usa es la **versión que aparece en las trazas del tramo y no aparecía en las
de antes**, que es el mismo tipo de hecho que «un modelo nuevo» o «una herramienta
nueva»: medido, comprobable y con un enlace a las trazas que lo sostienen. El historial
de despliegues sigue guardándose, pero para contar la historia en la pestaña de Prompts
—«la v7 estuvo puesta de martes a jueves»—, no para atribuir.

La causa del prompt se nombra **antes** que las demás. De todo lo que puede cambiar
alrededor de un pico, es lo más accionable (hay un botón para deshacerlo) y lo más
frecuente: un modelo nuevo se despliega una vez al trimestre y un prompt se toca los
martes.

### D-093 — Sin adoptar la gestión de prompts, la pestaña enseña lo que dicen las trazas
Una pestaña que sólo funciona después de mover todos tus prompts a otro sitio es una
pestaña que casi nadie llega a ver. Y lo que hace falta para no dejarla en blanco ya
estaba hecho: **la identidad de un paso incluye la huella de sus instrucciones** (D-060),
así que dos `step_key` distintos bajo la misma etiqueta son dos juegos de instrucciones
del mismo paso. Eso da, sin que el usuario haga nada: cuántas versiones ha tenido cada
paso, desde cuándo y hasta cuándo corrió cada una, y lo que costó por ejecución.

No da el texto completo —sólo se guarda la pista de 80 caracteres— ni el diff ni el
rollback, y la pantalla lo dice en vez de disimularlo: es menos de lo que da adoptar la
gestión, pero es real y es del usuario que está mirando.

Con una guarda que importa tanto como el dato. La fragilidad conocida de la identidad de
paso es que **un prompt con datos variables dentro genera una huella por llamada**. Sin
protección, esta pantalla enseñaría doscientas «versiones» de un paso que tiene una. Por
encima de ocho variantes se deja de listarlas y se dice lo que de verdad está pasando:
que ese prompt lleva una fecha o un nombre dentro, que eso es una plantilla y no un
histórico, y que sacándolo a variables Laplace podrá medirlo por versión.

### D-094 — En una comparación A vs B, con qué prompt corrió cada lado sale de las trazas
La conexión con Evaluaciones podría haberse hecho con la etiqueta de la tirada: el
usuario escribe `variant="prompt-v3"` y ya está. No sirve, por el motivo de siempre: esa
etiqueta la teclea una persona con prisa y se queda vieja a la segunda tirada. Lo que se
enseña es la lista de versiones que **usaron las trazas de esa tirada**, leída de los
propios spans. Si no coincide con lo que pone en `variant`, la que miente es la etiqueta.

Cuesta una consulta más por comparación —la misma que ya se hace para los costes, con
los mismos identificadores— y contesta a la primera pregunta que se hace cuando una
comparación sale rara: «espera, ¿con qué prompt corrió esto?».

### D-095 — Qué añade Avanzado en Prompts
La pregunta de siempre, porque si la respuesta fuera «nada» la pantalla estaría
incompleta. En esta pestaña, el modo avanzado añade:

* **El margen de cada acierto** debajo del porcentaje: «69–99 % (Wilson)». En modo
  diagnóstico se lee la cifra; en avanzado, su incertidumbre. Es exactamente lo mismo
  que en Evaluaciones, y a propósito: dos pantallas que dicen lo mismo se parecen.
* **Los tokens por ejecución** de cada versión, que es donde se ve *por qué* una versión
  cuesta más: casi siempre porque manda más contexto, no porque el modelo suba.
* **La numeración de líneas del diff**, a los dos lados.
* **El historial de despliegues completo**: cuándo, quién y con qué nota, con las vueltas
  atrás marcadas como tales. Y el aviso de que ese historial cuenta la historia pero no
  atribuye picos (D-092), que es el sitio natural para explicar esa distinción.
* **La huella (`step_key`) de cada juego de instrucciones** observado en las trazas, y
  los identificadores del prompt y de sus versiones.
* Las **fechas exactas** de creación de cada versión, en vez de sólo el último uso.

## 2026-09-12 — Cobertura, autenticación y proveedores de verdad

### D-096 — La cobertura va delante del dinero, no en Avanzado
El peor fallo que puede tener este producto es **indistinguible del éxito**: cuando la
identidad de un paso se parte, las reglas se callan por falta de llamadas, la pantalla
dice «no estás tirando dinero ahora mismo» y eso se lee como una buena noticia. Lo que
de verdad ha pasado es que no entendemos el agente.

Cuatro señales sobre las llamadas a modelos de la ventana: cuántas tienen **paso que se
distingue**, **tarifa conocida**, **tokens del proveedor** y **versión de prompt**. Tres
decisiones dentro:

* **Sitio.** Si alguna está baja, el bloque va **encima del héroe**, antes de cualquier
  cifra de ahorro. Si van bien, no desaparece: se queda en una línea plegable. Que el
  usuario sepa que esto se mide —y que hoy sale bien— es la mitad de lo que hará creíble
  el aviso el día que salga mal.
* **Palabras y arreglo, no un porcentaje.** «61 %» no acciona nada. «Cuatro de cada diez
  llamadas se nos escapan; decora tus funciones con `@observe`» sí. Cada señal lleva qué
  significa para las cifras de abajo y qué hacer para subirla.
* **Manda la peor, no la media.** Tres señales perfectas y la de los pasos por los suelos
  dan una media tranquilizadora y un diagnóstico que no vale nada.

Y la que hizo falta añadir al verla funcionando: **un paso partido no baja ninguno de los
cuatro porcentajes**. Cada una de sus llamadas tiene identidad; lo que pasa es que tiene
una distinta, así que el paso no se agrupa y las reglas no lo ven. Si no se subiera el
nivel por eso, la pantalla diría «entendemos el 100 % de tus llamadas» mientras el paso
más caro del agente es invisible para el motor. Ahora se nombra el paso y se explica que
sus instrucciones llevan datos variables dentro.

Dos detalles de honestidad: no gestionar prompts **no cuenta como defecto** —esa señal se
marca «no lo usas» y no pinta de rojo—, y `NothingToFix` deja de ser verde cuando la
cobertura es baja: «No hemos encontrado nada que arreglar, pero no hemos podido mirarlo
todo».

**Descartado:** una pestaña propia de diagnóstico del SDK (nadie la abriría antes de leer
la cifra de ahorro, que es justo cuando hace falta); una nota en modo avanzado (el que
tiene el problema es casi siempre el que no sabe que existe el modo avanzado); una
columna nueva en `spans` para marcar la identidad débil (mentiría sobre los spans ya
ingeridos; la firma del caso débil —`step_hint` vacío y `step_label` igual al nombre— ya
está guardada y se reconoce sin migrar nada).

### D-097 — La autenticación deniega por defecto, y el modo local es una exención escrita
La instalación de nube era «quien llegue a la URL lee y escribe todo», incluidos los
prompts y las respuestas en crudo de los usuarios finales de un cliente. Lo que cierra
eso no es un decorador por endpoint: es un **middleware que deniega por defecto** todo lo
que cuelga de `/api` y `/v1/traces` salvo una lista blanca de una entrada (`/health`).
Una ruta nueva **nace protegida**; abrirla exige escribirlo en la lista y se ve en el
diff. Mismo criterio estructural que D-083: la regla no se cumple porque alguien se
acuerde, sino porque el camino contrario no existe.

El middleware hace tres cosas: autenticar, comprobar el `project_id` que venga en la URL
y comprobar el que venga **en el cuerpo JSON**. El tercero es el que evita el agujero
clásico —proteger las lecturas, que son las obvias, y dejar abierta la escritura que
alguien añadió el martes—, y se puede hacer porque Starlette envuelve la petición en un
`_CachedRequest` pensado justo para esto.

Cuatro decisiones más:

* **La clave ata el proyecto.** Los spans traen el suyo dentro del protobuf; si no es el
  de la clave, se rechaza el lote entero con el motivo. Reetiquetarlos escondería una
  configuración mal puesta y el usuario descubriría dentro de un mes que su tráfico lleva
  semanas en el proyecto de otro.
* **Si no podemos verificar, denegamos.** El resto del producto se degrada cuando
  Postgres no responde; la autenticación no. El almacén nulo **levanta** en vez de
  devolver «esa clave no existe», porque lo cierto es «no lo sé» y eso es un 503, no un
  401 que mandaría al usuario a buscar el problema en su clave.
* **El modo local no pide clave a propósito**, y es una exención escrita
  (`auth_required = "auto"` → `sqlite` no, `clickhouse` sí), se dice por el log al
  arrancar y hay un test que la fija. Un despliegue de nube con la autenticación
  desactivada a mano deja un aviso en mayúsculas en cada arranque.
* **Las claves se crean por CLI, no por endpoint.** Un endpoint que emite credenciales
  necesita a su vez una credencial que lo proteja, y esa primera tiene que salir de una
  clave maestra en el entorno —un segundo camino de validación, un segundo sitio donde
  equivocarse—. `docker compose exec backend python -m laplace_backend.keys create` no
  añade ninguna ruta que pueda repartir poderes.

La clave se guarda **sólo como SHA-256**, y no con bcrypt: el secreto lo generamos
nosotros con 256 bits de entropía, así que no hay diccionario que probar y el coste de
derivación no compra nada a cambio de una dependencia en el camino de cada petición.

Lo que **no** es esto, y conviene decirlo: no hay cuentas, ni login, ni organizaciones,
ni roles. Hay claves por proyecto —y una comodín para el operador— guardadas por el
navegador de quien las escribe. Es el mínimo que hacía falta para que los datos de un
cliente no los lea otro; el sistema de identidades de verdad sigue pendiente (D-010).

### D-098 — Las integraciones se prueban contra los SDK reales, con el transporte falso
Las pruebas de OpenAI y Anthropic usaban dobles escritos a mano: objetos con la forma que
**nosotros creíamos** que tenían las respuestas. Eso deja fuera toda una clase de fallos,
y justo la más cara, porque instrumentar es lo primero que toca un usuario: que el parche
no llegue a la clase que el cliente usa de verdad, que un campo se renombre y lo leamos
como `None`, que el iterador de streaming cambie de forma.

Ahora los clientes son los reales, con su parseo, sus modelos Pydantic y su lector de
SSE; lo único falso es el transporte HTTP, que devuelve cuerpos con la forma documentada.
No hace falta clave, no se gasta dinero y se recorre todo el camino salvo la red. Los dos
SDK pasan a ser dependencias de desarrollo: si no estuvieran instalados, el fichero se
saltaría solo y volveríamos a probar contra dobles nuestros, que es como no probar.

Las pruebas **contra la API real** existen aparte, apagadas salvo con
`LAPLACE_LIVE_TESTS=1` y clave en el entorno, con tope de tokens por llamada. Sólo ellas
pueden comprobar lo que ninguna otra puede: que los números que guardamos son los que el
proveedor dice que ha cobrado.

Tres cosas que se descubrieron al hacerlo: `anthropic` ya no usa `httpx` sino `httpx2`
—un cliente HTTP propio pasado a mano falla con un `TypeError`—; el parche sigue
llegando bien a `openai` 3.x y `anthropic` 1.x; y `PromptTokensDetails` de OpenAI ha
ganado un campo `cache_write_tokens` que **no leemos** y para el que no tenemos tarifa
verificada. Eso último queda anotado y no arreglado a medias: inventarse un precio sería
exactamente lo que este producto no hace.

## 2026-09-12 — Determinismo, escritura de caché y nombres de modelo

### D-099 — Ninguna consulta escoge «una fila cualquiera», y hay un guardia que lo vigila
El patrón apareció por cuarta vez, así que deja de tratarse caso por caso. `any()` en
ClickHouse devuelve una fila arbitraria del grupo; `argMax(x, k)` devuelve una arbitraria
**de las que empatan en `k`**; y un `ORDER BY` sin desempate deja el orden al plan de
ejecución. Las tres cosas son la misma: una elección que el código no toma y que por eso
puede salir distinta en local y en la nube.

Lo grave no es que sea distinta, es **dónde** se nota. Ninguna de las tres rompe una
cifra, así que ninguna aparece en las pruebas de paridad que comparan dinero: divergen en
qué traza se enseña como ejemplo de un hallazgo, en qué orden salen dos problemas que
cuestan lo mismo, en qué paso va primero en un árbol con llamadas paralelas. Todo eso son
las reglas de detección, que es el peor sitio posible para que dos personas mirando la
misma pantalla vean cosas distintas.

Y los empates no son el caso raro: son **el** caso. Dos trazas que repiten un paso tres
veces empatan en `n` siempre. Dos llamadas lanzadas en paralelo comparten `start_time`
siempre. Por eso las pruebas de paridad nuevas siembran tráfico **empatado a propósito**:
sobre datos normales habrían pasado por casualidad.

Lo que se hizo, en los dos almacenes:

* `any(x)` → `max(x)`, que es lo que ya hacía SQLite. Cuatro sitios: el resumen de traza,
  las repeticiones (dos), y el uso por paso, donde además `any(trace_id)` pasa a
  `min(trace_id)` porque es lo que hace su gemelo local.
* `argMax(x, n)` → `argMax(x, (n, x))` y `argMin(x, start_time)` →
  `argMin(x, (start_time, span_id))`. El desempate va **dentro** de la clave, y es el
  mismo criterio del `ROW_NUMBER() OVER (ORDER BY start_time, span_id)` de SQLite.
* Siete `ORDER BY` sin desempate, en los dos almacenes: el árbol de una traza, la
  repetición de ejemplo, el orden de los hallazgos, los prompts observados y la lista de
  proyectos.

Y para que no haya una quinta vez, dos pruebas que leen el código: una prohíbe `any()` en
el SQL de la nube y otra exige que todo `argMax`/`argMin` lleve tupla. Si algún día hace
falta uno de verdad, que cueste escribir por qué y que se vea en el diff.

**Una corrección sobre la marcha:** al comprobar que las pruebas muerden se vio que el
orden de los modelos de una traza **no** divergía, porque el traductor común ya los
ordenaba en la asignación. Se quitó el arreglo redundante y se dejó escrito allí por qué
ese `sorted` no se puede quitar: ClickHouse los junta en orden de inserción —comprobado
contra el ClickHouse de verdad: devuelve `['zzz', 'aaa']`— y SQLite ordena por dentro.

### D-100 — Los nombres de modelo de los tests y los ejemplos también caducan
Es el mismo problema que ya se arregló en la demo, sobreviviendo en los sitios donde
nadie mira. Un fixture con `gpt-4o-mini` no engaña a ningún usuario, pero un ejemplo de
la documentación sí, y el `README` y la pestaña de Prompts enseñaban `claude-sonnet-4-5`
como si fuera lo que hay que usar hoy.

Se barrió el repositorio entero. Quedan fuera **a propósito** tres cosas:

* **La tabla de precios**, que tiene que seguir teniendo los modelos viejos: un usuario
  que siga en uno de hace dos años tiene derecho a que su coste salga bien.
* **Las pruebas que comprueban que un nombre viejo se resuelve** —`gpt-4o-mini-2024-07-18`
  contra la tarifa de `gpt-4o-mini`, `openai/gpt-4o` con prefijo de gateway—, que no son
  restos: son la funcionalidad. Se conserva además un caso con modelo viejo en las
  pruebas contra el SDK real, y está señalado como deliberado para que nadie lo «limpie».
* **Las entradas antiguas de este documento.** Son un registro fechado de lo que se
  decidió entonces; reescribirlas sería falsificar el histórico.

Cambiar el modelo de un fixture **mueve cifras**, porque las tarifas iban escritas a mano
en el valor esperado. Se actualizaron a las del modelo nuevo —sin aflojar ninguna
comprobación, que siguen siendo exactas— y se verificó rompiendo el motor a propósito que
los tests de doble conteo siguen mordiendo igual de fuerte.

### D-101 — Escribir en caché se cobra, también en OpenAI
Quedaba anotado como incompleto y ya no lo está. OpenAI cobra la escritura de caché a
**1,25x la tarifa de entrada** desde la familia GPT-5.6; Anthropic, **1,25x** la de cinco
minutos y **2x** la de una hora.

Lo interesante es dónde estaba el fallo. El motor de precios ya lo hacía bien: la tabla
tiene `cache_write` y `cache_write_1h` por modelo, y `compute()` los aplica como un tramo
más. **Anthropic estaba completo de punta a punta.** Lo que faltaba era una línea en la
integración de OpenAI: no se leía `prompt_tokens_details.cache_write_tokens`, así que
esos tokens caían en el montón de «entrada normal» y se cobraban a tarifa entera.

El error iba en la dirección que este producto no se puede permitir: **nuestro coste de
OpenAI salía por debajo del real**. Un suelo que no era suelo. Con 8.000 tokens escritos
en caché sobre `gpt-5.6-luna`, la diferencia es de 0,0016 $ a 0,0020 $ por llamada: un
25 % de más sobre esos tokens, todo el día, en cualquier agente que use caché.

Se arregló en los dos caminos —normal y streaming—, porque en un agente de verdad la
mayoría de las llamadas van en streaming y arreglarlo sólo en uno habría dejado el error
justo donde más tráfico hay.


## 2026-09-17 — Los proveedores, contra un modelo local

### D-102 — Un modelo local para ejercitar el camino entero sin pagar nada
Las integraciones ya se probaban contra los SDK reales, pero con el transporte HTTP
falseado: el cuerpo de la respuesta lo escribíamos nosotros (D-098). Eso deja un último
tramo sin tocar, y es un tramo con fallos propios: un cuerpo que llega comprimido, un SSE
troceado como lo trocee el servidor y no como lo trocee un `bytes`, cabeceras, códigos de
estado de verdad, un socket que se cierra a media respuesta.

Ese tramo se puede recorrer gratis. Un servidor de modelos local expone la API de OpenAI;
al cliente real se le pasa ese `base_url` y una clave ficticia, y el SDK publicado habla
por HTTP con un modelo que genera texto. Coste cero, red real, cuerpo ajeno.

**Ollama y no LM Studio**, por cuatro razones del mismo tipo —esto tiene que arrancar sin
que nadie toque una ventana—: es un servicio y no una aplicación, se maneja entero por
línea de comandos (así que los pasos se copian, se pegan y caben en un CI), carga el
modelo cuando llega la petición en vez de exigir un paso previo que se olvida, y un
modelo se pide por un nombre que es el mismo en cualquier máquina. Nada del código está
atado a Ollama: `LAPLACE_LOCAL_BASE_URL` apunta a donde haga falta, y con LM Studio
funciona igual cambiando el puerto.

Tres decisiones de diseño que no son obvias:

* **El modelo se descubre, no se escribe.** Las pruebas preguntan `GET /v1/models` y
  eligen. Un nombre de modelo en el código de un test es exactamente lo que caducó en los
  fixtures y en la demo (D-100), y aquí caducaría peor, porque dependería de qué se
  descargó cada uno.
* **Las capacidades se sondean, no se suponen.** Que el servidor mande `usage` al final
  de un stream depende de su versión. Se comprueba con una generación de un token y, si no
  lo manda, se salta **sólo** la prueba que lo necesita, con un motivo que dice qué
  actualizar. Suponerlo por número de versión habría dejado una prueba midiendo otra cosa
  sin avisar.
* **Las comparaciones van contra el objeto que devuelve el SDK**, nunca contra números
  escritos a mano: `span.llm.usage.output_tokens == respuesta.usage.completion_tokens`.
  Así la prueba vale con cualquier modelo y cualquier longitud de respuesta, y si un campo
  se renombra, nuestro lado se queda a `None` y muerde.

Y una cosa que se valida por fin contra una respuesta que no hemos escrito nosotros: **un
modelo sin tarifa no cuesta cero, cuesta «no lo sabemos»**. Un modelo local no está en la
tabla de precios y nunca lo estará, así que pasa por el mismo camino que cualquier modelo
nuevo de OpenAI el día que sale. Hasta ahora esa regla —la primera del motor de precios—
sólo se había comprobado con cuerpos propios.

### D-103 — Lo simulado, en un fichero aparte y con el alcance escrito en la cabecera
Lo que un modelo local no puede dar es caché: no sirve tokens desde caché ni los reporta.
Y la caché es el tramo más delicado del cálculo —dos proveedores con dos criterios
distintos, escritura por encima de la entrada, reparto 5 min / 1 h— y el que ya se
equivocó una vez en la dirección peligrosa (D-101). Hay que ejercitarlo, y para eso hay
que falsear la respuesta.

La decisión es **dónde vive eso**. Va en `test_modelo_local_simulado.py`, con «simulado»
en el nombre del fichero y en el de cada prueba, y no como un caso más entre los reales.
No es pulcritud: un fichero mezclado es exactamente cómo se acaba citando «el camino de
la caché está probado contra un proveedor real» sin que sea verdad.

La simulación aporta tres cosas que no estaban:

* **Conformidad de forma contra los modelos de los propios SDK.** Los bloques de uso
  simulados se validan contra `openai.types.CompletionUsage` y `anthropic.types.Usage`, y
  se exige además que **cada clave que usamos sea un campo declarado** por ellos. Lo
  segundo es lo que importa: los dos SDK llevan `extra="allow"`, así que un `cached_token`
  en singular pasa la validación sin protestar, se lee como `None` en la integración y el
  coste sale por debajo del real. Es el fallo de D-101 otra vez, y ahora hay un guardia.
  Con su prueba de la prueba, porque una comprobación que puede pasar por vacía no es una
  comprobación.
* **Los mismos tokens por los dos proveedores.** Los dos bloques describen la *misma*
  llamada contada como la cuenta cada uno, y se exige que el contrato los normalice a los
  mismos números. D-050 se comprobaba lado a lado contra cifras escritas a mano; esto
  compara los dos lados entre sí, que es donde se vería un error de criterio.
* **Inyección sobre una llamada local de verdad.** Los contadores de caché se meten en la
  respuesta del servidor local antes de que el SDK la parsee, así que el camino entero
  —red, parseo, acumulación, span, ingesta, precios— corre con esos campos presentes. Se
  inyecta en el transporte y no con un proxy aparte a propósito: así se lee en el código
  que el bloque lo ponemos nosotros, en vez de esconderlo detrás de un salto de red que lo
  hiciera parecer del servidor.

**Y lo que no se ha hecho, a propósito: un adaptador que traduzca la API de Anthropic a
la de Ollama.** Se puede escribir y tentaba, porque dejaría al cliente de `anthropic`
hablando con un modelo local. Pero entonces la forma de la respuesta vuelve a ser la que
*nosotros* creemos, que es exactamente el problema que D-098 vino a resolver. Anthropic
se queda con el transporte falso y con las pruebas vivas, y eso queda escrito como hueco
en lugar de taparse con algo que parece cobertura y no lo es.

### D-104 — El alcance, escrito tres veces, porque es lo que se malinterpreta
El riesgo de todo lo anterior no es técnico. Es que alguien lea «tests contra proveedor
real en verde» y entienda «el modelo de coste está validado contra facturación». No lo
está, y no puede estarlo por este camino:

1. **Un modelo local no factura.** No hay factura contra la que cuadrar los tokens que
   guardamos. Lo único que se comprueba es que el span dice lo mismo que *reportó el
   servidor*.
2. **No hay caché real** en ninguna parte, ni en las pruebas reales ni en las simuladas.
3. **El tokenizador local cuenta distinto**, con su propio vocabulario, así que de ahí no
   sale ninguna cifra en dólares que signifique nada. Por eso ninguna de esas pruebas
   comprueba un importe: sólo de dónde sale cada número y cómo queda marcado.
4. **Anthropic no se cubre** por este camino.

Eso está escrito en tres sitios y los tres hacen falta: en `STATUS.md`, que es lo que se
lee para saber dónde está el producto; en la cabecera de los dos ficheros de pruebas, que
es lo que se lee cuando se van a citar; y en `docs/tests-con-modelo-local.md`, antes de
los pasos de instalación, para que nadie monte el entorno creyendo que va a comprobar algo
que no va a comprobar.

Las pruebas que esperan una clave se han dejado **intactas y funcionando**. Son las únicas
que pueden cerrar el agujero, y el día que haya claves siguen donde estaban.

## 2026-09-18 — Una corrección, y el agente mediocre

### D-105 — Ollama sí tiene caché: corrige D-103 y D-104
D-103 y D-104 dicen que un modelo local «no sirve tokens desde caché ni los reporta» y
que «no hay caché real en ninguna parte». **Es falso**, y se escribió sin comprobarlo.
Lo destapó la primera traza del agente de ejemplo, que llegó con `cached_input_tokens`
distinto de cero. Contra Ollama 0.34.1, sin Laplace de por medio: dos llamadas con el
mismo prompt de sistema largo reportan 3 y después 1.813 tokens de caché sobre 1.819, en
`prompt_tokens_details.cached_tokens`, con la forma exacta de OpenAI y también en
streaming.

Lo cierto es más estrecho que las dos cosas: **hay lecturas de caché reales y no hay
escrituras.** Ollama reutiliza el prefijo de la caché de claves y valores y lo dice;
nunca reporta `cache_write_tokens` ni nada parecido al reparto 5 min / 1 h. Y cachea con
sus reglas —cualquier prefijo repetido, incluso de tres tokens—, no con las de OpenAI,
que empieza en 1.024 y va por bloques.

Qué cambia: la lectura de caché pasa a probarse de verdad
(`test_local_la_cache_de_prefijo_llega_al_span`), y el fichero simulado se queda con lo
que de verdad no se puede obtener: escrituras, la duración de Anthropic y su forma. La
separación entre simulado y real se mantiene; lo que estaba mal era dónde caía la línea.
Las entradas D-103 y D-104 no se reescriben, porque son el registro de lo que se decidió
entonces; los textos vivos —STATUS, la guía, el README y las cabeceras de las pruebas—
sí se han corregido.

La lección es la misma que la del resto de esta tanda, aplicada a nosotros: una
afirmación sobre lo que *no* hace un sistema también hay que comprobarla, y es la que
más fácil se escribe sin mirar.

## 2026-09-21 — Lo que el agente mediocre destapó

Seis arreglos que salen de mirar el producto con tráfico real delante. Cinco de los seis
son el mismo tipo de error: una cifra que se calla o que miente por redondeo conceptual,
no por un fallo de programación.

### D-106 — El sitio de un paso es el camino de llamada, no el nombre de la función
La cobertura dejó de avisar de un paso partido en cuanto hubo dos agentes en el mismo
proyecto. Uno metía la fecha con segundos en sus instrucciones —52 identidades en 52
ejecuciones, ratio 1,00— y el otro no —1 en 53—. Como los pasos se agrupaban por el
nombre de la función y los dos tenían una `resumir_para_crm`, el ratio conjunto salía
0,50, por debajo del umbral de 0,80, y el producto se callaba.

Bajar el umbral sólo habría movido el fallo de sitio. El problema era mezclar dos
poblaciones bajo un mismo nombre. La mitad «desde dónde» de la identidad de un paso pasa
a ser el **camino** de pasos abiertos —`atender_ticket > resumir_para_crm`—, que el SDK
lleva en una pila de `ContextVar` y viaja en `laplace.step.site`. La etiqueta sigue
siendo el nombre a secas, porque es lo que se lee en pantalla: el camino agrupa, no
decora.

Al auditar el resto del producto apareció un segundo sitio con el mismo fallo: la
atribución de picos del Panel también agrupaba por nombre, así que el pico de un agente
podía atribuirse al paso homónimo del otro. Mismo arreglo. Los demás agrupamientos
—repeticiones, uso por modelo, prompts— ya iban por clave de paso.

### D-107 — «No lo sabemos» no es «cero», y ahora hay un guardia que lo impide
Tres pantallas distintas convertían un coste desconocido en un cero: el hallazgo de
repetición decía «no gasta tokens de más» sobre 104 llamadas reales, el Panel enseñaba
«Gasto total: 0 $» con el 100 % de las llamadas sin tarifa, y el inicio ponía un «$0»
enorme con el aviso debajo —el patrón que D-073 prohibió para la proyección—.

Tres parches habrían dejado abierta la cuarta puerta, así que se hizo un guardia, al
estilo del que prohíbe `any()` en el SQL: un test recorre los modelos de la API y exige
que **toda cifra en dólares venga acompañada** de algo que diga si se puede afirmar. Al
escribirlo encontró tres sitios más que nadie había mirado: la serie del Panel, los picos
y el resumen de Evaluaciones. Es decir, el fallo iba por la sexta vez, no por la tercera.

La decisión de si hay dinero que afirmar vive ahora en un solo módulo, `dinero.py`, y la
usan el inicio y el Panel. Cuando no hay ni una tarifa: el inicio enseña el motivo en vez
del número —y debajo tokens, trazas y latencia, que sí están medidos—, las métricas de
dinero del Panel valen `None` con su porqué, y no se enseñan picos, porque un pico se
define por dinero.

### D-108 — Las reglas de dinero tienen que funcionar sin tarifa
Con modelos locales, dos de las tres reglas no podían disparar nunca: las dos necesitan
la tabla de precios para calcular el ahorro. Eso deja sin producto a cualquiera que use
Ollama, que es uno de los dos públicos de esto. Y el agente mediocre movía 8,2 veces más
tokens que el sano sin que Laplace dijera una palabra.

Las reglas pasan a detectar sobre lo que **siempre** se mide —tokens y tiempo— y el
dinero aparece sólo cuando existe tarifa:

* **Modelo caro para un paso corto**, sin tarifa: se mide en tiempo. «Este paso responde
  4 tokens de media y usa el modelo que en tu propio tráfico tarda 4,6 veces más que
  otro que ya usas». La alternativa sale del tráfico del usuario, no de una lista
  nuestra: proponer un modelo que no ha probado sería inventar.
* **Contexto fijo**: el desperdicio se expresa en tokens reenviados, y el dinero sólo se
  añade si hay precio.
* El orden de los hallazgos pasa a ser dinero → tokens → tiempo, porque sin tarifa el
  primero no ordena nada.

De paso, un fallo que sólo se ve con datos reales: el suelo de tokens de entrada de un
paso se calculaba con `MIN(input_tokens)` sobre todas las llamadas, y una sola caída del
proveedor —una llamada con 0 tokens— lo dejaba en cero y apagaba la regla del contexto
fijo para ese paso en toda la ventana. Ahora el mínimo sólo mira llamadas que
respondieron.

**Una corrección sobre la marcha, con datos reales.** La primera versión comparaba
**medias** de duración por llamada. En la tanda de verificación el portátil se suspendió
a mitad, dejó dos spans de dos horas y nueve minutos, y la media de un paso inocente se
disparó a 258 segundos por llamada: la regla lo señaló. Se cambió a **mediana**, que no
se mueve por un valor extremo, y el falso positivo desapareció sobre los mismos datos.
Es el tipo de fallo que no se ve con datos sembrados, porque nadie siembra un portátil
que se duerme.

Y un resultado honesto que conviene dejar escrito: con la mediana, el modelo grande
tarda un 26 % más que el pequeño para una respuesta de dos tokens en esta máquina, por
debajo del umbral de 1,8x. Así que sobre modelos locales esta regla **se calla**, y hace
bien: sin tarifa, lo único que se podía afirmar era el tiempo, y el tiempo aquí apenas
cambia. Con precios reales, la regla de siempre sigue funcionando igual.

### D-109 — La regla de bucles, que era el diferenciador y no existía
Lo que había era repetición **exacta**. Un bucle de verdad casi nunca repite exacto:
lleva un contador de intentos, un número de página, una hora. El agente de ejemplo daba
seis vueltas por ticket —312 llamadas al modelo en media hora, ninguna útil— y el
producto no decía nada, porque para `dedup_hash` eran seis llamadas distintas.

Un bucle atascado se define con dos señales, y hacen falta las dos:

1. **Las entradas se parecen salvo en los números.** Un `loop_hash` nuevo, calculado
   como el de deduplicación pero sustituyendo cada tirada de dígitos por `#`.
2. **Las salidas casi no varían.** Muchas vueltas con una o dos salidas distintas es la
   definición medible de «no avanza».

La segunda no estaba en el diseño inicial y la impuso un contraejemplo que se escribió
para probar la regla: un agente que procesa seis pedidos distintos hace seis llamadas que
sólo se diferencian en un número, y eso es trabajo legítimo, no un bucle atascado. Por
eso el hash de la salida **no** borra los números: ahí los números son el avance. El
contraejemplo se queda como test.

### D-110 — Tres textos que decían algo falso
Salieron de leer la pantalla con datos reales, no de leer el código: una errata que
duplicaba una palabra en el titular de cobertura; el consejo de «añade el precio de la
página oficial» para modelos locales, que no tienen página ni precio ni nadie que cobre;
y la señal de tokens del proveedor explicando como estimaciones lo que eran **llamadas
que fallaron**, donde no hubo nada que estimar porque no hubo respuesta.

El tercero es el que importa: una explicación que manda a mirar donde no es cuesta más
que no explicar nada.

## 2026-09-22 — La nube, ejecutada; y las medias que quedaban

### D-111 — Leer de caché también se cobra, y la regla no lo miraba
La regla del contexto fijo, ya rediseñada en D-108, seguía contestando media pregunta:
cuánto del prefijo **no** se está cacheando. La otra mitad es que leer de caché no es
gratis —OpenAI cobra la lectura entre el 10 % y el 50 % de la entrada según el modelo,
Anthropic el 10 %—, así que un prefijo de tres mil tokens bien cacheado en diez mil
llamadas sigue siendo una factura, y el producto se callaba porque «ya usa caché».

Ahora el hallazgo sale también en ese caso, y dice otra cosa: no «actívala», que sería
proponer lo que ya se hace, sino «la caché ya está haciendo su trabajo, pero leerla
también se cobra: son X $, el N % de lo que gastas; eso no baja cacheando mejor, baja
mandando menos». El umbral para hablar es que esas lecturas pesen al menos un 5 % del
gasto del proyecto.

Y una cuenta que hay que no equivocar: cuando la caché ya funciona, el dinero del
hallazgo es **sólo** el de las lecturas. Apuntar además el ahorro de cachear sería
prometer dinero por hacer lo que ya se hace.

El test que decía «a quien ya usa la caché no se le recomienda activarla» se conserva con
ese nombre y esa promesa —sigue comprobando que no se le propone cachear— y gana la
comprobación nueva. Su cifra esperada se saca ahora del propio almacén y no de una cuenta
a mano, para que no envejezca con el tamaño del fixture.

### D-112 — Las medias que quedaban, y la nube ejecutada de verdad
Cambiar a mediana la comparación de duraciones (D-108) dejaba una pregunta abierta: si
hizo falta en una regla, probablemente hiciera falta en más. Auditado el motor entero,
quedaba un caso: las dos reglas del modelo caro decidían con la **media** de tokens de
salida. Una generación desbocada —un modelo que se pone a repetir hasta agotar
`max_tokens`— mueve esa media igual que un span de dos horas movía la otra, y con ella la
decisión. Las dos pasan a decidir por mediana; las medias se siguen enseñando, pero para
leerlas, no para decidir.

Queda uno a propósito y anotado: la duración por ejecución del Panel es una media, y
sufriría lo mismo. Ahí la mediana exige percentiles por tramo sobre trazas, no sobre
spans, y es un cambio de otra talla; el Panel además enseña esa cifra con su periodo al
lado y no decide nada con ella.

**Y lo que no puede repetirse:** esta tanda tocó el SQL de los dos almacenes —columna
nueva, consulta de bucles, agrupación por camino— y se entregó con 55 pruebas saltadas
porque ClickHouse no estaba levantado. Es la tercera vez, y las dos anteriores salieron
fallos reales. Levantado, la suite pasa entera en 2 minutos y medio, no en trece: lo que
tardaba eran los tiempos de espera de conexión contra un ClickHouse que no existía.

Dos pruebas nuevas cierran ese agujero por donde se coló:

* **La migración, sobre una base con datos dentro.** `CREATE TABLE IF NOT EXISTS` no toca
  una tabla que ya existe, así que sobre una base vacía el `ALTER TABLE` no se ejercita
  nunca y la prueba fácil pasa siempre. La nueva quita las tres columnas de esta tanda,
  mete filas como las metía la versión anterior, pasa la migración por encima y exige tres
  cosas: que el `ALTER` funcione con datos, que las filas viejas se sigan leyendo con las
  columnas nuevas vacías, y que las consultas nuevas funcionen mezclando filas viejas y
  nuevas. Comprobada quitando el `ALTER` del esquema: se pone en rojo.
* **La paridad de la consulta de bucles**, con las dos trazas dando exactamente las mismas
  vueltas, que es el tráfico empatado donde una elección arbitraria se separa (D-099). Y
  la agrupación por camino, que también se escribió dos veces.

### D-113 — Un hallazgo que el motor encuentra tiene que saber explicarse
La regla de bucles entró en D-109 como «el diferenciador del producto desde el
principio», se añadió a `detect()` y **nunca se añadió a `detail()`**. Cuatro tandas
después, la tarjeta de 0,60 $ del proyecto de demo —la segunda que más dinero devolvía—
llevaba a una página que decía «ese problema ya no aparece. O lo has arreglado, o ha
dejado de darse. **Enhorabuena** en cualquiera de los dos casos». El producto felicitaba
al usuario por su hallazgo más caro, y los cuatro bucles del proyecto hacían lo mismo.

Lo que falló no fue la rama que faltaba: fue que **nada la exigía**. `detect()` y
`detail()` son dos puertas del mismo catálogo y no había ningún sitio donde estuviera
escrito que hay que cruzar las dos, así que una regla nueva se podía entregar entera por
la mitad con la suite de 333 pruebas en verde.

Ahora `detail()` despacha por tabla y `DETAILED_KINDS` **se deriva de esa tabla**, nunca
se escribe a mano: una lista a mano se queda desfasada afirmando que cubre algo que no
cubre. Dos redes, y hacen falta las dos:

* Sobre tráfico que produce los cuatro tipos, cada hallazgo tiene que tener ficha. Con
  una aserción previa que exige que el tráfico produzca los cuatro: sin ella, el día que
  la fixture deje de generar bucles la prueba volvería a pasar sin mirar el que falla,
  que es exactamente cómo sobrevivió el fallo original.
* Y una estructural, que no depende de que nadie se acuerde de sembrar tráfico del tipo
  nuevo. Es el criterio de D-097 con las rutas: una regla nueva nace sin ficha y la suite
  lo dice el mismo día.

La ficha del bucle explica además por qué se detecta aparte de una repetición: lo que
delata a un bucle no es la entrada —nunca hay dos iguales, llevan el contador dentro—
sino que la salida no cambia.

### D-114 — Un motivo no basta con que exista: tiene que ser cierto
El guardia de D-107 recorre los modelos de la API y exige que toda cifra en dólares venga
con algo que diga si se puede afirmar. Comprueba que **haya** un motivo. No comprueba que
el motivo **diga la verdad**, y por ese hueco se coló lo siguiente.

La regla del modelo caro mandaba por la misma puerta dos situaciones opuestas: que el
modelo no esté en la tabla de precios —un hueco nuestro— y que esté pero ya sea el más
barato que conocemos, que es una respuesta. Escribía la primera frase para las dos, así
que la pantalla afirmaba «gpt-5.6-luna no está en la tabla de precios» de un modelo cuyo
coste pinta en las otras diez pantallas, con `unknown_cost_spans` a 0 en ese mismo
inicio. Quien lea eso deja de creerse la tabla entera.

`dinero.AFIRMACIONES_DE_SIN_TARIFA` es la lista cerrada de las formas en que el producto
dice no tener tarifa, y un barrido sobre tráfico donde **todos** los modelos tienen
precio exige que ninguna aparezca. Cerrada a propósito: una forma nueva de decirlo se
añade ahí y se ve en el diff.

Y al arreglarlo apareció la tercera cara. La ficha de este hallazgo estaba escrita para el
camino del dinero, así que por el camino del tiempo la página decía «Cambia el modelo de
ese paso a **None**» y afirmaba una alternativa más barata que no existe. **Un tipo de
hallazgo con dos caminos son dos fichas**, y la red lo exige ahora: ninguna ficha puede
enseñar un hueco donde va un dato.

### D-115 — Lo que D-106 dejó detrás: `step_key` leído con su significado anterior
D-106 metió el **camino de llamada** dentro de `step_key` para que dos agentes con una
función homónima no mezclaran sus poblaciones. Fue correcto y sigue siéndolo. Lo que no
se hizo fue repasar quién consumía esa clave dando por buena la definición vieja, y dos
pantallas se quedaron afirmando lo que había dejado de ser cierto:

* **Prompts** agrupaba las variantes por etiqueta, así que un prompt que no había cambiado
  nunca salía como tres versiones porque se llamaba desde tres sitios: «redactar · 3
  juegos de instrucciones» con las tres filas enseñando el mismo texto carácter por
  carácter. La pestaña que existe para fechar cambios de prompt afirmaba uno que no
  ocurrió. Y la guarda de D-093 —«más de ocho variantes es una plantilla con datos
  dentro»— se podía disparar por tener nueve llamantes, dejando inservible el único sitio
  del producto donde se nombra la huella partida.
* **El inicio** enseñaba dos tarjetas con el título idéntico y cifras distintas, porque el
  título de un bucle llevaba sólo el nombre de la función. De diez cosas que arreglar,
  cuatro se leían como un duplicado del producto. Es peor que parecerlo: el usuario
  arregla una, vuelve, ve la otra con el mismo texto y concluye que no nos hemos enterado.

El barrido de todos los consumidores de `step_key` dio **tres** sitios con la suposición
vieja —los dos de arriba y el docstring de `ObservedPrompt`— y uno que ya estaba bien:
`coverage` agrupa por camino desde D-106.

La raíz no era ninguno de los tres: era que **no había un sitio que supiera cómo se llama
un paso**, así que cada pantalla se lo inventaba. `pasos.py` lo es ahora.
`disambiguate()` decide **cuándo** hace falta decir de dónde viene un paso;
`nombre_de_paso()` decide **cómo** se escribe. Dos sitios sabiendo escribir lo mismo era
la enfermedad, un nivel por encima de los dos síntomas.

Tres cosas que salieron al tirar del hilo:

* A los bucles **nunca** se les aplicó `disambiguate()`. A repeticiones y a uso de modelo,
  sí; la regla nueva volvió a entrar por media puerta, igual que en D-113.
* Donde sí se aplicaba, iba cojo: `RepeatedGroup` no llevaba el camino, así que
  `getattr(fila, "site")` devolvía siempre vacío y caía al peor camino —el principio del
  prompt— que su propio docstring desaconseja.
* Y ese peor camino no estaba recortado: el título de un hallazgo era el nombre de la
  función más ochenta caracteres de prompt entre comillas dentro de comillas, dos de las
  tres líneas de la tarjeta.

Dos llamantes distintos **son** dos pasos, que es justo lo que D-106 consiguió distinguir.
Lo que no puede pasar es que uno solo declare versiones que no tuvo, ni que dos se lean
como el mismo.

### D-116 — Un test que falla por el reloj es peor que no tenerlo
Los tramos del panel se alinean al reloj. El test de paridad sembraba el pico a «ahora
menos diez horas» con 48 minutos de duración, así que entre las 09:00 y las 10:00 UTC el
pico cruza medianoche, se parte en dos tramos y salen dos picos donde se espera uno. Un
13 % de las ejecuciones en rojo sin que nada esté mal.

Lo revelador es que el arreglo ya existía: `_proyecto_con_pico` ancla el pico al principio
de su tramo y lleva escrito por qué. El test de paridad no lo usaba, tenía una **copia
inline** de la siembra, y la copia se quedó sin el anclaje. Ahora comparte la pieza.

La regla: un rojo que depende del reloj enseña a no mirar el rojo, y en este proyecto el
rojo de la nube ya se ha ignorado tres veces (D-112). Cualquier prueba que coloque tráfico
en un instante relativo tiene que anclarlo al tramo, no al «ahora».

### D-117 — La quinta cara del doble conteo: el bucle contra el modelo caro
Salió al ir a arreglar la presentación del héroe con el 100 % evitable: antes de escribir
«esto pasa en agentes pequeños» había que comprobar si el 100 % era verdad. No lo era. Los
hallazgos del proyecto de demo sumaban 1,33 $ sobre un gasto de 0,75 $, y el
`min(suma, gasto)` de `overview()` lo recortaba a exactamente el 100 %, que la pantalla
enseña como una buena noticia.

El descuento que lo impide existe desde D-061 y se alimenta **sólo** de las repeticiones
exactas. Cuando la regla de bucles entró en D-109 nadie la enchufó, así que la del modelo
caro volvía a reclamar la diferencia de tarifa sobre las vueltas que el bucle ya daba por
eliminadas. Es la quinta forma que encuentra este proyecto de contar dos veces el mismo
dinero, y la tercera cosa que la regla de bucles se dejó a medias al entrar.

La red sigue la regla de STATUS de no comprobar con un tope contra el gasto —eso deja
pasar el error mientras quepa dentro—: exige que **el tope no llegue a morder** y que
`avoidable_ratio` no sea 1.0. En rojo decía la cifra exacta: 0,073944 prometidos sobre
0,063000 gastados, el 117 % de la factura.

Un bucle y una repetición exacta del mismo paso no se pisan entre sí —la consulta de
bucles exige entradas distintas y la de repetición la misma—, así que sumar los dos en el
descuento no descuenta de más. Y se pasan en `detect()` **y** en `detail()`: en uno solo,
la ficha diría una cifra distinta de su tarjeta.

**Y el `min(suma, gasto)` se queda**, pero ahora se sabe lo que es: un cinturón, no un
cálculo. Que llegue a morder significa que dos reglas se solapan, y eso es un fallo, no
un caso. La prueba nueva es la que lo dice.

### D-118 — El héroe cuando el reparto no reparte, y la cifra que explica el hallazgo
Dos cosas de la misma familia: enseñar el número con el que de verdad se decidió.

**El héroe.** Con el doble conteo arreglado, el proyecto de demo se queda en el 93 %
evitable, y ahí la pareja «X → Y» con su barra sigue sin decir lo que parece: una barra
con un lado invisible no es una barra, y en letra de 54 px la segunda cifra promete que
puedes dejar de pagar casi todo tu agente. Por encima del 90 %, el inicio enseña el gasto
y explica lo que pasa: la cifra está medida y no se esconde, pero no se presenta como una
promesa, porque lo que suele haber detrás es un agente pequeño donde dos o tres pasos son
la factura entera. El aviso de `savings_needs_caution` —que salta al 60 %— se calla
entonces: repetirlo en la misma pantalla no lo hace más creíble.

**La cifra que justifica.** El repaso de medias contra medianas que pedía D-112 da que
ninguna regla decide ya con una media. Pero tres frases explicaban el hallazgo con la
media mientras la decisión usaba la mediana, y eso se nota justo en el caso para el que se
eligió la mediana: once llamadas de 6 tokens y una de 3.000 dejan la mediana en 6 y la
media en 256, así que la tarjeta decía «responde con 256 tokens de media, que es una
respuesta muy breve». La frase que sostiene el hallazgo lo contradecía, y quien quisiera
comprobarnos no podía. `_salida_tipica()` es el único sitio que responde «cuánto contesta
este paso en una llamada normal», y lo usan la decisión y las tres frases. La media se
queda en modo avanzado al lado de la mediana: verlas juntas es lo que enseña la cola larga.

**Lo que queda anotado y sin tocar**, porque cambia qué modelo se recomienda y eso es una
decisión de producto: `_modelo_mas_rapido()` promedia las medianas de varias filas del
mismo modelo **sin ponderar por llamadas**, así que un paso de poco tráfico pesa igual que
uno de mucho al elegir la alternativa. Es una media dentro de un camino de decisión.

### D-119 — Un bucle visto desde dos alturas del árbol es un problema, no dos
Un agente decorado envuelve cada paso en un span propio, así que seis vueltas producen
dos grupos de bucle: el del envoltorio —que no gasta tokens y sólo puede hablar de
tiempo— y el de la llamada al modelo que lleva dentro, que sí tiene dinero. El inicio los
enseñaba como dos tarjetas; después de D-115 con títulos distintos, pero contando lo
mismo. De diez cosas que arreglar, cuatro eran dos.

**Que sean el mismo se sabe por los datos, no por el parecido.** El camino de llamada del
span de modelo termina en el paso que lo envuelve —eso lo escribe el SDK, no lo deducimos
nosotros— y además los dos tienen que cubrir las mismas trazas con las mismas vueltas. Si
cualquiera de las dos cosas falla, son bucles distintos y se quedan los dos.

Se queda el de dentro porque es el que **puede ponerle precio**: «este bucle te cuesta
0,60 $» acciona, y «te cuesta 2,4 s» acciona menos. Se pierden los pocos milisegundos del
envoltorio alrededor de la llamada, que es el lado bueno por el que equivocarse. Un bucle
de herramientas **sin** llamada al modelo dentro no tiene quien lo sustituya y sigue
saliendo con su tiempo: es la promesa de no inventar dinero donde no lo hay.

La guarda de «mismas trazas y mismas vueltas» no es cosmética, y lo demostró la segunda
mutación: quitarla dejaba la suite en verde, o sea que no tenía quien la probara. Sin
ella bastaría con que un paso llame alguna vez al modelo para que su bucle desapareciera
detrás del de dentro, y con él las trazas en las que da vueltas **sin** llegar a llamarlo.
Es el patrón que D-078 prohibió en el panel: rellenar con algo que correlaciona.

### D-120 — Un solo sitio que sabe escribir un número
En la cabecera de una traza convivían «120.255 / 108» —tokens, punto de millar—,
«$0.007181» —dinero, punto decimal— y «12,8 pasos por ejecución» —coma decimal—. Tres
lecturas del mismo carácter en la misma pantalla, en un producto cuyo argumento entero es
que una cifra se enseña con lo que haga falta para leerla bien.

No era un descuido en un sitio. El guardia que se escribió para encontrarlos dio **cuatro**
formateadores de dinero: `insights`, `alerts`, `prompts` y la web. El de `alerts` llevaba
en su docstring «el mismo criterio de decimales que la interfaz, para que no se
contradigan»: la intención estaba, pero era una copia, y las copias derivan. Es la misma
enfermedad que `pasos.py` curó para los nombres de paso (D-115), con otra cara.

`cifras.py` es el único que sabe hacerlo. **Punto para los millares, coma para los
decimales**, que es la española, y el símbolo delante. Y `dinero_exacto()` aparte, para el
texto que enseña la cuenta: ahí redondear a cuatro decimales rompe la suma, y este
producto se apoya en que el lector pueda echarla a mano.

Dos guardias, y hacen falta los dos porque el código vive en dos runtimes: uno lee los
módulos que redactan texto y prohíbe el patrón que creó las cuatro copias; otro barre
`apps/web` y prohíbe `toFixed` con decimales, que escribe el punto inglés sea cual sea el
idioma de la página. `toFixed(0)` se deja: no emite separador, y un guardia que salta
donde no hay nada acaba silenciado.

**Divergencia conocida y aceptada:** con importes de 100 $ o más, Python redondea el medio
al par y JavaScript hacia arriba, así que un 1.234,50 $ exacto se escribe «$1.234» en una
frase del backend y «$1.235» en una tarjeta. Sólo ocurre en el medio exacto de un dólar.
Queda escrito para que quien lo vea sepa que está mirado y no lo persiga como un fallo.

### D-121 — El punto ciego de D-097 era una fuga entre clientes
D-097 cerró las lecturas: middleware que deniega por defecto, claves atadas a su proyecto,
y una nota al final —«si aparece una escritura que no lleve `project_id` ni en el cuerpo
ni en la URL, el middleware no tiene por dónde acotarla; hoy no existe ninguna, el día que
exista es el punto ciego»—. Existían seis.

Lo que se buscaba era `POST /api/pricing/reload`, que no lleva proyecto y recarga la tabla
de precios de toda la instalación. Lo que apareció al escribir la red estructural —recorrer
las rutas de escritura y exigir que cada una tenga por dónde acotarse— fue otra cosa:

    DELETE /api/prompts/{id} con la clave de otro proyecto → 200 OK

Borrado de los prompts de otro cliente con su histórico entero. Y la vecina, peor:
`GET /api/prompts/{id}` pide `project_id` —así que el middleware la deja pasar si pones el
tuyo— y después buscaba el prompt por su id a secas. Con tu proyecto en la URL y el id de
otro, te llevabas sus prompts con **el texto completo de todas sus versiones**.

No es lo que D-097 cerró, y por eso pasó: aquello iba por las rutas que llevan proyecto, y
esto va por **id opaco**, que es justo por donde el middleware no puede mirar.

El arreglo es el patrón que ya estaba escrito, aplicado donde faltaba: **acotar la
consulta, no comprobar después**. `Identity.scope(None)` da el proyecto de la clave —`None`
sólo para el operador— y los métodos del almacén lo llevan en el `WHERE`. Un id ajeno no
existe: 404, sin decir si existe en otro sitio, que es la regla que `Identity.require` ya
tenía escrita para no convertir la API en un directorio de los clientes. Comprobar después
—leer la fila, mirar de quién es, actuar— habría funcionado igual y se habría podido
olvidar en la ruta siguiente.

De paso: `delete_prompt` borraba versiones y despliegues **antes** que el prompt, así que un
intento fallido dejaba el prompt en pie con el histórico vacío.

Dos cosas que este episodio dice, y valen más que el arreglo:

* **Una nota en un documento no es una red.** D-097 vio el hueco, lo escribió, y el hueco
  se llenó de seis rutas sin que nadie se enterara. El guardia que las recorre existe
  ahora y es lo que faltaba entonces.
* **A `api_evals` le faltaba un import y la suite pasaba entera**, porque ninguna prueba
  ejecutaba el borrado de una anotación por la API. Lo encontró el linter. Una ruta
  arreglada y sin ejecutar está a un refactor de volver a estar rota.

### D-122 — Diez cosas que un cliente habría visto en su primera tarde
Una auditoría mirando el producto como lo mira quien paga —no como lo mira quien lo
escribe— encontró diez defectos, todos en pantalla y ninguno en una aserción. Los que
importan son los tres primeros, porque son **botones que no hacen lo que dicen**:

* **«Ver las trazas afectadas» enseñaba todas las trazas** en los hallazgos de repetición
  y de bucle, que son los que más dinero devuelven. Buscaba por la etiqueta técnica
  «paso», que esas dos reglas no llevan, y salía `q=` vacío. Buscar por texto tampoco
  servía en los demás: el título lleva el llamante, y `consultar_manual` casaba con
  `consultar_manual_cacheado`, así que el agente sano salía como afectado. Ahora cada
  hallazgo lleva su `step_key` y el explorador filtra por **identidad exacta** en los dos
  almacenes, con un aviso visible y un «Quitar».
* **«Ya lo he arreglado, vuelve a medir» sólo recargaba.** Las cifras cuentan la ventana
  entera, así que el arreglo no se veía: el botón prometía justo lo que no hacía, en el
  momento en que el usuario más quiere creernos. Mientras no exista el antes y el después
  de verdad, lo honesto es llevar a las ejecuciones más nuevas de ese paso y decir que un
  arreglo deja de sumar cuando lo viejo sale del rango.
* **No se podía crear un conjunto de casos desde un filtro.** Evaluaciones decía
  «fíltralo en el explorador y vuelve», y el formulario enviaba siempre `{sort:
  "recent"}`. El filtro vive en el explorador, así que el botón de guardar también.

El resto, en corto: el código de ejemplo metía el título entero en Python
(`respuesta = agente → consultar_manual(...)`); la ficha de un bucle decía «las entradas no
son idénticas» y dos líneas después «3 llamadas idénticas más»; una tarjeta con tokens de
más y sin tarifa llevaba la etiqueta «No cuesta dinero»; la ayuda de precios mandaba a
editar un fichero del repo que quien instala con `pip` no tiene —ahora hay
`LAPLACE_PRICES_EXTRA`— y salía dos veces seguidas; `es-ES` no agrupa las cifras de cuatro
dígitos y el backend sí, así que «1360» y «11.314» convivían; el `_seconds` del backend y
el `_money` de evaluaciones seguían escribiendo el punto inglés que D-120 creía cerrado; y
el modo sencillo se llamaba «Diagnóstico», como la pestaña de al lado.

La lección repite la de D-113, y por eso las redes van antes que la entrada: el filtro de
cada hallazgo tiene que encontrar su traza de ejemplo y sólo las de su llamante, y ningún
fragmento de código puede llevar la decoración de un título.

### D-123 — Lo que el usuario decide, en una pantalla y no en variables de entorno
La auditoría de D-122 terminó con una lista de lo que faltaba mirando al que paga, y casi
todo tenía la misma forma: el producto sabía la respuesta y el usuario no tenía por dónde
preguntarla, o sólo podía decirla editando un fichero. Entra entero, con una decisión de
diseño que lo sostiene y cuatro que conviene dejar escritas.

**Una tabla de ajustes, clave → JSON por proyecto**, en los tres almacenes de metadatos.
Estado de cada hallazgo, presupuesto, alertas y tarifas propias (`*` es la instalación).
Ninguno se consulta por nada que no sea su clave, y cada tabla nueva son tres
implementaciones: cinco tablas habrían sido quince métodos para lo mismo.

* **Arreglado no es lo que dice el usuario, es lo que se mide después.** Marcar guarda el
  instante; se compara la ejecución media de antes (siete días) con la de después, y con
  menos de cinco ejecuciones después no se dice nada. Si sigue saliendo igual vuelve a la
  lista como `reaparecido`. Por ejecución y no en totales: con la mitad de tráfico, el
  total baja solo. Lo arreglado y lo ignorado salen del evitable —que es lo que todavía
  se puede hacer— y no alertan.
* **Una tarifa nueva alcanza lo ya guardado.** El coste se calcula en la ingesta (D-005),
  así que poner un precio sólo servía para lo que llegara después. Se recalcula con la
  misma función que usa la ingesta y se reescribe el span: los dos almacenes sustituyen
  por clave, así que no hay doble conteo.
* **Los canales de alerta de la interfaz no piden `LAPLACE_ALERTS_ENABLED`.** Ponerlos ya
  es encenderlas a propósito para ese proyecto. El webhook del entorno sigue exigiéndolo.
  Las URL son secretos: se aceptan y nunca se devuelven enteras.
* **El presupuesto mide los días con la misma función que el inicio.** La primera versión
  contaba desde la primera traza hasta ahora y proyectaba el mes con once horas de datos
  justo debajo de un inicio que decía «todavía no proyectamos». Dos lecturas del mismo
  dato en la misma pantalla es la forma de este proyecto de equivocarse, otra vez.

Mirando la pantalla con esto delante salieron dos fallos que no eran nuevos. Filtrar las
trazas por usuario o por sesión —el modo avanzado ya lo permitía— filtraba **spans**, y
como esos campos los lleva el span raíz, la traza salía en la lista con coste y tokens a
cero. Y el HTML de la interfaz local se servía sin `Cache-Control`: tras actualizar
Laplace, el navegador enseñaba la versión anterior por heurística, sin ningún aviso.

Lo que queda fuera, a propósito: cuentas y roles, y el SDK de TypeScript. No son
funciones de una pantalla, son proyectos, y meterlos aquí habría sido hacerlos mal.

### D-124 — El rigor delante tapaba lo que el rigor protege
En un móvil, la primera pantalla del inicio eran dos avisos —«casi todo es evitable» y
«todavía no proyectamos»— y ni un solo problema. Cada aviso era cierto y estaba bien
argumentado; juntos convertían el producto en un informe que había que leer entero antes
de llegar a lo que se había venido a ver.

No se quita ninguno. Se parte cada uno en **una línea que se lee siempre** y **un porqué
que se despliega**, y la línea conserva lo que no se puede perder: que el total está
incompleto, que es un suelo, que no se proyecta. Lo que se pliega es la argumentación,
no la advertencia.

Con el mismo criterio, en la lista:

* Las tarjetas llevan **una frase** (`lead`) en lugar del resumen de tres o cuatro líneas.
  El título ya dice qué pasa y la cifra cuánto cuesta; el resumen repetía las dos cosas.
  Sigue entero en la ficha y en modo avanzado.
* **Por debajo del céntimo, la tarjeta dice «< $0,01»**, con la cifra exacta en el
  `title` y en la ficha. «$0,000816» no se lee; se descifra. Donde el importe por paso o
  por ejecución es la medida —el árbol, el explorador, el panel— sigue sin redondear.
* Fuera de Prompts la frase «un diff y un rollback los tiene cualquiera; lo que no tiene
  nadie más…» con cifras inventadas: es texto de la web comercial, no de una pantalla
  donde alguien está mirando sus propios datos.

### D-125 — Que se vea como un producto, sin cambiar lo que dice
Lo último de la auditoría era la forma, y casi todo tenía un motivo funcional detrás:

* **Colores en tokens, y tema claro.** Había cuarenta hex sueltos por el CSS —bordes y
  fondos tintados de cada color de estado— y un tema claro habría sido perseguirlos uno a
  uno. Ahora son tokens con nombre (`--amber-bg`, `--rose-line`…); el tema claro redefine
  la paleta y nada más. Sigue al sistema, y en Ajustes se puede fijar uno; se aplica en el
  mismo script que el modo, antes del primer pintado, para que no haya fogonazo.
* **Las fuentes se sirven desde Laplace.** `next/font` las descarga al construir. El modo
  local presumía de no llamar a nadie y pedía dos hojas de estilo a Google cada vez.
* **Título por pestaña y favicon.** Con tres fichas abiertas, tres pestañas «Laplace».
  La traza y la ficha llevan su propio nombre.
* **La barra ya no se parte.** El proyecto va junto al logo como una ruta; las pestañas
  se desplazan, con el borde difuminado para que se note; el doble botón Sencillo /
  Avanzado es un interruptor. En móvil, dos filas y 87 px, antes tres y 125.
* **Tres acciones a la vista.** El inicio enseña los tres problemas que más devuelven y
  despliega el resto; lo que sólo cuesta tiempo va aparte, porque «531 ms» y «$1,21» no
  se comparan. Las etiquetas de la tarjeta son texto con un punto de color: con borde y
  fondo se leían como botones.
* **La lista de trazas dice qué le preguntaron al agente.** Era la misma fila doce veces
  con un hash distinto. `input_preview` sale de la entrada del span raíz: la primera
  cadena con contenido, o el último mensaje del usuario, en una línea.
* **La gráfica empieza en el primer dato.** Con once horas en un rango de siete días, dos
  barras aplastadas contra el borde y veintiséis tramos de nada. Los vacíos del final se
  quedan: «no ha llegado nada desde entonces» sí es información.
* **Evaluaciones vacía es una lista de tres pasos** que se marcan solos, en vez de tres
  párrafos que había que leer para saber por dónde empezar.

El contraste del tema claro se ha comprobado recorriendo los textos de inicio, traza,
ficha (en modo avanzado) y panel: nada por debajo de 3:1 salvo la barra decorativa de la
ruta, que está oculta a los lectores de pantalla. El resto de pantallas no se ha medido.

### D-126 — El SQL de nube de D-122 a D-125, ejecutado
Las cuatro tandas anteriores se entregaron con el camino de la nube escrito y sin
ejecutar, que es exactamente lo que D-112 llama «sin terminar». Con ClickHouse y Postgres
levantados, la suite entera da **389 pasan y 4 saltadas** —las cuatro que necesitan una
clave de proveedor—, y hay un fichero nuevo, `test_ajustes_nube.py`, que siembra los mismos
spans en los dos almacenes y exige que digan lo mismo en todo lo nuevo: reparto por
usuario, filtros por usuario y por paso, la pregunta de cada traza, el recálculo de una
tarifa (que en ClickHouse depende de que reinsertar sustituya y no duplique), la
retención, los ajustes en Postgres y el borrado de un proyecto sin tocar a otro. Se ha
comprobado rompiendo a propósito el filtro por usuario de ClickHouse: la prueba se pone en
rojo.

Encima, un recorrido de punta a punta por la API con el backend en modo nube: tráfico
real por OTLP, marcar un hallazgo, ficha, presupuesto, reparto, alertas, la lista de
trazas y el borrado. Todo contestó lo que tenía que contestar a la primera. Que no hiciera
falta arreglar nada no hace inútil la tanda: hasta hoy eso era una suposición.

### D-127 — Cuentas: las personas entran, los agentes escriben con clave
Hasta aquí la nube sólo sabía de claves, creadas por línea de comandos y pegadas en el
navegador. Servía para que un cliente no leyera a otro y no servía para un equipo: nadie
sabía quién había hecho qué, quitarle el acceso a una persona era rotar la clave de todos,
y la primera pantalla de un producto de pago era «pega aquí tu clave».

**Las cuentas no sustituyen a las claves**: una sesión acaba en la misma `Identity` del
middleware que una clave, con los proyectos de sus organizaciones y su rol en cada uno. Ni
una ruta de datos ha tenido que cambiar. Lo que sí ha cambiado:

* **Toda escritura con sesión dice sobre qué proyecto es.** Una clave tiene un proyecto;
  una persona, varios, y «su primer proyecto» —lo que hacían las rutas por id opaco— es
  la forma de que un lector de A escriba en A porque es miembro de B. El middleware exige
  el proyecto y mira el rol en ése; la interfaz lo manda siempre.
* **La primera cuenta sale de un código que va al log**, no de quien primero llegue a la
  URL. Adopta los proyectos que ya existían: sin dueño serían invisibles para todos.
* **CSRF sin tokens**: toda escritura con cookie trae `X-Laplace`, que un formulario de
  otro sitio no puede poner sin pasar por CORS. Se pide también al entrar, que es lo que
  impide que otro sitio te meta en una cuenta suya.
* **Sin dependencias**: `scrypt` de la biblioteca estándar, un almacén de cuentas único
  para SQLite y Postgres (mismas consultas, otro marcador), y el freno de intentos en
  memoria —su trabajo es hacer inútil probar contraseñas, no llevar un registro; eso lo
  hace la auditoría—.
* **La interfaz no ofrece lo que el rol no permite**, pero no es la protección: el
  backend lo rechaza igual. Ante la duda deja escribir y que diga que no el backend; una
  pantalla sin botones por un fallo de red sería peor.

Tres fallos que sólo salieron ejecutando, y que por eso merecen línea: el admin de una
organización cualquiera no podía crear el primer proyecto de la suya (el nombre nuevo
venía en el cuerpo y el middleware lo rechazaba por no ser todavía de nadie); en Postgres
la clave apunta a `projects` con clave foránea y en SQLite no, así que el almacén fallaba
en uno y no en otro; y «último uso» de las claves se guardaba en una columna que nadie
actualizaba. Los tres tienen prueba.

`keys.py` decía que no debía existir un endpoint que emitiera credenciales porque la
primera tenía que salir de algún sitio. Ahora sale del código de configuración, y crear
claves pide sesión de admin. Queda fuera, a propósito: SSO y verificación de email.

### D-128 — La auditoría de septiembre: nueve costuras, y una red por cada una
Una auditoría de todo el código encontró nueve fallos críticos. Ninguno estaba en lo que
las pruebas recorrían; todos, en lo que quedaba entre dos piezas. Se arreglan juntos y
cada uno deja su prueba en `test_auditoria_p1.py`, escrita como el ataque que era.

* **Fugas entre proyectos.** `/api/alerts` sin `project_id` listaba todos los proyectos de
  la instalación. Y lo que se pide por id —conjuntos, tiradas, anotaciones— no se
  comprobaba contra la identidad: con un id ajeno se leían casos de otro cliente, se
  comparaban sus tiradas o se colgaban veredictos de sus trazas (pisando el suyo, porque
  la anotación se reescribe por traza, fuente y autor). Regla: **lo que se busca por id se
  compara con la identidad, y si no es suyo contesta 404**, como si no existiera.
* **Una clave de proyecto no gobierna el proyecto.** `_check_role` sólo miraba a las
  personas, así que la clave de ingesta —la que vive en el entorno de los agentes, que es
  donde se filtra— podía borrar el proyecto o mandar sus alertas a otro sitio. Lo de admin
  pide ahora una persona con ese rol o la clave de instalación. Anotar y escribir datos,
  no: eso sigue siendo cosa de la clave.
* **La nube reventaba en cuatro rutas.** Postgres y el almacén nulo no aceptaban el
  alcance que las rutas les pasan desde D-121 (`get_prompt(id, proyecto)` y compañía):
  `TypeError` en la ficha de un prompt, al desplegar, al borrar. Las rutas se prueban
  contra SQLite y nadie lo vio. La red es una prueba que compara la firma de cada método
  del protocolo en las tres implementaciones.
* **Un Postgres lento al arrancar dejaba la instalación muerta.** Se instalaba el almacén
  nulo para siempre, y como el nulo no puede verificar claves, todo —la ingesta incluida—
  era 503 hasta reiniciar. Ahora se queda el de Postgres, que se recupera solo, y las
  migraciones, las tarifas y las cuentas se reintentan en segundo plano.
* **El SDK no mandaba la clave** al pedir prompts ni al registrar tiradas. En la nube,
  `get_prompt()` servía en silencio el texto de reserva. Ahora manda las mismas cabeceras
  que el exportador de spans, y espera 5 s y no 30: va en el camino de cada petición.
* **Sin tope al tamaño de lo que entra.** Ni el cuerpo ni el gzip descomprimido tenían
  límite, y el proceso es de todos. `LAPLACE_MAX_BODY_MB` (32 por defecto) se aplica en un
  middleware ASGI que corta antes de que nadie lea, y el gzip se descomprime con tope.
* **SSRF por el webhook de alertas.** Cualquier https valía, y las redirecciones se
  seguían. Ahora el host tiene que ser público al guardar y resolver a direcciones
  públicas al enviar, y una redirección no se sigue. En local sigue valiendo la propia
  máquina. Queda, anotada, la ventana entre resolver y conectar.
* **`X-Forwarded-For` se creía siempre**, y el freno de intentos por IP no frenaba. Sólo
  se cree a los proxies de `LAPLACE_TRUSTED_PROXIES` (IP, rango o nombre), vacío por
  defecto. Se probó poner `web` en compose y **no vale**: levantado el stack, Next
  reenvía la cabecera del navegador tal cual y no añade la IP real, así que un login con
  «X-Forwarded-For: 6.6.6.6» quedaba auditado desde 6.6.6.6. Y sin creerla, todos los
  usuarios llegaban con la IP de `web`: cinco fallos de cualquiera bloqueaban el login de
  todos durante un cuarto de hora (también comprobado; pasaba ya antes de la auditoría).
  La salida es una puerta delante de Next, `entrada` (Caddy, `deploy/Caddyfile`), que
  reescribe la cabecera con la IP de la conexión; `web` deja de publicar su puerto y el
  backend se fía de `web`. Probado con el stack levantado: la cabecera inventada se
  descarta, y cinco fallos desde un cliente lo bloquean a él y no a otro. Aceptar una
  invitación con una cuenta que ya existe prueba una contraseña, y ahora lleva el mismo
  freno que entrar.

La lectura de conjunto es la de siempre en este proyecto: la regla no se cumple porque se
recuerde. La comprobación de rutas nuevas ya existía (D-097); faltaba la misma idea para
la propiedad de los objetos y para la paridad de los almacenes.

### D-129 — La deuda de la auditoría: lo que escala, lo que se reparte y lo que se ve
La otra mitad de la auditoría de septiembre. D-128 cerró lo que se podía explotar; esto
es lo que no rompía nada hoy y rompería con volumen, con dos procesos o con el tiempo.
Cada punto tiene su prueba en `test_auditoria_p2.py`.

**Rendimiento.** Las subconsultas de los filtros de la lista de trazas no llevaban
proyecto, y la clave de ClickHouse empieza por él: cada filtro leía la instalación
entera. Ahora van acotadas, en los dos almacenes. La pantalla de tarifas hacía dos
agregaciones de 90 días **por proyecto** para sacar nombres de modelos: ahora es una
consulta (`unpriced_models`). La lista de tiradas pedía su contexto tirada a tirada:
ahora una vez. El juez hace hasta cuatro trazas a la vez, en orden. Postgres va con pool
(`storage/_pg.py`): antes cada operación abría su conexión, y cada petición con sesión,
dos. Las llamadas a la base que quedaban dentro de handlers `async` salen del bucle.

**Varios procesos.** El bucle de alertas corría en cada worker y en cada réplica, y dos
que evaluaban a la vez avisaban los dos. Ahora cada vuelta pide un candado consultivo de
Postgres y la hace uno. La retención en ClickHouse deja de ser un `DELETE` diario y pasa a
ser un TTL de la tabla, que se pone y se quita según `LAPLACE_RETENTION_DAYS`.

**Lo que se quedaba a medias.** Borrar un proyecto borra primero los metadatos (claves
incluidas, en una transacción) y después las trazas: al revés, un fallo dejaba claves
vivas escribiendo en un proyecto sin trazas. Aceptar una invitación la gasta en la misma
escritura que comprueba que no estaba gastada, y no baja de rol a quien ya era más.
Entrar barre sesiones e invitaciones caducadas. Los textos libres de la API tienen tope.

**El SDK.** Tras un fallo, `get_prompt()` no vuelve a preguntar en diez segundos: sirve
la copia o la reserva sin esperar. Al caducar la copia pregunta un solo hilo.

**Contratos.** El protocolo `SpanStore` no recogía seis métodos que las rutas llaman en
los dos almacenes: la misma clase de hueco que dejó la nube en 500 (D-128). Ahora están, y
una prueba exige que todo método público común a los dos esté en el protocolo con la
misma firma. Y hay integración continua (`.github/workflows/ci.yml`), con ClickHouse y
Postgres de verdad: las pruebas «de nube» dejan de saltarse.

**Despliegue.** El backend corre sin root. ClickHouse y Postgres se publican sólo en
127.0.0.1. `.dockerignore` deja fuera la interfaz exportada, que se colaba en la imagen y
se servía vieja. Toda respuesta lleva `nosniff`, `frame-ancestors 'none'`,
`X-Frame-Options` y `Referrer-Policy`, desde el backend y desde Next; Next deja de decir
que es Next.

**Next 16.** La 14.2 ya no recibe parches y arrastraba avisos que tocan a esta app —el de
*request smuggling* en `rewrites`, que es justo cómo habla la interfaz con el backend—.
Next 16 admite React 18, así que el salto fue pequeño. Dos cosas que sólo salieron
mirando la pantalla: precarga las páginas con `HEAD`, que el servidor de ficheros del
modo local no aceptaba, y su export deja los segmentos en `__next.trazas/__PAGE__.txt`
aunque el navegador los pide como `__next.trazas.__PAGE__.txt`. Las dos se resuelven en
`main.py`.

**Queda, a propósito:** partir `insights.py` y las páginas de más de 600 líneas (es
trabajo de refactor, no de auditoría, y sin cambio de comportamiento que probar); retirar
la clave de API de `localStorage` ahora que hay sesiones (cambia cómo entra quien usa
clave); llevar el freno de intentos a la base para que no se multiplique por procesos;
fijar la IP al conectar con un webhook (DNS rebinding, D-128); y delimitar mejor el
contenido de las trazas en el prompt del juez.

### D-130 — Lo que D-129 dejó anotado, hecho
Los cinco pendientes de la auditoría. Tienen prueba en `test_auditoria_pendientes.py`,
salvo partir `insights`, que la tiene en las pruebas que ya había y que no han cambiado.

* **Webhooks a IP fija.** El nombre se resuelve una vez, se comprueba que todas sus
  direcciones son públicas y se conecta a ésas, con el nombre en SNI y en `Host`. Entre
  comprobar y conectar ya no hay una segunda resolución que pueda llevar a la red
  interna. Una cosa que salió probando: quedarse con la primera dirección no vale, porque
  un nombre con IPv6 e IPv4 puede no escuchar en la primera; se prueban todas las
  comprobadas, en orden, como hacía `urllib`.
* **El freno de intentos, en la base de cuentas.** En memoria, cada worker llevaba su
  cuenta y el tope se multiplicaba por procesos; y reiniciar lo ponía a cero. La tabla
  `login_failures` es de todos, y entrar barre lo que tiene más de un día.
* **El juez distingue sus instrucciones de lo que evalúa.** El contenido de la traza va
  entre `<dato>…</dato>`, el prompt de sistema dice que es dato y no instrucciones, y
  cualquier forma de la etiqueta escrita dentro se neutraliza. El prompt pasa a `v2`: dos
  veredictos de prompts distintos no se comparan.
* **La clave de API fuera del alcance de JavaScript.** Quien entra a la interfaz con clave
  la manda una vez a `POST /api/auth/key`, que la comprueba y la deja en una cookie
  `httpOnly`. Sus escrituras piden la cabecera anti-CSRF, como las de sesión. Los
  navegadores que la tenían en `localStorage` la pasan a la cookie una sola vez y la
  borran de ahí. `Authorization: Bearer` sigue siendo lo de los agentes y las
  herramientas: esto cambia sólo lo que guarda el navegador.
* **`insights` es un paquete**: `modelos` (tipos, umbrales y redacción), una regla por
  módulo (`repeticion`, `bucle`, `modelo_caro`, `contexto_fijo`) y `motor`. Se partió con
  un script que no cambia ni una línea de lógica; el `__init__` reexporta todo, así que
  ninguna importación de fuera cambió. El guardia de `test_cifras` que busca dinero
  formateado a mano mira ahora todos los módulos del paquete: si hubiera seguido
  mirando sólo `__init__`, habría dejado de vigilar las reglas sin fallar.
* **Ninguna página pasa de 500 líneas.** Los componentes que ya existían se movieron a
  ficheros hermanos (`detalle.tsx`, `alertas.tsx`, `listado.tsx`…), también con un
  script que no toca su código. Se comprobó con `tsc --noUnusedLocals` y recorriendo cada
  pantalla en el navegador con datos que pasan por lo movido.

### D-131 — P3: lo menor, que también se equivocaba en silencio
La auditoría original llegaba a P2. Esto es lo menor que se vio por el camino y no entró:
nada permitía leer datos ajenos, pero cada punto era una forma de equivocarse sin que
nada lo dijera. Pruebas en `test_auditoria_p3.py`.

* **`/docs` y `/openapi.json`, sólo en local.** No cuelgan de `/api`, así que el
  middleware no los tocaba, y en la nube enseñaban a cualquiera el mapa de la API.
* **Con varios proyectos hay que decir cuál.** Una persona de una organización con varios
  proyectos que no mandaba `project_id` recibía el primero por orden alfabético: la misma
  petición contestaba de un proyecto u otro según cómo se llamaran. Ahora es un 400 que lo
  dice. Una clave, que tiene un solo proyecto, sigue sin tener que decirlo.
* **El destinatario de las alertas se valida.** Bastaba una `@`: un salto de línea dentro
  de una cabecera de correo es la forma de añadir cabeceras propias, y una coma daba para
  cien destinos. Ahora hay formato, sin saltos de línea y como mucho cinco. Y si queda uno
  malo guardado de antes, el envío lo dice en vez de reventar el ciclo de alertas —esto lo
  encontró la prueba: el error saltaba al construir la cabecera, antes del `try`—.
* **El apunte de «último uso» de las claves no crece sin fin**: se barre al pasar de diez
  mil.
* **Las conexiones SQLite se cierran.** `with sqlite3.connect()` confirma pero no cierra;
  en Windows eso es un fichero abierto hasta que pase el recolector, y desde Python 3.13
  un aviso por conexión.
* **Cambiar de filtros cancela lo que estaba en vuelo.** `useApi` pasa una señal a su
  carga y las lecturas la reciben. Se intentó primero sin tocar las funciones —una señal
  «en el ambiente» que `get` recogía al empezar— y la prueba en el navegador enseñó que no
  llegaba: casi todas las pantallas piden antes los proyectos, y en el navegador nada
  sobrevive a un `await`. Va explícita.
* **El código de configuración se gasta una vez**, bajo cerrojo, antes de crear nada: dos
  peticiones a la vez ya no crean dos administradores. Si la configuración falla después
  —una contraseña corta—, el código se devuelve.

## 2026-09-24 — Revisión de diseño del inicio

### D-132 — Escalas cerradas, contraste AA y el inicio en dos columnas
La identidad sigue siendo la del mock de D-031 (fondo tinta, iris de marca, ámbar para lo
que se tira, verde azulado para lo que se ahorra, IBM Plex), pero sus tokens no bastaban:

* **Contraste.** `--ink-3`, el gris de todo el texto secundario de 12–13 px, daba 3,6:1
  sobre `--panel`, por debajo del 4,5:1 de AA; `--ink-4` se usaba como texto con 2,1:1.
  La paleta se reajusta sin cambiar de tono: `--ink-3` pasa de 5:1 en los dos temas y
  `--ink-4` de 3:1, que queda para separadores y lo decorativo.
* **Escalas.** Había veintiún tamaños de letra (12, 12,5, 13, 13,5, 14, 14,5…) y paddings
  a ojo. Ahora `--fs-*` en pasos enteros, `--sp-*` sobre 4 px y dos radios (`--r-sm` 4 px,
  `--r` 6 px). El CSS existente se pasó a la escala con un script: los medios píxeles
  van al entero de arriba, así que ningún texto encoge.
* **`--dim` no existía** y lo usaba la explicación de «sin dinero».
* **El inicio en dos columnas.** A 1440 px la columna de lectura dejaba un tercio de
  pantalla vacío a la derecha. Ahora el presupuesto, la cobertura y las métricas van en
  un carril que se queda fijo mientras se baja por la lista; en estrecho cae debajo del
  héroe, donde estaba. La cobertura mala sigue arriba del todo, antes que el dinero
  (D-096), y los avisos siguen pegados a la cifra.
* **El h1 era el título más pequeño de la pantalla** (15 px, gris), por debajo del de
  cada tarjeta. Ahora es el título y la ventana va debajo.
* **Tarjetas.** Llevan su puesto y una barra con lo que valen frente a la primera: tres
  importes en ámbar del mismo tamaño no dejaban ver que uno vale treinta veces otro. El
  «Ver cómo arreglarlo» se ve siempre; sólo al pasar el ratón no se sabía que la tarjeta
  llevaba a algún sitio.

El resto de pantallas hereda paleta, escala y radios, pero conserva su maquetación.

## 2026-09-24 — Rediseño visual: cristal y dos temas

### D-133 — Glassmorphism, «Rose Gold & Amanecer» en claro y «Tech Abisal» en oscuro
Sustituye la paleta de D-031/D-132 (no las escalas). Las clases y la maquetación no
cambian: cambia de dónde salen los colores.

* **Nueve variables base por tema** (`--bg-gradient-*`, `--text`, `--muted`, `--glass`,
  `--line`, `--accent-1..3`, `--glow-color`), copiadas tal cual del encargo. El claro va
  por defecto; el oscuro sigue a `prefers-color-scheme` y a `data-theme`, así que el
  selector de Ajustes (D-125) funciona igual que antes.
* **Los tokens de siempre son alias** (`--ink` → `--text`, `--panel` → `--glass`,
  `--hair` → `--line`, `--iris`/`--teal`/`--amber` → acentos 1/2/3) y los tintes de
  estado se derivan con `color-mix`. Ningún componente tuvo que cambiar de clase.
* **Contraste.** En claro los acentos puros dan unos 2:1 sobre el cristal y `--muted`
  3,9:1: valen para rellenos —barras, bordes, resplandores—, no para letra. Como texto se
  oscurecen hacia `--text` hasta pasar AA (D-132). En oscuro se usan puros.
* **Cristal.** Héroe, tarjetas, carril, estados y paneles: fondo `--glass`,
  `backdrop-filter: blur(15px)`, borde `--line` y radios 12/18/24. Detrás, dos
  resplandores (`filter: blur(55px)`) como pseudo-elementos de `.shell`, sin marcado nuevo.
* **Tipografía.** Inter para el texto; IBM Plex Mono se queda para las cifras. La cifra del
  héroe sube a 88 px (`--fs-hero`) con tracking negativo y baja con el ancho.

## 2026-09-26 — Fase 1 de la auditoría: lo que la pantalla afirmaba sin ser cierto

### D-134 — Llamadas simultáneas, la ficha del modelo caro, generadores y el paquete
La auditoría de septiembre recorrió la app con la demo delante y encontró cosas que
ninguna prueba veía. Cada arreglo lleva la suya.

* **Prompts afirmaba un cambio que no ocurrió.** `responder` llama tres veces al modelo
  desde el mismo sitio —clasificar, extraer, contestar— y la pestaña contaba «3 juegos de
  instrucciones» con las mismas 16 ejecuciones y las mismas fechas. El camino de llamada
  no basta para separar «otro prompt» de «otra llamada»: lo que las separa es que
  convivan en una misma ejecución, porque una versión no corre junto a la anterior dentro
  de una traza. `co_occurring_step_keys` lo pregunta a los dos almacenes (con paridad) y
  el paso se marca como llamadas simultáneas. Con mezcla, no se afirma cambio: es el lado
  seguro.
* **La ficha del modelo caro explicaba un paso cualquiera.** «Los modelos grandes se pagan
  sobre todo por lo que escriben», de un paso que recibe 20.000 tokens y contesta 19. El
  porqué sale ahora del reparto real del coste, y con más de 4.000 tokens de entrada por
  llamada avisa de que ese es el caso donde un modelo pequeño más se equivoca. El título
  deja de decir «paso muy corto» (lo corto es la respuesta) y se quita «cuando exista la
  capa de evaluación», que existe desde la Fase 5.
* **Comillas.** La pista del prompt va entre “” dentro de los «» del título, que es la
  norma: antes salían tres niveles de «» seguidos.
* **Dinero entre un céntimo y un dólar**, con dos cifras significativas en los dos
  runtimes: «$0,0692 al mes» pasa a «$0,069». La cifra grande va en Inter con cifras
  tabulares: en la monoespaciada la coma ocupaba una celda y se leía «$14 , 64». La
  posición del símbolo se decide con la internacionalización, por idioma.
* **Interfaz.** «En todas las ejecuciones» deja de pintarse como una cifra; la ficha de un
  problema marca Diagnóstico y no Trazas; en móvil la lista va justo después de la cifra
  y el carril debajo.
* **Id corto.** La lista enseña 12 caracteres y la URL exigía 32. Con el proyecto dicho,
  un prefijo único abre la traza; sin proyecto no se busca por prefijo.
* **`@observe` y los generadores.** El span se cerraba al crear el generador, así que el
  paso duraba cero y lo que se llamaba al iterar colgaba de otro padre: el caso normal de
  un agente con streaming. Ahora el span se abre sin activar, se activa sólo mientras
  corre el cuerpo y se cierra al agotarlo, cortarlo (sin error) o fallar.
* **El paquete.** `pip install "laplace-trace[ui]"` dependía de un `laplace-backend` que no
  estaba publicado. `paquete.yml` construye los dos wheels con la interfaz dentro, los
  instala en un entorno limpio sin el repositorio, arranca `laplace ui` y le pide la
  interfaz y la API; con una etiqueta `v*` publica por Trusted Publishing.

### D-135 — Lo que la demo de un mes destapó en el motor, y las pantallas con red
La demo pasa a ser un mes de «Vuelos Laplace» (814 ejecuciones, reloj simulado sobre la
ingesta normal, prompts v1/v2 con su fecha, anotaciones, comparación A/B y un arreglo
marcado). Con tráfico de verdad aparecieron seis fallos del motor, cada uno con prueba en
`test_demo_hallazgos.py`: un bucle se multiplicaba por cada pregunta distinta (ahora se
agrupa por paso); dos versiones de un prompt se titulaban igual (segunda pasada: modelo,
versión de prompt, dónde cambian las instrucciones o «variante n»); las tiradas de
evaluación salían como cosas que arreglar (las reglas excluyen `laplace-eval`, el gasto
no); el contexto fijo decía «no está en la tabla de precios» de un modelo que estaba
(sin nada que recuperar y con tarifa, no hay hallazgo); la versión de prompt baja se
ponía delante del dinero (ya no: no mueve ninguna cifra); y lo que dejó de ocurrir se
proyectaba como ahorro (se aparta como «ya no ocurre»). Además, una repetición o un bucle
cuyo nombre comparten varios pasos lleva la pista de sus instrucciones, y `add_prompt_version`
y `set_prompt_production` aceptan `at=` para fechar el historial.

Y las pantallas tienen red por fin: `test_pantallas.py` arranca `laplace ui` en un proceso
aparte, le carga la demo con `laplace demo` y abre las seis pantallas en Chromium a 1440
y a 375 px, exigiendo que no haya errores de consola, que el documento no se salga por
los lados y que cada una diga lo suyo; además, que la ficha de un problema y una traza
se abran desde sus enlaces y que la cifra grande no vaya en monoespaciada. Se comprobó
que muerden (un texto que no existe y un bloque de 3.000 px las ponen en rojo). Corre en
un trabajo propio de CI y se salta sola sin Playwright o sin la interfaz construida.

## 2026-09-26 — Fase 3: integraciones

### D-136 — Las trazas de OpenInference y OpenLLMetry, entendidas
Un agente con LangGraph, CrewAI, LlamaIndex, el Agents SDK de OpenAI o el AI SDK de
Vercel suele ir instrumentado ya con OpenInference (Arize) u OpenLLMetry (Traceloop), y
sus spans llegaban como `chain` sin modelo, tokens ni coste. `ingest/convenciones.py`
traduce las dos familias a nuestros atributos antes de clasificar: tipo de span, modelo,
tokens (con la caché dentro del total, sumándola sólo si no cabe), mensajes aplanados,
parámetros, herramientas, entrada y salida, sesión y usuario. Regla única: se rellena lo
que falta y nunca se pisa lo que ya viene en nuestro formato. Los atributos traducidos no
se guardan otra vez en crudo. Nombres contrastados contra
`openinference-semantic-conventions` y `opentelemetry-semantic-conventions-ai`. Pruebas en
`test_convenciones.py`, incluida una que exige que el motor encuentre una repetición en
tráfico que nunca ha visto el SDK de Laplace.

### D-137 — Las otras puertas al modelo: Responses API, `parse`, `messages.stream()` y la respuesta cruda
Sólo se instrumentaban `chat.completions.create` y `messages.create`, y un agente llama al
modelo por más sitios. Una llamada que no pasa por el parche no existe para Laplace: ni
coste ni hallazgos, y la cobertura no puede avisar porque ni siquiera ve el span.

* **La Responses API de OpenAI** (`responses.create` y `.parse`, síncrono, asíncrono y
  streaming), que es la que usa el Agents SDK. `instructions` se une como mensaje de
  sistema, igual que el `system` de Anthropic, porque es la mitad de la identidad del
  paso. Los mensajes con partes `input_text`/`output_text` se aplanan a texto si todas
  son texto; las llamadas a herramientas se guardan enteras. `finish_reasons` guarda el
  motivo del corte (`max_output_tokens`) y si no lo hay, el estado. En streaming no hace
  falta `include_usage`: el evento final trae la respuesta entera con su uso. Un flujo
  que acaba en `response.failed` se cierra como error aunque por fuera haya terminado
  bien, y sin uso se estima y se marca.
* **`chat.completions.parse`, `responses.parse` y `messages.parse`** van directos a la
  red sin pasar por `create`, así que llevan su propio parche. Los ayudantes
  `chat.completions.stream()` y `responses.stream()` no: llaman a `create` por dentro, y
  parchearlos contaría la llamada dos veces. Hay una prueba que lo exige.
* **`messages.stream()` de Anthropic** no llama a nada: devuelve un gestor que lanza la
  petición al entrar en el `with`. El span se abre en esa petición y no al preparar el
  gestor (uno que no se abre no ha llamado al modelo), y el flujo crudo se envuelve con el
  mismo acumulador de `create(stream=True)`, así que `get_final_message()`, `text_stream`
  e iterar pasan todos por él. La petición vive en un atributo privado del gestor: si
  una versión lo mueve, se avisa en el log y las pruebas contra el SDK real lo detectan.
* **El rol `developer`** cuenta como instrucciones en la huella del paso. Es el nombre que
  OpenAI da al prompt de sistema desde los modelos de razonamiento, y sin él todo el
  tráfico que lo use caía en un único paso.
* **`with_raw_response` costaba cero dólares.** Nuestro parche recibía la respuesta HTTP
  sin parsear, el span salía con cero tokens *medidos* y la llamada no costaba nada.
  LiteLLM llama así a OpenAI siempre. Ahora se parsea en el parche —el cliente guarda el
  resultado, y el `.parse()` del usuario recibe el mismo objeto sin releer nada— sólo
  cuando la cabecera dice que el cuerpo ya está leído. La de `with_streaming_response`
  no se toca, porque el cuerpo es del usuario: ahí, y en cualquier respuesta sin uso, la
  entrada se estima y el span se marca como estimado. Una respuesta sin uso ya no puede
  ser una llamada gratis.
* **Sin ruido en el proceso ajeno.** Volcar una salida estructurada hacía que Pydantic
  avisara en cada llamada; `dump_model` pide `warnings=False`.

Pruebas en `test_proveedores_otras_rutas.py` (34), contra `openai` 3.13 y `anthropic` 1.5
con el transporte falso. Dieciséis mutaciones, y todas ponen alguna en rojo: quitar cada
parche (cuatro), parchear también `responses.stream()`, dejar de unir `instructions`,
quitar `developer`, no leer la escritura de caché, no marcar el flujo fallido, abrir el
span al preparar el gestor, mover el atributo del gestor, no leer la respuesta cruda, no
esperarla en asíncrono, leer también la de streaming, escribir el cero medido y dejar los
avisos de Pydantic. Fuera: `client.beta.*`.

### D-138 — La tabla de LiteLLM como capa no verificada
La tabla propia tiene 56 modelos de OpenAI y Anthropic transcritos a mano y con fecha.
Todo lo demás —Gemini, Mistral, DeepSeek, lo que pase por Bedrock, Azure u OpenRouter—
salía como «coste desconocido»: honrado, pero sin cifra para casi cualquiera que no use
esos dos proveedores. Debajo va ahora la tabla comunitaria de LiteLLM
(`pricing/litellm_prices.json`, unos 3.200 modelos de texto).

* **Cómo se convierte.** De USD por token a USD por millón; sólo modos de texto (`chat`,
  `responses`, `completion`); entrada, salida, lectura y escritura de caché (5 min y 1 h)
  y modo prioritario. Los tramos de contexto largo, lotes y regiones de LiteLLM no se
  cargan. **Un modelo a cero en entrada y salida no entra**: para Laplace «cuesta cero» es
  una afirmación, y la de un modelo local no la ha verificado nadie (D-108).
* **Quién manda.** El nombre tal cual, primero en la verificada y después en LiteLLM;
  sólo entonces se pelan los adornos del gateway y se repite. Así un snapshot que LiteLLM
  tenga como entrada exacta no le gana al modelo base verificado, pero un nombre de
  gateway que LiteLLM conoce sí se cobra con su precio. Eso cambia una cosa que ya
  estaba mal: `eu.anthropic.claude-…` en Bedrock regional cuesta un 10 % más, igual que
  las zonas de datos de Azure o el recargo de OpenRouter, y se cobraba en silencio a la
  tarifa verificada del proveedor directo, por debajo de la factura. La prueba de
  identificadores de `test_pricing.py` pasa a mirar sólo la capa verificada, que es lo
  que prueba (cómo se pelan los adornos). Las tarifas propias del usuario mandan sobre
  las dos capas.
* **Cómo se dice.** `Cost.rate_unverified`, aparte de `rate_assumed`. Asumida quiere decir
  suelo («coste mínimo», con «+»), y una tarifa de LiteLLM puede quedarse corta o pasarse:
  llamarla suelo sería afirmar lo que no se sabe. No lleva columna nueva: `rate` termina
  en `@ litellm <fecha>` y los dos almacenes derivan la marca de ahí al leer, con prueba
  de paridad. La interfaz dice «coste (tarifa no verificada)» y la nota lleva la fecha de
  descarga. La capa no entra en `stale_sources`: la que caduca a los 30 días es la
  verificada.
* **Cómo se renueva.** `scripts/precios_litellm.py` descarga, convierte y escribe la capa
  sólo si cambian los modelos, y con `--informe` compara las dos capas tal como las usa el
  producto. `precios-litellm.yml` lo corre los lunes y abre una PR con ese informe. La
  verificada no se toca nunca desde ahí: una discrepancia es un motivo para volver a la
  página del proveedor. En la primera descarga (26 de septiembre) no hay ninguna por
  encima del 1 % en los nombres directos, lo que no sustituye a reverificar la tabla antes
  del 7 de octubre.
* **Coste.** Con 3.200 prefijos más, cada búsqueda se recuerda mientras vive la tabla.

Pendiente: los hallazgos y el panel no distinguen todavía cifras con tarifa no
verificada (sí el span, la traza y la ficha técnica), y la traza agregada no lleva la
marca. Pruebas en `test_precios_litellm.py` (22); ocho mutaciones comprobadas —que
LiteLLM mande, pelar antes, quitar la marca, convertirla en asumida, dejar entrar los
modelos gratis o los que no son de texto, no derivar la marca al leer y no pasar a
millones— y todas ponen alguna en rojo.

### D-139 — Agentes en TypeScript: la guía, y dos fallos de la ingesta que destapó
La guía (`docs/typescript.md`) no se ha escrito de memoria: un agente en Node con
`openai` 7.23 se ha instrumentado con OpenInference-js y con OpenLLMetry-js, contra un
servidor que responde como OpenAI, exportando a `laplace ui`. Lo que salió:

* **El exportador JSON de Node guardaba ids inventados.** OTLP/JSON manda los ids de
  traza y de span en hexadecimal; el JSON genérico de protobuf los espera en base64. Un
  id hexadecimal de 32 caracteres también es base64 válido, así que se leía sin error y
  se guardaba un id de 24 bytes que no existía. Ahora se pasan a base64 antes de leer,
  en las dos grafías de los campos; se distinguen por el largo (32/16 caracteres en
  hexadecimal, 24/12 en base64), así que lo que ya venía en base64 sigue valiendo.
  `test_otlp_json.py`.
* **Las convenciones GenAI nuevas.** OpenLLMetry-js 0.27 ya manda `gen_ai.provider.name`
  en vez de `gen_ai.system`, y los mensajes con el texto en `parts` en vez de `content`.
  Lo primero dejaba el proveedor vacío; lo segundo dejaba la huella del paso sin
  instrucciones, así que dos pasos con prompts distintos caían en uno. Se leen los dos.
  Los mensajes se siguen guardando en crudo, como dice el contrato.
* **Lo que no se puede arreglar desde aquí.** OpenLLMetry-js no manda los tokens leídos
  de caché de OpenAI aunque la respuesta los traiga: el coste sale por encima de la
  factura y Laplace no tiene cómo saberlo. La guía lo dice y recomienda OpenInference
  para OpenAI. Y con estos instrumentadores sólo llegan las instrucciones, no el camino
  de llamada, así que dos pasos con el mismo prompt se agrupan juntos.

El paquete fino `@laplace/sdk` (`init`, `observe`, `getPrompt`) espera a que el usuario
reserve el scope en npm. Mutaciones comprobadas: no convertir los ids, quitar la grafía
`parentSpanId`, no leer `gen_ai.provider.name` y no leer `parts`; todas en rojo.

### D-140 — La prueba inestable era un servidor falso que no leía el cuerpo
`test_el_notificador_no_sigue_redirecciones` fallaba una de cada catorce veces con
`ConnectionAbortedError` (36 de 500 en un bucle). Su servidor falso contestaba 302 sin
leer el cuerpo del POST y cerraba; en Windows, cerrar un socket con datos sin leer manda
un RST, y el cliente veía la conexión anulada antes de leer la respuesta. El fallo era de
la prueba, no del notificador: ahora el servidor lee el cuerpo antes de contestar. 0 de
2.000 en el mismo bucle, y la prueba sigue mordiendo: con un `build_opener()` que sí
sigue redirecciones se pone en rojo.

### D-141 — Cerrar la Fase 3: el doble conteo, el nodo de LangGraph y la tarifa sin verificar en lo agregado
* **`laplace.init()` junto a otro instrumentador de LLM contaba cada llamada dos veces.**
  Se vio con LangGraph + OpenInference: ocho llamadas para cuatro, en trazas separadas y
  sin id de respuesta con el que emparejarlas en la ingesta. Ahora, en cada llamada, el
  parche mira si hay un instrumentador de OpenInference (cualquiera: la familia es sólo
  de IA) o de OpenLLMetry (una lista de los de LLM y frameworks de agentes; ahí también
  viven `requests` o `httpx`, que no cuentan) importado **y activo**, y si lo hay cede:
  llama al original sin abrir span y avisa una vez en el log, nombrando el
  instrumentador y cómo evitarlo. Se mira en cada llamada y no en `init()` porque lo
  normal es instrumentar el framework después. `defer_to_others=False` (o
  `LAPLACE_DEFER_TO_OTHERS=false`) lo desactiva para quien mande ese instrumentador a
  otra parte. Lo que se pierde al ceder: las llamadas directas que no pasen por el
  framework, y la atribución de versión de prompt y el sitio del SDK para las que sí;
  es el precio de no inflar el gasto, que era el error caro. Contrastado de nuevo con
  el agente de LangGraph: cuatro llamadas en dos trazas y un aviso. La clase base de
  los instrumentadores pasa a las dependencias de desarrollo para probarlo.
  `test_convivencia.py`.
* **El nodo de LangGraph es el sitio del paso.** OpenInference no dice desde dónde se
  llama, pero deja `metadata.langgraph_node`; se usa como padre del paso cuando el SDK
  no ha dicho otra cosa. Dos nodos con el mismo prompt ya no se juntan.
* **La tarifa sin verificar llega a lo agregado.** Los hallazgos llevan
  `cost_unverified` y `unverified_rate_models`, la traza agregada `rate_unverified`, y lo
  dicen la tarjeta, la ficha, la cabecera de la traza y la alerta de Slack («tarifa sin
  verificar», nunca «al menos»: no es un suelo). Se decide por modelo con la tabla en
  vigor y no span a span, así que no hace falta otra columna en los dos almacenes; lo
  guardado se recalcula cuando cambia una tarifa propia (D-123), pero no cuando se
  despliega una tabla nueva, y ese es el hueco que queda.

Mutaciones comprobadas: no ceder nunca, quitar un nombre de la lista, avisar en cada
llamada, ceder a un instrumentador apagado, ceder a cualquier `opentelemetry.
instrumentation.*`, pisar el sitio del SDK con el nodo, no pasar el modelo a la marca,
no marcar la traza agregada y convertir la marca en suelo; todas en rojo.

## 2026-09-26 — Fase 4: escala

### D-142 — Diez millones de spans al día: qué se ha medido y qué se ha cambiado
`scripts/carga.py` genera dentro de ClickHouse (`INSERT … SELECT FROM numbers()`, unos
120.000 spans/s) un día de tráfico con forma de agente —trazas de ocho spans, 3 % de
errores, repeticiones en una de cada diez, payloads de un par de KB, la mitad del
volumen en un proyecto— y mide las pantallas contra el objetivo escrito: **10 millones
de spans al día y el Diagnóstico por debajo de 1,5 s**. Se ha decidido con esos números
delante, y lo que la hoja de ruta proponía y los números no apoyaban no se ha hecho.

* **Repeticiones y bucles, de tumbar la máquina a algo más de un segundo.** Con 5
  millones de spans en un proyecto, la consulta de repeticiones pedía más de 2 GB y no
  terminaba en cinco minutos; midiendo la primera vez, se llevó por delante la máquina
  virtual de Docker. Hacía una docena de agregados con `FINAL` para cada pareja (traza,
  entrada), y casi ninguna se repite. Ahora una primera pasada, sin `FINAL` y agrupando
  por un hash de 64 bits, busca las parejas candidatas, y la agregación de siempre sólo
  corre sobre ellas. El filtro sólo acota: un span reenviado o una colisión cuelan una
  candidata de más, y el `HAVING` de siempre, sobre `FINAL`, la descarta. Los bucles
  llevan el mismo atajo. Una prueba de paridad nueva pone un bucle justo en el mínimo de
  vueltas, porque ninguna vigilaba ese borde.
* **El resumen del Diagnóstico se pedía dos veces**; ahora cada lectura se hace una vez
  por petición. Lanzar las cinco lecturas a la vez se probó y **no se hace**: cada
  consulta ya usa todos los núcleos, y en paralelo tardaban 5,1 s frente a 5,0 en serie.
* **Caché de un minuto del Diagnóstico en la nube**, que se pide al abrir el inicio, la
  lista y cada traza. Cualquier escritura por `/api` la borra (marcar un hallazgo, una
  tarifa, la demo), porque quien lo hace espera verlo ya; lo que llega por la ingesta
  tarda hasta un minuto en verse, que es el precio. En local no hay caché.
  `LAPLACE_OVERVIEW_CACHE_S` la cambia.
* **Ingesta:** traducir el lote sale del bucle de eventos (`run_in_threadpool`); el
  proyecto se registra una vez por proceso y no en cada lote, y se olvida al borrarlo;
  ClickHouse inserta con `async_insert=1, wait_for_async_insert=1`, que junta los lotes
  pequeños en el servidor sin que el 200 deje de significar «guardado».
* **Payloads con `ZSTD(3)`**: menos de la mitad de disco que con LZ4 sobre una muestra
  de un millón de filas (datos sintéticos, muy repetitivos: con datos reales la
  diferencia será menor). Cambiar el códec no reescribe nada; las partes viejas lo toman
  al fusionarse.
* **Lo que no se ha hecho, y por qué.** La clave de ordenación nueva y la tabla
  `trace_id → proyecto`: abrir una traza ya tarda 0,03 s, con proyecto o sin él, gracias
  al índice bloom de `trace_id`. Quitar `FINAL`: sobre la tabla medida cuesta lo mismo
  con que sin él, y la ganancia no justifica reescribir 28 consultas con su paridad.
  Acotar por tiempo las subconsultas de filtros: con un solo día de datos no se nota, y
  un portátil no aguanta generar semanas de histórico a este ritmo para medirlo. Los
  tres quedan anotados en la hoja de ruta, con la medida que falta.

**Resultado, sobre 10 millones de spans en un día (proyecto grande, 4,8 millones):**
Diagnóstico unos 4 s en la primera carga y lo que tarde servir la caché después (antes
no terminaba); Panel 1,2–1,4 s; lista de trazas 1,0 s, sólo errores 0,6 s, con búsqueda
1,6 s; abrir una traza 0,03 s. **El objetivo del Diagnóstico no se cumple en la primera
carga**: lo que queda son cuatro consultas de 0,6 a 1,3 s cada una recorriendo toda la
ventana, y bajar de ahí pide preagregados (vistas materializadas por hora para el uso
por paso, el resumen y la cobertura) o calcular repeticiones y bucles al ingerir. Es el
siguiente paso de la fase.

### D-143 — El Diagnóstico que alguien mira se recalcula antes de caducar
Los preagregados se descartaron por ahora, con los números delante: sólo abaratan el
resumen, el uso por paso y la cobertura (unos 2 s de los 4), no las repeticiones ni los
bucles, que miran dentro de cada traza. El Diagnóstico habría quedado en unos 2,5 s a
cambio de mucho trabajo delicado: no contar dos veces un lote reenviado, cuadrar las
horas del borde de la ventana, rellenar el histórico, borrar los preagregados con los
datos y la paridad exacta con el modo local.

En su lugar, la caché de un minuto (D-142) se renueva sola: cada petición deja guardada
su forma de recalcularse, y un proceso de fondo recalcula cada Diagnóstico cuando lleva
tres cuartos de su vida en la caché, **sólo si alguien lo ha leído en la última media
hora**, para que lo que nadie abre no gaste ClickHouse. La ventana se calcula al
renovar, no al pedir. Si mientras se recalcula alguien cambia algo por la API, el
resultado se tira: se calculó con los estados de antes. Un fallo al renovar deja el valor
anterior hasta que caduca, nunca para siempre. En local no hay caché ni renovador.

Medido contra ClickHouse con 2 millones de spans: primera apertura 2,6 s; después,
0,00 s también pasado el minuto. **La primera apertura de un proyecto sigue tardando lo
que tarda** (unos 4 s con 10 millones al día); los preagregados siguen siendo la vía si
eso llega a importar. Pruebas en `test_diagnostico_precalculado.py`, con reloj
inyectado; seis mutaciones comprobadas.

### D-144 — Buscar dentro de prompts, respuestas y herramientas
La lista de trazas buscaba en el nombre de los pasos y en el id. Lo que se busca de
verdad está en el contenido: el número de pedido del que se quejó un cliente, la frase
que el modelo no debía decir, el vuelo que devolvió una herramienta. `TraceFilter.content`
(y `?content=` en `/api/traces`, de 3 a 200 caracteres) busca en mensajes de entrada y
salida, argumentos y salidas de herramientas, consulta y documentos recuperados, y
entrada y salida de cada paso. Sin distinguir mayúsculas, también con tildes; `%` y `_`
son literales; el nombre del paso **no** es contenido (para eso está `search`). Se
filtran trazas, no spans, como en los demás filtros (D-123).

* **Sólo se recorre la ventana.** El contenido es casi todo el disco, así que la
  subconsulta va acotada a `since`/`until` además del proyecto. Un texto que sólo está en
  un span fuera de la ventana no encuentra su traza, a propósito.
* **ClickHouse: `idx_contenido`, `ngrambf_v1(4, 65536, 2, 0)` sobre
  `lowerUTF8(concat(...))` y consulta con `LIKE`.** Medido con 2 millones de spans en el
  proyecto grande: buscar un id («TK123456») pasa de 1,17 s a 0,19 s (26 de 995
  gránulos); el índice ocupa 55 MB, un 5 % de la tabla. Con bloques de 3 bytes se leían
  41 de 86 gránulos en la prueba pequeña (los trozos de un id están en casi todos), con 4,
  uno; `tokenbf_v1` no sirve para subcadenas. **Trampa:** el analizador nuevo de la 24.8
  sólo usa el índice si la consulta escribe la misma expresión, y cualquier constante
  dentro —un `'\n'` entre columnas, `concatWithSeparator`— hace que deje de reconocerla.
  Por eso las columnas van pegadas (`CONTENIDO` en `clickhouse.py`, idéntica a la del
  esquema) y una prueba mira el `EXPLAIN`. Un texto que sale en miles de trazas no puede
  descartar gránulos: 1,7 s con 2 millones de spans a la semana, que es lo que cuesta
  recorrerlos. Instalaciones anteriores: `ADD INDEX IF NOT EXISTS`, sin materializar en el
  arranque; las partes nuevas lo traen y las viejas se recorren.
* **SQLite: se recorre, sin FTS5.** La hoja de ruta decía FTS5; medido con 100.000 spans
  de unos 3 KB (430 MB), buscar un id tarda 2,7 s y la búsqueda por nombre que ya existía
  2,2 s: lo que pesa es agregar las trazas, no leer el texto. Un índice de trigramas
  duplicaría el fichero y pediría disparadores frágiles con `INSERT OR REPLACE`. Las
  minúsculas las hace `str.lower` registrado como función, porque el `lower()` de SQLite
  sólo sabe de ASCII y «ÁRBOL» no casaba con «árbol».
* **Interfaz:** un selector junto al buscador, «En nombres e id» / «En prompts y
  respuestas». Y un fallo que ya estaba: el enlace «Más antiguas» sólo llevaba proyecto y
  días, así que la página siguiente perdía la búsqueda y todos los demás filtros.

Pruebas en `test_busqueda_contenido.py` (los dos almacenes) y en `test_pantallas.py`;
nueve mutaciones comprobadas, entre ellas quitar la ventana, las minúsculas, el escape
y poner un separador en la expresión.

### D-145 — Mediana, p95 y errores en el Panel, en lugar de la duración media
El Panel decía la duración **media** por ejecución. Con agentes la media no es la espera
de nadie: unas pocas ejecuciones colgadas la disparan. Ahora las métricas por ejecución
son coste, tokens, pasos, **duración mediana, duración p95 y ejecuciones con error**
(unidad `ratio`), cada una contra el periodo anterior cuando lo hay (D-107).

* **Percentil de rango más cercano** (`nearest_rank`, posición `ceil(q·n)`): es la
  duración de una ejecución que existió, no una interpolación entre dos, y sale idéntica
  en los dos almacenes. ClickHouse lo calcula con el mismo índice sobre
  `arraySort(groupArray(...))`; SQLite trae las duraciones ordenadas y elige en Python.
  La duración es la de la traza de principio a fin, la misma que en `TIMESERIES_SQL`.
* **Coste:** una consulta más por periodo, 0,3 s con 2 millones de spans a la semana.
  Los percentiles no se pueden sumar tramo a tramo, así que no salen de la serie. La del
  periodo anterior sólo se lanza si hay comparación.
* **Errores:** salen de los tramos, que ya contaban trazas con error; de cero a algo no
  da «infinito por ciento», da «sin comparación».

Pruebas en `test_panel_latencia.py` (los dos almacenes) y la paridad del Panel, que
compara todas las métricas por ejecución; nueve mutaciones comprobadas, entre ellas
redondear el rango hacia abajo en cada almacén.

### D-146 — La demo llega hasta ahora
La demo generaba de hace treinta días a ayer a medianoche: `range(dias, 0, -1)` nunca
pasaba por el día de hoy, aunque el bucle ya tenía el `continue` para no pasar de ahora.
El Diagnóstico aparta como «ya no ocurre» lo que no se ha visto en el último día (D-135),
así que cargada por la mañana la demo tenía siete problemas pendientes y cargada por la
tarde los siete salían resueltos. Era la prueba inestable de `test_pantallas.py` apuntada
en la hoja de ruta: no fallaba por tiempo de carga, fallaba según la hora. Ahora el
bucle incluye hoy hasta el momento de la carga. `test_demo_hoy.py` recorre el bucle de
verdad sin red, a las 00:30, 09:30, 16:30 y 23:30.

### D-147 — Cinco idiomas: cómo viaja el idioma y cómo se escribe una cifra
Los idiomas, elegidos por el usuario: **español, inglés, portugués (Brasil), francés y
chino simplificado**. Esta entrada es la base; cada pantalla y el motor se traducen
después, por tandas.

* **Backend: una variable de contexto por petición** (`idioma.py`). El motor redacta
  títulos, lecturas y avisos en muchas funciones; pasar el idioma de mano en mano habría
  dejado siempre alguna sin él. `MiddlewareIdioma` (ASGI puro) lo fija con `?lang=` —un
  enlace de un correo abre en el idioma del correo— o con `Accept-Language`, respetando
  los pesos, y responde con `Content-Language` y `Vary`. Sin idioma pedido, español: la
  API nació así y un script sin cabeceras sigue recibiendo lo mismo. Lo que corre fuera
  de una petición lo fija con `usar()`: el Diagnóstico lleva el idioma en la clave de la
  caché y su `calcular` lo fija, porque el renovador de D-143 no hereda el de nadie.
* **Web: sin librería.** Todo son componentes de cliente sobre HTML estático, y
  `next-intl` resuelve sobre todo rutas por idioma y render en servidor. `idioma.ts`
  guarda el idioma fuera de React (lo leen `format.ts` y `api.ts`, que manda
  `Accept-Language`); `textos.ts` tiene `t()` y `tn()` (plurales con
  `Intl.PluralRules`); `i18n.tsx`, el proveedor, `tr()` para textos con elementos dentro
  y el selector. El idioma sale de lo elegido en ese navegador, si no del navegador, y si
  no, inglés. Cambiarlo **vuelve a montar la página entera**: las frases del motor hay que
  pedirlas otra vez, y así ninguna pantalla se queda con las del idioma anterior.
* **Catálogos:** `mensajes/es.ts` define las claves y los otros cuatro se tipan contra
  él, así que una clave que falte no compila. `test_textos_web.py` carga los catálogos
  con Node y exige los mismos huecos que el español (en los plurales, cualquiera de la
  pareja): un `{n}` perdido en chino compilaría y dejaría la cifra fuera de la frase.
* **Cifras por idioma.** En español «1.235 US$», como decía la hoja de ruta (antes
  «$1.235»); en inglés «$1,235»; en portugués «US$ 1.235»; en francés «1 235 $US» con
  espacio fino; en chino «US$1,235». Entre la cifra y su unidad, espacio que no parte
  línea. Las precisiones por magnitud no cambian. Sin `Intl.NumberFormat`, cuya salida
  cambia con la versión de ICU del navegador.
* **El espejo se prueba de verdad.** `test_idioma.py` ejecuta `format.ts` con Node (22.6
  o posterior quita los tipos; un gancho resuelve los imports sin extensión) y lo compara
  con `cifras.py` valor a valor en los cinco idiomas. Antes sólo podía comprobar la
  convención. Y se acaba la divergencia «aceptada» del redondeo: los dos redondean el
  valor binario exacto con empate hacia arriba (`toFixed` allí, `Decimal(x).quantize(...,
  ROUND_HALF_UP)` aquí). Hay casos que separan esto de un `Math.round` a mano.
* **Chino:** las fuentes CJK del sistema detrás de Inter y Plex, sin descargar ninguna
  (pesan megas y el modo local no puede depender de internet), y más interlineado.
* **Pruebas de pantallas en español explícito:** Chromium sin interfaz anuncia `en-US`.
  Una prueba nueva abre con el navegador en francés y cambia a inglés con el selector.

### D-148 — El producto entero en cinco idiomas
Sobre la base de D-147, todo lo que se lee está traducido: las cuatro reglas del
Diagnóstico con sus fichas (incluido el código y el prompt que proponen como arreglo), la
cobertura, el Panel, el presupuesto, el seguimiento de lo marcado, Prompts, las
evaluaciones, los avisos y todas las pantallas de la web.

* **Backend: catálogos en JSON** (`laplace_backend/textos/{es,en,pt,fr,zh}.json`), con
  `t()` y `tn()`. JSON y no módulos: se revisan como textos y los lee cualquier
  herramienta de traducción. Las reglas de plural son las de `Intl.PluralRules` —
  `test_textos.py` las compara con Node en los cinco idiomas—, y las duraciones
  (`span_label`, `window_label`) son las mismas claves que en la web, comprobadas en
  espejo. Una clave que no existe lanza `KeyError`: devolver el español escondería el
  fallo justo en el idioma que nadie del equipo lee.
* **Frases que se construían a trozos**, reescritas enteras: la lectura del Panel pegaba
  sujeto y verbo sueltos («el gasto» + «sube»), y eso no se traduce. Ahora cada cláusula
  es una clave. En portugués y francés, `{ventana}` lleva artículo («os últimos 7 dias»,
  «les 7 derniers jours»), así que va detrás de preposiciones que no contraen («durante»,
  «sur») en lugar de «em»/«de», que darían «em os» o «de les».
* **Los avisos** salen de un proceso de fondo, sin petición de la que sacar el idioma:
  los ajustes de alertas guardan el de quien los configuró (`language`), y `evaluate()` y
  `send_test()` redactan dentro de `idioma.usar(...)`.
* **Guardias.** `test_sin_frases_sueltas.py` lee con `ast` los módulos que redactan y
  falla con cualquier literal con aspecto de frase que no sea docstring, log, error
  interno, SQL o fragmento de código (lista cerrada de excepciones: diagnósticos del
  operador). Lee el código en vez de ejecutarlo para cubrir los caminos que ninguna demo
  recorre. En la web, `tsc` exige todas las claves en los cinco catálogos,
  `test_textos_web.py` los mismos huecos, y una prueba de pantallas abre las cinco
  pantallas principales en inglés y falla si queda interfaz en español.
* **Detalles que cambian por el camino.** La marca de pasos indistinguibles pasa de
  «(variante n)» a «(#n)», neutra en los cinco idiomas (`motor.py` la recortaba buscando
  el texto literal). La línea técnica ya no se identifica por su etiqueta, que ahora se
  traduce: «Probar antes» busca el valor con «→». Los títulos de pestaña los pinta React
  (un `<title>` en el proveedor de idioma) y no los metadatos de Next, que salían fijos en
  el HTML estático y se volvían a aplicar después de montar; los `layout.tsx` que sólo
  existían para el título se borran. `not-found` pasa a cliente para no quedarse en
  español. Los importes en euros usan `money(…, "EUR")` del idioma.

**Lo que sigue en español, a propósito o pendiente:** los mensajes de error de la API
(`HTTPException.detail`, que la web enseña tal cual en algunos formularios); el prompt del
juez, que es para un modelo y no para una pantalla; los datos de la demo, que son el
agente de un cliente hispanohablante; la línea de órdenes y los logs.

### D-149 — Los errores de la API, en el idioma de la petición
D-148 dejó en español los `HTTPException.detail`, y la web los enseña tal cual en sus
formularios: una contraseña corta, una invitación caducada o un prompt repetido salían en
español en mitad de una pantalla en chino. Ahora cada error sale del catálogo
(`error.*`, y `estado.*` para los tres `detail` de estado: juez apagado, alertas apagadas
y prompt desplegado), igual que las frases del motor.

* **Todos los caminos, también los que no pasan por una ruta.** El 401 del middleware de
  autenticación, el 413 del tope de tamaño (`limites.py`) y el del cuerpo OTLP
  descomprimido contestan sin llegar a la ruta; funcionan porque `MiddlewareIdioma` es el
  más externo. Una prueba lo comprueba con peticiones de verdad, y moverlo por dentro
  del de autenticación la hace fallar (comprobado).
* **Los validadores** (`validar_contrasena`, `motivo_correo_invalido`) devuelven el
  motivo ya traducido, porque la ruta lo pasa tal cual a `detail`.
* **Frases enteras, no trozos.** `_solo_instalacion("cambiar una tarifa")` pegaba la
  acción delante de «afecta a todos los proyectos…»; ahora es una clave por acción. Lo
  mismo con el rol que falta («hace falta ser admin»): una clave por rol.
* **`MetadataUnavailable` se queda en el log.** Lo que dice («postgres no responde: …»)
  es para quien opera la instalación; a la pantalla le llega «la base de metadatos no
  está disponible ahora mismo; si era un cambio, no se ha guardado», traducido.
* **Guardia** (`test_errores_api.py`): lee con `ast` las rutas, `auth`, `cuentas`,
  `limites`, `main` y la ingesta, y falla con cualquier literal con aspecto de frase
  dentro de un `HTTPException`, un `AuthError`, un `CuerpoDemasiadoGrande`, un cuerpo con
  `"detail"` o lo que devuelven los validadores. Encontró uno que se me había pasado
  (««x» no es una dirección de correo válida»).
* **Web:** el mensaje de respaldo («500 en /api/…») pasa por el catálogo, y un 422 de
  FastAPI, que trae una lista de campos en vez de una frase, ya no se enseña como
  «[object Object]» sino como «La petición no es válida: name, project_id».
* **Un código donde hacía falta distinguir un caso.** `NeedsKey` escondía el mensaje
  «credencial inválida» comparando el texto, que en inglés ya no casaba. `AuthError`
  lleva ahora un `code` opcional que sale en la respuesta (`"credencial_invalida"`), y
  la web lo lee en `ApiError.code`. El resto de errores no lo necesitan todavía.

Sin idioma pedido la API sigue contestando en español, como un script sin cabeceras
esperaba (D-147).

### D-150 — Los pendientes de la auditoría del rediseño (A1, A2, B1) y la maquetación
Tres hallazgos de `docs/auditoria-rediseno.md` que seguían abiertos, y el ancho de
pantalla que la hoja de ruta apuntaba para la Fase 5.

* **A1 · Un rango vacío no es un proyecto vacío.** Con trazas de hace tres días y el
  rango en un día, el Diagnóstico y el Panel decían «Esperando la primera ejecución», que
  es lo primero que se ve al entrar y hace pensar que la instalación no funciona.
  `NoTracesYet` recibe ahora `last_seen` (ya venía en `/api/projects`) y el rango: si
  alguna vez llegó algo, el título es «No hay ejecuciones en el último día», dice cuándo
  llegó la última y ofrece **el rango más corto que la incluye** con un botón. Si es más
  vieja que el rango más largo (30 días), lo dice sin botón, porque no hay rango al que
  llevar. Las instrucciones de instalar quedan sólo para el proyecto al que nunca ha
  llegado nada, con un texto que ya no habla de rangos. Pruebas de pantallas con una traza
  de hace tres días (el botón lleva a `days=7` y enseña los datos) y otra de hace noventa.
* **A2 · El coste de cada traza, visible en el móvil.** La pregunta va en una línea
  con puntos suspensivos, y en una celda de tabla eso fija el ancho mínimo de la
  columna: a 375 px la tabla medía 479 y el coste, por el que se ordena la lista,
  quedaba fuera. La columna de la traza lleva `width: 100%; max-width: 0` en estrecho y
  es ella la que recorta.
* **B1 · Las pestañas, enteras a cualquier ancho.** Hasta 1100 px la cabecera pasa a dos
  filas (el contexto arriba, las pestañas debajo a lo ancho): a 1024 «Ajustes» quedaba
  bajo el difuminado. En móvil las seis van en una rejilla de tres por dos en vez de una
  barra desplazable que escondía «Prompts» y «Ajustes» y que nadie desplaza porque no
  parece desplazable. El difuminado ya no es fijo: `TopBar` mide hacia qué lado quedan
  pestañas escondidas (`data-desborda`) y difumina sólo ese borde.
* **A 1440 px ya no sobra un tercio.** La carcasa pasa de 1440 a 1280 y se centra, así
  que cabecera y contenido comparten bordes. El Diagnóstico llena ese ancho (antes se
  quedaba en 1180, pegado a la izquierda). A partir de 1200 px el Panel pone los picos
  al lado de las métricas y «quién gasta» debajo a todo el ancho, y la ficha de un
  problema pone lo que se hace con él (probar antes, marcarlo, ver las trazas) en una
  columna fija junto a lo que se lee, con los párrafos a 75 caracteres. Ajustes,
  Evaluaciones y Prompts, que son formularios y listas, se quedan en la medida cómoda
  de 960.
* **Pruebas de pantallas** para las tres: el coste dentro de su marco a 375 px; las seis
  pestañas enteras a 375, 1024 y 1440 en español y en francés; y a 1440 el contenido
  llega hasta donde llega la cabecera. Con la interfaz anterior fallan siete de nueve
  (las dos que pasan son la navegación a 1440, que ya estaba bien).

### D-151 — Menos texto: lo que puedes dejar de pagar, un distintivo de confianza y el porqué plegado
La hoja de ruta pedía «una frase y un ¿por qué? plegable», las guardas como distintivo y
no como párrafo, y un héroe que diga «Puedes dejar de pagar hasta X».

* **El héroe.** La cifra grande es ahora lo evitable, en el mismo ámbar que su tramo de
  la barra: «Puedes dejar de pagar hasta 131 US$ al mes», y debajo, en una frase, «de
  los 216 US$ que te costará este mes, al ritmo de los últimos 6,8 días». Antes eran dos
  cifras grandes con una flecha —total → lo que quedaría—, y lo que el producto vende
  (el ahorro) había que restarlo. Sin proyección no se promete futuro: «Te habrías
  ahorrado hasta X en 6 horas», sobre lo ya gastado. Cuando no hay nada evitable, no hay
  tarifas o el evitable pasa del 90 % (D-073, D-107, `CASI_TODO_EVITABLE`), el héroe
  sigue como estaba: esos casos tienen su propia forma de decirse.
* **Las salvedades, en un distintivo** («Confianza media · 2 notas») que se despliega
  con las mismas líneas y porqués de D-124: nada se quita, sólo se pliega. El nivel lo
  pone la peor salvedad: **baja** si falta gasto por contar (pasos sin tarifa) o la
  cobertura es mala; **media** si la cifra es un suelo (tarifa asumida) o el evitable
  pasa del umbral de cautela; **alta** en otro caso. Una proyección no baja el nivel: no
  es una duda, es cómo se calcula, y va como nota. Cada nivel dice en una frase qué
  significa, y esa frase tiene que ser verdad en todos los casos que lo producen.
* **La ficha de un problema**: «Qué está pasando» sigue a la vista; «¿Por qué pasa?» y
  el cálculo del ahorro («¿Cómo se calcula?») van plegados. Lo que se hace con el
  problema ya estaba al lado (D-150).
* Pruebas de pantallas: el héroe dice lo que puedes dejar de pagar, la línea de la
  proyección no se ve hasta abrir el distintivo, y la ficha llega con el porqué cerrado.
  Las claves que se quedaron sin uso (`diag.si_arreglas`, `prob.por_que`) se borran.

### D-152 — Dónde se va el dinero: gasto por día con la franja evitable y coste por paso
El Diagnóstico decía cuánto se puede dejar de pagar pero no cuándo ni en qué. Debajo de
la lista de problemas (lo primero sigue siendo qué arreglar) hay ahora dos gráficos: el
gasto por día —por hora si la ventana es de menos de tres días— con la parte evitable
encima, y el coste de los pasos que más gastan con su parte evitable.

* **La franja evitable es un reparto, y se dice.** Las reglas miden lo evitable de cada
  hallazgo sobre la ventana entera, no día a día. Se reparte por tramos **en proporción
  a lo que gastó en cada tramo el paso del hallazgo** (`insights/grafico.py`). Así los
  tramos suman exactamente lo evitable del héroe —nada se cuenta dos veces ni se
  inventa— y un día en que ese paso no trabajó no recibe nada. Lo descartado: pasar las
  reglas día a día (los umbrales cambian con el tamaño de la ventana y la suma no
  daría la cifra de arriba) y repartir a partes iguales (pintaría derroche en días sin
  actividad; la prueba lo comprueba mutando a eso). Debajo del gráfico, plegado, «¿Cómo
  se reparte lo evitable por días?». Si algún hallazgo no tiene gasto de su paso en la
  serie, su importe no se coloca en ningún día y se dice cuánto es. Y ningún tramo
  enseña más evitable que gasto.
* **Una lectura más**, `step_cost_series`, en los dos almacenes: el gasto total por tramo
  (por el instante de cada span, no de la ejecución como el Panel: aquí la pregunta es
  cuándo se gastó) y el de cada paso con el filtro de las reglas, sin tiradas de
  evaluación, que es de donde sale lo evitable. Se pide una vez por Diagnóstico.
* **Los pasos se nombran como en el resto del producto** (`disambiguate`, D-106): en la
  demo salían tres filas «responder» con tres cifras distintas.
* **Dos textos que el CSS pintaba en español en los cinco idiomas**, encontrados por el
  camino: el «¿por qué?» de los avisos plegados y la marca «avanzado». Los pasa ahora el
  proveedor de idioma como variables (`--txt-porque`, `--txt-avanzado`), y una guardia
  en `test_textos_web.py` falla con cualquier `content:` con texto fuera de `var()`.
  Los plegados que ya son una pregunta («¿Cómo se calcula?») no llevan el sufijo.

Pruebas en `test_grafico_diagnostico.py` (los dos almacenes): los tramos suman el gasto
y lo evitable del héroe, lo evitable cae donde gastó su paso, orden y resto de pasos,
tramos por hora en ventanas cortas, el tope por tramo y los homónimos; y dos de
pantallas.

### D-153 — El grafo del agente en la vista de traza
Encima del árbol, «Cómo está hecho el agente»: una caja por paso (identidad `step_key`, o
tipo y nombre si no la hay) con sus llamadas y su coste, y una flecha por cada paso que
llama a otro con cuántas veces (en ámbar si son varias: un bucle pasa de treinta filas a
una flecha «×6»). Capas por la menor profundidad a la que aparece cada paso; una llamada
hacia atrás va curvada por debajo. Ámbar el paso con un problema del Diagnóstico en esta
traza, rosa el que falló. Sin tarifa no cuesta cero: el nodo dice «sin tarifa» o «≥ X».
Sale del árbol que ya llega a la web, sin backend nuevo. Prueba de pantallas: nodos y
aristas en una traza de la demo, y «×n» en una con bucle.

## 2026-09-28 — Fase 5: el cierre

### D-154 — El Diagnóstico medido otra vez, con el gráfico dentro
`step_cost_series` (D-152) es una lectura más en cada Diagnóstico y no se había medido
con volumen. `scripts/carga.py` enseñaba sólo las cuatro lecturas más pesadas, así que
una lectura nueva podía pesar sin verse; ahora da el desglose entero, con la media de
cada lectura y su parte de la suma.

**Dónde:** 10 millones de spans en un día (proyecto grande, 5 millones), en un
contenedor Linux con 4 núcleos y 16 GB, **unas tres veces más lento que el portátil de
D-142**: genera 40.000 spans/s frente a 120.000. Las cifras absolutas no se comparan con
las de D-142; el reparto sí.

* **Con las partes recién escritas** (medida justo después de generar), el Diagnóstico
  tardó 10,5–11,4 s. **Con las partes ya fusionadas** (la misma tabla minutos después),
  5,8–6,7 s con un día de rango y 5,3–5,6 con siete. La primera cifra es la del peor
  momento de un servidor que ingiere sin parar; la segunda, la de uno tranquilo.
* **`step_cost_series`: 0,75–0,80 s, un 13–14 % de la suma de lecturas**, en el mismo
  orden que las otras cinco (bucles 21 %, repeticiones 20 %, uso por paso 16 %, resumen
  14–16 %, cobertura 14–15 %). No es la que manda, pero es una sexta consulta que recorre
  la ventana entera.
* **Probado y no hecho: juntar sus dos pasadas en una.** Hoy hace una para el gasto total
  por tramo y otra, con el filtro de las reglas, para el de cada paso. En una sola
  (agrupando por paso y tramo, con `sumIf` para lo que no es tirada de evaluación) baja
  de 0,73 a 0,57 s: **0,2 s de 6, un 3 %**, a cambio de reescribir la consulta en los dos
  almacenes con su paridad. No acerca el objetivo de 1,5 s, que sigue pidiendo lo mismo
  que en D-142: preagregados por hora para uso por paso, resumen, cobertura y ahora
  también la serie del gráfico, que es exactamente la forma de un preagregado por hora.
* **El Panel** tarda 1,6–1,8 s aquí (1,2–1,4 en el portátil) y la lista de trazas
  1,0–1,1: el mismo orden que en D-142 con el factor de la máquina.

Queda en la hoja de ruta: la serie del gráfico entra en la lista de lecturas que
cubrirían los preagregados, y sigue faltando medir con semanas de histórico.

### D-155 — El oscuro es la insignia; el claro, neutro
Sustituye la parte de D-133 que ponía el claro «Rose Gold & Amanecer» por defecto.

* **El oscuro por defecto.** Quien no ha elegido nada ve «Tech Abisal», tenga el sistema
  como lo tenga: es la cara del producto en capturas, demos y la primera apertura de
  `laplace ui`. Ajustes ofrece tres opciones, en este orden: Oscuro (sin nada guardado),
  Claro y Como el sistema. Lo que ya estaba guardado sigue valiendo: `light` y `dark`
  se leen igual; quien tenía «como el sistema» no tenía nada guardado y pasa al oscuro,
  que es justo el cambio pedido.
* **El claro, neutro.** El rosa hacía que el producto pareciera otro al cambiar de tema.
  Ahora el fondo es gris frío (ningún canal se separa más de un 6 % de los otros), el
  cristal casi blanco y los acentos son los del oscuro —violeta, cian, ámbar— con el
  mismo tono; como letra se oscurecen hacia su propio color hasta pasar AA, y como
  relleno (barras, el botón principal) se usan tal cual. El botón principal lleva letra
  oscura sobre el degradado en los dos temas.
* **El claro está escrito dos veces**, elegido a mano y «como el sistema» con el sistema
  en claro: CSS no deja nombrar un bloque de variables. Antes pasaba lo mismo con el
  oscuro, sin nada que vigilara que las dos copias no se separasen.
* **El contraste, medido en cada pasada.** `test_tema.py` lee los tokens de
  `globals.css`, resuelve `var()` y `color-mix()` como el navegador, compone el cristal
  sobre los tres puntos del degradado y exige 4,5:1 a toda tinta de texto (`--ink`,
  `--ink-2`, `--ink-3`, `--iris`, `--teal`, `--amber`, `--rose`, `--rose-ink`) y 3:1 a la
  decorativa, en los dos temas. Lo primero que encontró fue del **oscuro**, no del claro:
  la auditoría medía sobre el color del medio, y en la esquina iluminada del degradado,
  donde van el logo y el proyecto, el violeta de los enlaces daba 4,1:1 y la tinta
  decorativa 2,1. `--iris` en oscuro es ahora el violeta con un 12 % de blanco y
  `--ink-4` sube del 60 al 80 % de `--muted`. La prueba exige también que las dos copias
  del claro sean idénticas, que el fondo claro no tenga tinte, y comprueba que el claro
  rosa con el ámbar de antes de la auditoría no pasaría.
* **En pantalla**, `test_pantallas.py` abre Ajustes con cada combinación de lo guardado
  y del sistema y mide la luminancia del fondo pintado, no una clase: sin nada y el
  sistema en claro, oscuro; «como el sistema» con el sistema en claro, claro. Rompiendo
  a propósito el script del layout (sin «system»), falla.

### D-156 — El producto alrededor del ciclo: detectar, probar, arreglar, verificar
La cifra que vende Laplace es «ahorrado y recuperable», y un problema pasa de lo segundo
a lo primero recorriendo cuatro pasos. Las piezas existían —el hallazgo, el conjunto para
probarlo (sólo en el modelo caro), el botón de marcarlo y el antes y después (D-123)—,
pero ninguna pantalla decía en qué paso estaba cada cosa ni enseñaba la mitad
«ahorrado».

* **Evaluaciones pasa a ser «Probar»** y va en la barra justo después del Diagnóstico.
  La ruta sigue siendo `/evaluaciones`, para no romper enlaces guardados. Arriba,
  **«Arreglos por probar»**: los problemas abiertos de un paso con dinero (los seis que
  más devuelven), con lo que devuelven y si ya tienen su prueba (sin probar, guardado
  sin tirada, n tiradas); guardar sus ejecuciones crea el conjunto con las llamadas
  reales de ese paso. El Diagnóstico se pide aparte y la pantalla no lo espera: con
  volumen tarda segundos (D-154), y lo mismo en Prompts. Debajo, lo de siempre (A vs B, conjuntos, tiradas). Los textos
  que nombraban la pestaña cambian en los cinco idiomas, también en el backend.
* **La ficha dice dónde está el problema en el ciclo**: cuatro pasos con su estado
  (hecho, en curso, pendiente, no ha funcionado), sacado sólo de lo que se sabe. Probar
  está hecho si hay un conjunto guardado con el filtro de su paso (`source_filter.step_key`)
  y alguna tirada sobre él. Arreglar está hecho si se marcó. Verificar sale del
  seguimiento: hecho si lo ha dado por arreglado, en curso si espera ejecuciones o ha
  mejorado sin desaparecer, y no ha funcionado si reaparece. El estado va también en
  texto para lectores de pantalla, no sólo en el color de la marca.
* **Probar ya no es sólo del modelo caro.** Cualquier problema de un paso ofrece guardar
  sus llamadas reales y la línea para lanzarlas: con el modelo barato, con cada versión
  del prompt (D-157) o con el arreglo puesto. Si el conjunto ya existe, se dice en vez de
  ofrecer crearlo otra vez.
* **El héroe dice lo ya ahorrado**, debajo de lo que se puede dejar de pagar: «Ya has
  dejado de pagar X desde que arreglaste N problemas». Es la suma de `FixCheck.saved` de
  lo que el seguimiento ha verificado (`arreglado` o `mejor`, en dinero), con «al menos»
  si alguno salía de un coste que era un suelo. Lo pendiente o lo que reaparece no
  cuenta. En la demo: 2,64 $ de la repetición arreglada.
* **De paso:** las pestañas Probar, Panel, Prompts y Ajustes no salían marcadas nunca
  en la interfaz exportada, porque se comparaba la ruta exacta y la exportación añade
  «/» al final. Y la barra de móvil repartía tres tercios exactos y «Tableau de bord»
  no cabía a 375 px en Linux (el fallo de `test_pantallas` que sólo se veía fuera de
  Windows): cada columna mide ahora al menos lo que su pestaña.

Pruebas: `test_ajustes.py` (lo ahorrado suma sólo lo verificado; lo pendiente y lo que
reaparece, nada) y `test_pantallas.py` (el héroe con lo ahorrado, el ciclo en la ficha,
Probar empieza por los arreglos del Diagnóstico y la pestaña de cada pantalla sale
marcada, con Probar la segunda).

### D-157 — Prompts como fuente de hallazgos
La pestaña de Prompts sabía que la v2 de «atencion» cuesta un 60 % más por ejecución, y
el Diagnóstico, que es donde se decide qué arreglar, no se enteraba. Ahora es una regla
más (`insights/prompt_caro.py`, tipo `prompt_caro`):

* **Cuándo sale.** La versión que corre ahora (la de la última llamada vista) es más
  nueva que la anterior con tráfico y cuesta por ejecución más de un 5 % por encima
  (`MATERIAL_COST_CHANGE`, el de Evaluaciones), con al menos cinco ejecuciones en cada
  una (el mínimo de la comparación de Prompts; no se importa porque el panel importa el
  motor, y una prueba exige que sean iguales). Tras volver atrás, la que corre es la
  vieja y no hay nada que decir. La versión 0 (el texto de reserva del SDK) no cuenta.
* **Cuándo se calla.** Si alguna llamada de las dos versiones no tiene tarifa: una resta
  con un sumando desconocido no es un suelo, es otro desconocido. Y sin las tiradas de
  evaluación, como el resto de reglas: la lectura de las reglas es `prompt_usage(...,
  rules=True)`, con `RULES_WHERE` y con las `step_keys` de cada versión y una traza de
  ejemplo, en los dos almacenes y con paridad probada.
* **No contar dos veces el mismo dinero, sin callar de más.** Lo primero que se escribió
  restaba a la diferencia todo lo que otras reglas reclaman sobre las llamadas de la
  versión cara, y en la demo la regla salía muda: el modelo caro reclama 31 $ sobre esas
  llamadas y la diferencia es de 19. Pero los arreglos **se componen**: si cambiar de
  modelo abarata un 64 % esas llamadas, volver a la versión anterior ahorra después su
  diferencia a ese precio, no nada. La diferencia se escala por la parte del coste de la
  versión que no reclaman ya otras reglas; la suma de todo nunca pasa de lo que costaron
  esas llamadas. En la demo sale tercera, con 5,60 $.
* **Lo que no sabe:** si la versión cara acierta más. Los veredictos viven en la base de
  metadatos y el motor lee sólo trazas, así que no lo afirma en ningún sentido. El
  arreglo tiene tres pasos: comparar el acierto en Prompts (o probarlo con las mismas
  llamadas, D-156), volver a la anterior con un clic, o quedarse con lo que mejoró y
  quitar lo que encarece.
* **El id lleva la versión cara** (`prompt_caro:atencion:v2`): si después la v3 vuelve a
  encarecer, es otro problema y no hereda lo que se dijo de la v2. Volver atrás y
  marcarlo arreglado se verifica como cualquier otro hallazgo, sin código aparte.
* **En Prompts**, la ficha de un prompt con el problema abierto lo dice y enlaza a él; y
  la ficha del problema enlaza a Prompts, donde se vuelve a la versión anterior.
* **Discrepancia que queda, anotada en la hoja de ruta:** la pestaña de Prompts cuenta
  las tiradas de evaluación en el coste de cada versión y la regla no. En la demo, la
  comparación A/B de ayer corre con la v2 y el modelo barato, y abarata la v2 en
  Prompts: allí dice un 60 % más, aquí un 70 %.

Pruebas en `test_regla_prompt_caro.py`, en los dos almacenes: sale con la cifra medida;
no sale tras volver atrás, con la nueva más barata, con un 3 %, con pocas ejecuciones
en cualquiera de las dos, sin tarifa ni sólo en tiradas de evaluación; la reserva no
cuenta; lo reclamado por otra regla abarata la diferencia en proporción y nunca se pasa
del coste; la ficha dice lo mismo que la tarjeta; volver atrás se verifica; las dos
lecturas dan lo mismo en los dos almacenes. `test_catalogo_hallazgos.py` siembra también
un prompt encarecido y exige que tenga ficha y trazas. Rompiendo a propósito el
escalado, el filtro de evaluaciones o la guarda de tarifa, fallan.

## 2026-09-28 — El tema claro y el alto contraste

### D-158 — El claro vuelve a ser Rose Gold, con cristal líquido
Revierte la mitad de D-155 que cambió el claro por un gris neutro: el usuario lo vio y
el claro había perdido el cristal. Un cristal translúcido sólo se nota si detrás hay
color, y sobre un gris uniforme cada tarjeta parecía una caja blanca. El oscuro no cambia
y sigue siendo el tema por defecto.

* **Fondo de rubor y champán** con tres manchas de luz (`--bg-layers`: coral arriba a la
  derecha, oro a la izquierda y rosa abajo) sobre el degradado de siempre. El cristal es
  muy transparente (40 % de blanco) y lleva un filo de luz arriba y abajo y una sombra
  cálida (`--glass-sheen`). El desenfoque satura lo que tiene detrás
  (`blur(18px) saturate(170%)`), que es lo que hace que el color atraviese el cristal.
* **Acentos rose gold, coral y oro**, oscurecidos como letra hasta pasar AA. El ahorro
  no puede ser del mismo cobre que el derroche: en el claro es jade (`--teal` fijo),
  el complementario del rose gold, que se distingue del ámbar a simple vista.
* **El oscuro no se toca:** sus tokens nuevos no hacen nada (`--glass-sheen: 0 0 #0000`,
  una capa transparente) y el desenfoque es el de antes. La prueba lo exige.
* `test_tema.py` cambia la prueba de «el claro no es rosa» por la contraria: fondo de
  rubor, acento rose gold, cristal con transparencia y filo de luz, y manchas detrás.
  El contraste de toda la letra sigue medido en cada pasada.

### D-159 — Alto contraste, para el claro y para el oscuro
Un botón en Ajustes, debajo del tema, que se combina con cualquiera de los tres
(oscuro, claro o del sistema). Se guarda en el navegador (`laplace.contrast`) y lo
aplica el script del layout antes del primer pintado, como el tema (`data-contrast`).

* **Otro modo, no el mismo tema con más tinta.** Sin transparencias, sin desenfoque, sin
  resplandores ni tintes de fondo en etiquetas y círculos: negro puro o blanco puro,
  grises neutros, y sólo los tres colores que significan algo (ámbar lo que se tira,
  verde lo ahorrado, rosa lo que falla). En la primera versión del claro quedaban restos
  del rose gold —la barra de cada tarjeta empezaba en rosa, los círculos de la posición
  tenían fondo beige y los grises eran tostados— y el usuario lo vio como una
  inconsistencia; ahora la prueba exige que los tintes sean transparentes y que el acento
  principal del claro sea casi negro.
* **El mismo estilo en los dos: luxury, no tech.** La primera versión del oscuro de alto
  contraste llevaba el violeta y el cian del oscuro normal subidos de brillo, y al lado
  del claro sobrio se veía neón; el usuario pidió «dark luxury». Ahora es negro cálido
  (#0a0908), letra marfil, enlaces y botón en champán, lo ahorrado en jade claro y lo que
  se tira en oro. La prueba exige acentos de poca saturación y un negro que no tire a
  azul. Las casillas y los controles nativos toman el acento del tema en vez del azul
  del navegador.
* **Letra a 7:1 como mínimo (AAA)** y la tinta decorativa a 4,5, medidas igual que en los
  temas normales. El botón principal va en negro con letra blanca en el claro, y al
  revés en el oscuro.
* **Los enlaces de texto se subrayan** (no las tarjetas ni los botones, que ya tienen su
  borde) y el contorno del foco pasa a 3 px.
* El claro de alto contraste también está escrito dos veces (elegido y del sistema), y
  la prueba exige que las copias sean iguales. En pantalla, `test_pantallas.py` lo
  enciende desde el botón en los dos temas, mide que el fondo sea negro cálido o blanco y
  que la barra no desenfoque, recarga, y lo apaga.

## 2026-09-28 — Tres decisiones que estaban abiertas

### D-160 — Prompts sin tiradas de evaluación, envoltorios con «=» y el modelo rápido ponderado
Tres cosas que `STATUS.md` y el repaso de D-157 dejaron para decidir con el usuario. Se
decidieron a favor de la recomendación de cada una.

* **La pestaña de Prompts ya no cuenta las tiradas de evaluación.** El coste, las
  ejecuciones y el acierto de cada versión son los de su tráfico real, como en las
  reglas. En la demo, la comparación A/B de ayer corre con la v2 y el modelo barato:
  abarataba la v2 en Prompts (un 60 % más cara que la v1) y no en el Diagnóstico (un
  70 %). Ahora las dos dicen lo mismo. Se excluyen el coste (`prompt_usage(rules=True)`),
  los veredictos de las trazas de una tirada (`prompt_versions_by_trace(...,
  sin_evaluaciones=True)`, porque una anotación del juez sobre una tirada no es del
  tráfico) y los prompts observados sin gestión (`observed_prompts(rules=True)`). La
  comparación de tiradas en Probar sigue leyéndolas, que es de lo que habla.
* **Un envoltorio del árbol enseña «=» en vez de repetir las cifras de su hijo.** Un
  paso con un solo hijo, sin llamada al modelo propia y con el mismo coste y los mismos
  tokens —el span de un `@observe` alrededor de una llamada— sigue en el árbol, porque es
  el paso del usuario y a quien depura le interesa verlo, pero sus columnas dicen «=»,
  con el motivo al pasar el ratón y para lectores de pantalla. Antes el dinero se leía
  dos veces. `esEnvoltorio()` en `lib/tree.ts`, probada con Node.
* **El modelo rápido que se propone se elige ponderando por llamadas.** Un modelo corre
  en varios pasos, cada uno con su mediana, y se juntaban con la media simple: un paso de
  cinco llamadas lentas pesaba lo mismo que otro de cinco mil rápidas, y la
  recomendación podía salir de una muestra suelta. Ahora es la media de las medianas
  ponderada por llamadas, y a igualdad decide el nombre, no el orden de las filas.

Pruebas: `test_prompts.py` (una tirada barata y anotada como fallo no mueve ni el coste
ni el acierto de la v8; rompiendo cualquiera de las dos exclusiones, falla),
`test_regla_prompt_caro.py` (las dos lecturas nuevas, iguales en SQLite y ClickHouse),
`test_arbol_envoltorio.py` y `test_modelo_rapido.py`.

## 2026-09-28 — Fase 6: margen por cliente

### D-161 — Qué clientes te hacen perder dinero
La primera función nueva, elegida por el usuario: lo que te paga cada cliente frente a lo
que te cuesta su trabajo, al mes, y el aviso de quién te hace perder dinero. Laplace sabe
lo que cuesta cada ejecución; faltaba saber de quién es y cuánto paga.

* **`customer_id` de punta a punta.** `laplace.set_context(customer_id="acme")` en el SDK,
  el atributo `laplace.customer.id` en el contrato, la ingesta, y una columna nueva en los
  dos almacenes (tardía en los dos: `COLUMNAS_TARDIAS` en SQLite y `ADD COLUMN IF NOT
  EXISTS` en ClickHouse, para las instalaciones que ya existen). No es el usuario: una
  empresa cliente tiene muchos usuarios, y el margen es por quien factura. El coste de
  cada cliente es el de sus ejecuciones enteras, con la lectura de siempre del Panel
  (`cost_by`, que ya agrupaba por usuario y por sesión) y una dimensión más.
* **Lo que paga cada uno lo pone el usuario**, en dólares al mes, desde la propia pestaña
  (la hoja de ruta decía Ajustes; al lado de su coste se entiende mejor lo que se está
  escribiendo). Se guarda como ajuste del proyecto (`revenue:<cliente>`). Desde Stripe,
  después. Un cliente con ingresos y sin ejecuciones en el rango se sigue enseñando, y se
  puede poner lo que paga uno que todavía no ha llegado.
* **Las reglas de siempre.** El coste al mes es la proyección del héroe, sobre los días de
  datos del proyecto, así que suma lo mismo; sin un día de datos no se compara con lo que
  pagan al mes y no se afirma que nadie pierda. Con llamadas sin tarifa el coste es un
  suelo y el margen un techo: «te deja como mucho», y «pierdes al menos», que sigue
  siendo cierto con más coste. El trabajo sin cliente se cuenta aparte, nunca repartido.
* **Sin tiradas de evaluación.** La primera versión las contaba, y en la demo la
  comparación A/B de ayer heredaba el cliente del último `set_context` y se le cobraba a
  él: 55 ejecuciones de más. Arreglarlo en el SDK no bastaba —el agente puede fijar un
  `customer_id` dentro de la tirada—, así que el margen lee `cost_by(..., rules=True)`,
  como las reglas y como Prompts desde D-160. Una prueba del arreglo no es trabajo de
  ningún cliente.
* **Cuatro estados, con el umbral escrito:** pierde (margen negativo), ajustado (menos
  del 20 % de lo que paga, `MARGEN_AJUSTADO`: cualquier cambio de modelo o de tráfico lo
  pasa a pérdidas), deja margen, y sin ingresos. Los que pierden van primero.
* **La pantalla**, pestaña «Clientes» después de Panel: el aviso en rosa arriba («Este
  cliente te hace perder dinero», con su frase), la tabla con coste, lo que paga
  (editable) y margen al mes, lo que no tiene cliente aparte y la línea para decir de
  quién es cada ejecución. **El Diagnóstico lo avisa en su carril**, por encima del
  presupuesto, sólo cuando hay alguno.
* **La demo** reparte sus usuarios en cuatro empresas y les pone ingresos como múltiplo
  de su coste real, para que salgan las cuatro historias pase lo que pase con los
  precios: el que más trabajo da (iberviajes) no cubre su coste, uno ajustado, uno que
  deja margen y uno sin ingresos puestos.

Pruebas en `test_margen.py`: el cliente viaja del SDK a la traza; coste por cliente y lo
que no tiene cliente aparte, en los dos almacenes; el margen y el aviso; ajustado; sin un
día de datos no se compara; sin tarifa el margen es un techo; un cliente sin tráfico no
desaparece; sin clientes se dice; una tirada que fija cliente no se le cobra; y la API
pone y quita ingresos. Rompiendo la proyección o el suelo, fallan. En pantalla, el aviso
en Clientes y en el carril del Diagnóstico, y la séptima pestaña cabe en móvil.

### D-162 — Lo que quedaba del margen por cliente: sus ejecuciones, sus problemas, la alerta y Stripe
* **De un cliente a sus ejecuciones.** El explorador filtra por `customer_id` (en los dos
  almacenes, por trazas como el usuario y la sesión: el cliente lo lleva la raíz), y cada
  cliente de la pestaña lleva su «Ver sus ejecuciones».
* **Qué problemas del Diagnóstico pasan en sus ejecuciones.** El aviso decía «mira qué
  problemas pasan en sus ejecuciones» y había que buscarlos a mano. Ahora cada cliente
  lleva los tres que más devuelven, cruzados por el paso igual que la vista de traza
  (`customer_steps`: por cliente, los pasos que recorren sus ejecuciones y cuántas veces
  como mucho en una; una repetición o un bucle sólo cuentan si el paso sale más de una
  vez). Salen del mismo Diagnóstico del inicio, con su caché y sus estados: lo arreglado
  o ignorado no se le atribuye a nadie. En la demo todos los clientes comparten los
  mismos problemas, porque es el mismo agente para todos; con agentes que hacen cosas
  distintas por cliente, la lista cambia.
* **La alerta**, por los canales de siempre y en el mismo mensaje: «N clientes han
  pasado a hacerte perder dinero», **una vez por cliente** cuando pasa a perder. Mientras
  sigue perdiendo no vuelve a sonar —se le toca `seen_at` en cada vuelta para que la
  limpieza de `decide()` no lo olvide y suene cada periodo de calma—; cuando se recupera
  se olvida, y si recae vuelve a sonar. Se silencia como una regla más
  (`cliente_pierde`) y sin ingresos puestos no dice nada.
* **Ingresos desde Stripe** (`stripe_ingresos.py`). Una clave secreta o restringida por
  proyecto, guardada con los ajustes y devuelta sólo como sus cuatro últimos caracteres.
  «Traer de Stripe» lee las facturas pagadas de los últimos 30 días, con el cliente
  expandido y paginando entero. Tres decisiones:
  * **A qué cliente de las trazas va cada factura:** `metadata.laplace_customer_id` del
    cliente de Stripe, o su id (`cus_…`). No por correo ni por nombre.
  * **Al mes de verdad:** cada línea se lleva a un mes por el periodo que cubre (un plan
    anual de 1.200 $ son 100 al mes); un cargo sin periodo cuenta entero.
  * **Sin convertir monedas:** lo que no está en dólares no se suma con un tipo
    inventado; se cuenta aparte y se dice al traerlo.

  Lo puesto a mano para un cliente que Stripe no conoce se queda; lo que Stripe conoce
  lo pisa la factura; lo que venía de Stripe y ya no sale se quita. Cada ingreso dice de
  dónde sale («Stripe» en la tabla). La traída es a mano, con un botón: sin salida a
  internet la instalación sigue funcionando con ingresos escritos. **No se ha probado
  contra la API real** —aquí no hay salida a `api.stripe.com` ni clave—: las pruebas van
  contra una Stripe falsa con la forma documentada de las facturas.

Pruebas: `test_margen.py` (pasos por cliente en los dos almacenes, problemas por cliente,
la alerta una vez, silenciada y sin ingresos; quitando el toque de `seen_at`, falla),
`test_stripe.py` (cliente y mes de cada factura, otra moneda, paginación, lo manual que se
queda, la clave que no se devuelve y la que Stripe rechaza; sin llevar al mes, falla) y
`test_pantallas.py` (del aviso a las ejecuciones del cliente).

De paso: la prueba de pantalla de D-157 buscaba el problema del prompt entre las tres
tarjetas visibles del Diagnóstico, y su puesto depende de la hora a la que se carga la
demo (según la hora sale un 53 % o un 70 % más caro, tercero o cuarto). Ahora despliega
la lista entera antes de buscarlo: era la prueba la que dependía del reloj, no el
producto.

### D-163 — Stripe se trae solo cada día, y el cliente desde Node sin SDK
Lo que quedaba de la Fase 6 sin necesitar una clave.

* **La traída diaria.** Con la clave puesta, el bucle de fondo —el de las alertas, que ya
  tiene turno entre procesos— trae los ingresos de un proyecto si la última traída fue
  hace más de un día, y lo hace **antes** de evaluar sus alertas, para que la de
  clientes que pierden dinero mire lo que pagan hoy. Corre aunque el proyecto no tenga
  canal de alertas. Si Stripe falla, se apunta en el log y se reintenta en la siguiente
  vuelta: lo último traído sigue valiendo. El botón de la pestaña sigue ahí para traerlo
  en el momento.
* **El cliente desde un agente en Node.** No hace falta el SDK de TypeScript: es el
  atributo `laplace.customer.id` en el span que envuelve la ejecución, que la ingesta ya
  leía (D-161). La guía de TypeScript lo explica y está **probado con un agente en Node de
  verdad** (OpenTelemetry 2.11 y el exportador JSON 0.222 contra `laplace ui`): tres
  ejecuciones de dos clientes llegan con su cliente y su coste. Además, una prueba de
  OTLP/JSON en la suite lleva ese atributo hasta el margen.
* **Lo que sigue esperando:** probar la traída contra la API real de Stripe (hace falta
  una clave de pruebas) y el paquete fino `@laplace/sdk` con su `setContext({
  customerId })`, que espera el nombre del scope en npm.

Pruebas: `test_stripe.py` (se trae una vez al día y no antes; si Stripe falla, lo último
sigue valiendo; el bucle de fondo lo trae) y `test_otlp_json.py`.

## 2026-09-28 — Restos de fases cerradas: las integraciones sin probar

### D-164 — `client.beta.*` en Python: las llamadas que no se veían
La hoja de ruta lo daba por «sin probar» y el README lo reconocía («no se ven las
llamadas por `client.beta.*`»). Al probarlo contra los SDK de verdad (anthropic 1.9,
openai 3.20) había dos huecos, y los dos eran llamadas que no existían para Laplace:

* **`client.beta.messages` de Anthropic** es otra clase
  (`anthropic.resources.beta.messages.Messages`), que no hereda de la normal y va
  directa a la red. Todo lo nuevo de Anthropic se pide por ahí (gestión de contexto,
  compactación, servidores MCP, el modo rápido), y quien lo usa manda **todo** su
  tráfico por esa puerta: su agente salía sin coste ni hallazgos. Ahora se parchean
  `create`, `parse` y `stream` en las dos, síncronas y asíncronas. `count_tokens` no:
  no genera nada ni se factura como una llamada, y hay una prueba que lo exige.
* **El gestor de `beta.messages.stream()`** guarda la petición en otro atributo privado
  (`_BetaMessageStreamManager__api_request`). El nombre ya no se fija a mano: se busca
  por la jerarquía del gestor con la regla de deformación de nombres de Python. Si una
  versión lo mueve, se sigue avisando en el log.
* **`client.beta.responses.create` de OpenAI** también es otra clase, y se añade a las
  puertas. `client.beta.chat.completions` es la misma clase que `chat.completions` y ya
  estaba cubierta; una prueba lo fija para enterarnos si cambia.
* **Lo que sigue fuera, dicho en el README:** de `client.beta` de OpenAI, las Assistants
  (`threads.runs`), Realtime, ChatKit y los agentes alojados, que no pasan por estas
  puertas.

Ninguna puerta nueva cuenta dos veces: `span_llm()` exige exactamente un span por
llamada. Visto de paso y sin tocar: sin streaming, la salida de Anthropic se guarda como
la lista de bloques (con `citations` y el resto), y en streaming como el texto
acumulado; la misma llamada queda con dos formas según cómo se haga.

Pruebas: `test_proveedores_beta.py` (15, con los clientes reales y el transporte
falso). Sin el arreglo fallan 10, y si la búsqueda del atributo se salta la clase del
gestor fallan las de `stream()`, las beta y las normales.

### D-165 — Las integraciones de TypeScript que faltaban, probadas: y lo que se perdía
Anthropic por OpenInference-js y por OpenLLMetry-js, el AI SDK de Vercel y LangChain.js
estaban en la guía como «deberían llegar, nadie lo ha comprobado». Se han probado con
agentes de Node de verdad contra `laplace ui` y un proveedor falso con la forma
documentada de cada API (sin clave ni gasto). Los tokens, la caché y el coste llegaban
bien por las cuatro vías. Lo demás, no:

* **El AI SDK de Vercel 7 no mandaba nada.** La versión 7 ya no lee
  `experimental_telemetry`, que es lo que decía la guía: hace falta
  `registerTelemetry(new OpenTelemetry())` de `@ai-sdk/otel` y `telemetry` en cada
  llamada. La guía lo explica ahora.
* **Las convenciones GenAI actuales se leían a medias.** Vercel 7 y OpenLLMetry-js
  mandan los mensajes en `parts` (no en `content`) y el prompt de sistema **aparte**, en
  `gen_ai.system_instructions`. Los mensajes se guardaban tal cual —la interfaz los
  enseñaba vacíos y las reglas que miran el texto no tenían nada que mirar— y el prompt
  de sistema se perdía, con él la mitad de la identidad del paso: dos pasos con la misma
  pregunta caían en uno. La ingesta pasa ahora cada mensaje en `parts` a la forma del SDK
  de Python (la de OpenAI): el texto a `content`, las llamadas a herramientas a
  `tool_calls` y su respuesta a un mensaje `tool`; lo que no es texto ni herramienta
  (una imagen) se queda en `parts` al lado. Las instrucciones aparte se unen como primer
  mensaje de sistema si no hay ya uno. Nunca se pisa un mensaje que ya trae `content`, y
  para saber si hay `parts` se busca la cadena antes de parsear nada: los spans de
  nuestro SDK no pagan el cambio.
* **OpenInference-js con Anthropic deja el `system` en `llm.invocation_parameters`**, no
  entre los mensajes: se une igual (cadena o bloques de texto). Además se leen su
  `llm.response.model_name` y su `llm.finish_reason`, en singular.
* **La respuesta de LangChain.js con `ChatOpenAI` 1.6 llegaba sin texto.** Va por la
  Responses API, su contenido es una lista y la instrumentación de OpenInference sólo
  manda el rol. El texto está en `output.value`, en `generations[i][0].text`, y se saca
  de ahí si los mensajes de salida no traen ninguno.
* **Lo que no es nuestro y se deja dicho en la guía:** LangChain.js 1.5, en streaming con
  Anthropic, cuenta un token de salida de más (suma el de `message_start` al total), y
  OpenLLMetry no manda el motivo de parada en streaming.

El banco queda en `scripts/integraciones_js`, con las versiones fijadas y un
`verificar.py` que exige a cada llamada modelo, tokens con la caché dentro, coste
medido, el prompt de sistema y la respuesta en texto. Contra la ingesta de antes da 21
fallos, y contra la de ahora, ninguno. Sigue sin probarse contra las API reales, y
LangGraph.js, el Agents SDK de TypeScript y Mastra no se han probado.

Pruebas: 10 nuevas en `test_convenciones.py`, con los atributos copiados de las trazas
de verdad. Entre ellas, una con un span que sólo trae `parts` y nada más que lo delate
(como la instrumentación oficial de OpenTelemetry para OpenAI), porque sin ella nada
fallaba al romper la detección.

## 2026-09-29 — Escala: dos semanas de histórico

### D-166 — Con semanas de histórico, una ventana de un día lee el mes entero
La hoja de ruta dejaba por decidir, a falta de medir con semanas de histórico, dos
cosas: acotar por tiempo las subconsultas de `_where` y el `FINAL` con partes sin
fusionar. Medido en un contenedor de 4 núcleos y 16 GB, con `scripts/carga.py`, que
ahora genera varios días (`--dias`, `--dias-atras`). Son 10 millones de spans al día
(el proyecto grande, la mitad), primero con un día guardado y después con catorce.

**Lo que empeora sin cambiar la ventana:**

| Proyecto grande, ventana de 1 día | 1 día guardado | 14 días guardados |
|---|---|---|
| Diagnóstico | 9–11 s | 15–19 s |
| Panel | 2,4 s | 7,8 s |
| Filas leídas por un Diagnóstico | 112 millones | **590 millones** |

El Diagnóstico de 7 días, con esos 7 días llenos, tarda 100 s y el Panel 46 s. La lista
de trazas de 7 días, 16 s. Abrir una traza sigue en 0,03 s. En disco son 10,9 GB para
140 millones de spans (12×).

**La causa no es ninguna de las dos que se sospechaban: es la clave de ordenación.** La
tabla se ordena por `(project_id, trace_id, span_id)`, y los `trace_id` son aleatorios,
así que cada gránulo mezcla trazas de todos los días. En cuanto las partes se fusionan
(al medir quedaban 8 activas), el índice `minmax` de `start_time` ya no descarta nada, y
cada consulta de una ventana recorre casi todo el histórico del proyecto en la
partición mensual. Con retención de 30 días, una ventana de un día cuesta como el mes.

**Comprobado con una copia.** Se hizo una muestra del proyecto grande (una de cada cuatro
trazas, 17,5 millones de spans, 14 días), guardada en dos tablas idénticas salvo la clave
y fusionadas en una parte cada una. Mismas consultas, con la forma de las del almacén:

| Ventana de 1 día | `(project_id, trace_id, span_id)` | `(project_id, toDate(start_time), trace_id, span_id)` |
|---|---|---|
| Resumen con `FINAL` | 17,5 M filas, 0,34 s | 1,24 M filas, 0,05 s |
| Repeticiones por traza | 17,5 M, 0,44 s | 1,24 M, 0,10 s |
| Lista de errores, subconsulta como hoy | 35 M, 0,53 s | 18,8 M, 0,25 s |
| Lista de errores, subconsulta acotada | 30 M, 0,43 s | 2,5 M, 0,06 s |
| Abrir una traza (con proyecto / sólo id) | 5.016 filas, 78 / 30 ms | 10.042 filas, 42 / 28 ms |

**Lo que se decide con esto:**

* **Acotar por tiempo las subconsultas de `_where`: sólo junto con la clave nueva.** Con
  la de hoy no sirve de nada (35 → 30 M filas); con la nueva divide por siete.
* **`FINAL`: no es el problema.** Con la clave nueva, el resumen con `FINAL` lee lo mismo
  que la ventana.
* **La propuesta es ordenar por día, sin tocar la partición mensual.** La ventana de un
  día lee 1/14 de lo que lee hoy con dos semanas guardadas, y el coste pasa a crecer con
  la ventana, no con el histórico. Abrir una traza no empeora, porque ahí manda el índice
  bloom de `trace_id`. Un span reenviado conserva su `start_time`, así que
  `ReplacingMergeTree` lo sigue deduplicando.
* **Sigue sin hacer y espera al usuario:** el esquema nuevo y la migración de las
  instalaciones que ya existen. Hay que crear la tabla con la clave nueva, copiar los
  datos por particiones y cambiarlas con `EXCHANGE TABLES`. Es una operación larga sobre
  datos de clientes y se hace con permiso, no de paso. Con ella, acotar las subconsultas
  de `_where` por la ventana.
* **Los preagregados por hora (D-143) quedan detrás.** Con la clave nueva, el Diagnóstico
  de un día tendría que volver a la escala de la medida de un día guardado, y hay que
  medirlo otra vez antes de decidir si hacen falta.

## 2026-09-29 — Funciones diferenciales: el replay contrafactual

### D-167 — Probar el modelo barato reenviando las llamadas reales, sin escribir código
El paso «probar» del ciclo (D-156) pedía guardar las ejecuciones de un paso y escribir
una función que corriese el agente sobre cada caso con el modelo nuevo. Para la pregunta
más común —«¿el modelo barato respondería igual en este paso?»— no hace falta el agente:
basta con reenviar **las mismas llamadas** que hizo el paso, con los mismos mensajes, al
modelo barato, y comparar las respuestas. Es lo que hace `laplace replay`.

* **Corre en el SDK, con las claves del usuario** (decidido con el usuario). El backend
  dice qué llamadas se pueden reenviar (`GET /api/datasets/{id}/replay`); las reenvía el
  cliente de OpenAI o Anthropic del proceso del usuario, con la clave de su entorno.
  Laplace no guarda claves de proveedor ni gasta dinero de nadie, que es D-086 aplicado a
  algo que no es ejecutar el agente pero se le parece. La otra opción, un botón que
  reenviase desde el backend, era mejor experiencia a cambio de custodiar claves.
* **Qué se reenvía.** Sólo lo que no puede tener efectos ni cambiar de sentido al
  reenviarse:
  - llamadas **hoja** del paso del conjunto;
  - **sin herramientas**, ni declaradas, ni pedidas en la respuesta, ni resultados de
    herramienta entre los mensajes;
  - con **todos los mensajes en texto y con rol**, lo que también deja fuera los mensajes
    que el SDK recortó por tamaño;
  - que salieron **bien** y con **respuesta**;
  - que **no son de una tirada**.

  Lo demás se cuenta por motivo y se enseña al pedir permiso. De los parámetros se
  llevan `temperature`, `top_p` y `max_tokens`, y los dos primeros sólo si el SDK
  instalado los acepta: `anthropic` 1.x ya no tiene ninguno de los dos en
  `messages.create`, y pasarlos hacía fallar cada llamada (lo encontró la prueba con el
  cliente real).
* **El tope se cumple por construcción.** Antes de cada caso se suma lo peor que puede
  costar: la entrada original con un 30 % de margen, porque otro tokenizador puede contar
  más, y la salida máxima, que es la fijada por el original o cuatro veces su respuesta,
  entre 256 y 4.096. Si con eso se pasaría, no se hace. La salida máxima va también en la
  petición, así que el proveedor no puede pasarse de ella. Un caso se reenvía entero o no
  se reenvía: medio caso compararía menos llamadas en un lado que en el otro. Sin tarifa
  del modelo de destino no hay tope que cumplir, y no se reenvía nada.
* **Con permiso.** Antes de gastar se enseña cuántas llamadas son, lo que costaron (medido),
  lo que costarán si el modelo responde lo mismo y lo peor. Desde la línea de órdenes se
  pregunta; `--si` se lo salta, y sin nadie delante y sin `--si` no se gasta nada.
* **Una comparación justa.** Quedan dos tiradas del conjunto:
  - la **original**, que no gasta nada: apunta a las llamadas reenviadas dentro de las
    trazas reales, con los `span_ids` nuevos de cada caso de una tirada;
  - la del **modelo nuevo**, con sus trazas, marcadas como tirada de evaluación y fuera de
    las reglas.

  Sin los `span_ids`, el lado original habría costado el agente entero y el nuevo un solo
  paso. El coste de un caso limitado a unos spans se suma con los spans de su traza, y un
  span que ya no está cuenta como coste desconocido, así que el total pasa a ser un suelo
  y no una cifra más baja que parece exacta. `cost_key` separa en la comparación la misma
  traza entera y limitada. Los `span_ids` se guardan en los dos almacenes de metadatos,
  con migración.
* **El juez, contra la respuesta de esa llamada.** `POST /api/judge` acepta la referencia
  de cada traza. En un paso intermedio, como clasificar un ticket, la salida final del
  agente no dice nada de si el paso respondió igual; la respuesta original de la llamada,
  sí. Si el juez está apagado, se dice que la comparación sólo tendrá el coste.
* **En la interfaz**, la ficha de un problema de modelo caro y «Arreglos por probar» dan
  la orden de `laplace replay` con el modelo barato que propone el hallazgo, en vez del
  código de `run_dataset`. El texto dice que corre en tu máquina, con tu clave y un tope,
  en los cinco idiomas. Los demás arreglos siguen con `run_dataset`, porque no cambian de
  modelo.

Pruebas:
- `test_replay.py` (10): qué se reenvía y por qué no lo demás, la tarifa, el coste de la
  tirada original limitado a sus spans, un span que falta hace del coste un suelo, y el
  juez con la referencia por traza.
- `test_replay_sdk.py` (11): de punta a punta con el backend en memoria y los clientes
  reales de OpenAI y Anthropic con el transporte falso: lo que llega al proveedor, las dos
  tiradas y su comparación, el tope, sin permiso no se gasta, sin tarifa no se reenvía, un
  caso que falla no corta, las instrucciones a `system` en Anthropic, el juez y la línea
  de órdenes.
- `test_pantallas.py`: la ficha del modelo caro da la orden de `laplace replay`.
- `test_evals.py`: los dos almacenes de metadatos devuelven los mismos `span_ids`,
  probado contra Postgres de verdad.

Cada garantía se ha roto a propósito y alguna prueba falla. El margen del 30 % no
mordía, porque la prueba del tope calculaba lo peor con la misma función, y tiene ahora
una prueba con las cifras escritas a mano.

**Lo que no hace:**
- no reenvía llamadas con herramientas, que exigirían ejecutarlas y eso es correr el
  agente;
- no convierte imágenes ni bloques entre proveedores;
- no se ha probado contra las API reales, porque espera la clave de la sección 5 de la
  hoja de ruta.

## 2026-09-29 — Escala: la tabla ordenada por día

### D-168 — La clave de ordenación por día, su migración y los filtros acotados
Lo que D-166 midió y dejó por decidir, decidido con el usuario y hecho.

* **La clave nueva.** Las instalaciones nuevas crean `spans` con `ORDER BY (project_id,
  toDate(start_time), trace_id, span_id)`. La partición sigue siendo mensual. Un span
  reenviado conserva su `start_time` y se sigue deduplicando, y hay una prueba que lo
  exige. `recalcular_coste` reinserta los spans con el mismo instante, así que tampoco se
  duplican.
* **La migración de las que ya existen, a mano** (`python -m
  laplace_backend.storage.migrar_orden`, que sin `--hacerlo` sólo dice qué haría). El
  backend avisa al arrancar si la tabla tiene la clave vieja, pero no migra solo: es una
  copia entera, y una migración a medias en el arranque sería peor que ninguna.
  - **La copia:** va a una tabla nueva, partición a partición y día a día. Si se corta,
    se vuelve a lanzar: una partición que ya cuadra se salta y una a medias se tira entera.
  - **La ingesta no se para.** Lo que llega mientras se copia se recoge al final por su
    `ingested_at`, y otra vez justo después del `EXCHANGE TABLES`, que es atómico.
  - **El TTL de retención se vuelve a poner**, porque no pasa con `CREATE TABLE ... AS`.
  - **Sitio:** comprueba antes que hay disco para una segunda copia.
  - **La tabla vieja no se borra**: queda como `spans_antes_d168` hasta que alguien la
    borre.

  Probada a escala con 10 millones de spans creados con la clave vieja: tardó 6,5 minutos
  en este contenedor (3,5 de ellos copiando) y cuadran los 10 millones de spans distintos.
* **Los filtros por traza acotados a la ventana**, con una hora de margen por cada lado,
  en los dos almacenes. Con la clave vieja no servía de nada (D-166); con la nueva, sí. La
  lista de trazas ya resumía sólo los spans de la ventana, así que filtrar con los de la
  ventana es coherente con lo que se enseña. El margen es para la traza cuyo span raíz, el
  que lleva la sesión, el usuario y el cliente, empezó justo antes: sin él no se
  encontraría por ellos.

**Medido con 14 días guardados** (10 millones de spans al día, el mismo contenedor de 4
núcleos que en D-166):

| Proyecto grande | Clave vieja | Clave por día |
|---|---|---|
| Diagnóstico, ventana de 1 día | 15–19 s, 590 M filas | **5,2–6,5 s, 175 M filas** |
| Panel, 1 día | 7,8–8,2 s | 3,4–3,6 s |
| Lista de errores, 1 día | 2,2 s | 0,6 s |
| Diagnóstico, 7 días | 101–107 s | 55–66 s |
| Panel, 7 días | 46–50 s | 23–26 s |
| Lista de trazas, 7 días | 16–18 s | 10–12 s |
| Abrir una traza | 0,03 s | 0,03 s |

Con un día guardado y la clave vieja, el Diagnóstico de un día tardaba 8,5–10 s. Ahora,
con catorce días guardados, tarda **menos que eso**: el coste ya sigue a la ventana y no
al histórico, que era lo que había que arreglar.

**Lo que sigue sin cumplirse, y qué pide.** El objetivo de 1,5 s queda lejos en este
contenedor, que es unas tres veces más lento que el portátil de D-142:
- un día con 5 millones de spans en el proyecto son unos 5 s;
- siete días son casi un minuto, porque de verdad hay que recorrer 35 millones de spans.

Ya no es un problema de lo que se lee de más, sino de lo que hay que leer. Es lo que
resuelven los preagregados por hora de D-143, que ahora tienen el número que les faltaba.

Pruebas:
- `test_migrar_orden.py` (6, contra ClickHouse de verdad y en una base propia): una
  instalación nueva nace con la clave; la migración conserva todo y cambia la clave;
  deja la vieja; vuelve a poner el TTL; no pierde lo que llega mientras copia ni lo que
  llega justo antes del cambio de nombre; rehace una copia a medias sin dejar filas de
  más; y un span reenviado se sigue deduplicando.
- `test_paridad.py`: una traza que cruza el borde de la ventana se sigue encontrando
  por sesión y por estado, igual en los dos almacenes.

Cada garantía se ha roto a propósito y alguna prueba falla. Tres no fallaban al
principio, por culpa de la prueba, y se corrigieron:
- los datos sembrados llevaban el `ingested_at` de ahora, así que la puesta al día del
  final los recogía todos y tapaba cualquier fallo de la copia;
- el span que «llegaba durante la copia» entraba antes de copiarse su día;
- la segunda puesta al día tapaba a la primera por su margen.

### D-169 — `/health` nombra el almacén que hay, y la limpieza
**`/health` ya no afirma lo que no ha comprobado.** En modo local contestaba
`clickhouse: true, postgres: true`: era la salud de SQLite con los nombres de la nube.
Ahora dice qué hay (`store`: `clickhouse` o `sqlite`; `metadata`: `postgres`, `sqlite` o
`none`) y si responde (`store_ok`, `metadata_ok`). Las claves `clickhouse` y `postgres`
siguen, para no romper a quien las lea, pero valen `null` donde ese almacén no existe.
Nadie de dentro las leía: el CI y el orquestador sólo miran el 200.

Prueba: `test_local_mode.py::test_la_salud_en_local_no_dice_que_hay_clickhouse`. Muerde:
con las claves nuevas pero `clickhouse: store_ok` como antes, falla (`True is None`).
La otra mitad, `test_la_salud_en_la_nube_nombra_clickhouse_y_postgres`, llama a `/health`
con ClickHouse y Postgres de verdad (se salta si no están) y exige que los nombre y diga
que responden. Sin el cambio, falla.

**La limpieza.** Fuera lo que nadie usaba, buscado con vulture y knip y comprobado con
grep en todo el repo, pruebas incluidas:
- Python: `se_puede_afirmar`, `enviar_trazas_de_ejemplo`, `safe`,
  `PriceTable.unverified_models` y `alerts.destino_inseguro` (sólo la llamaba una prueba,
  que ahora usa `resolver_destino`).
- Web: `backendReachable`, `getDataset`, `Tokens`, `EvalRun`, `EvalRunItem` y
  `DatasetItem`, y el `export` de una treintena de piezas que sólo se usan en su fichero.
  Siguen exportados `moneyExact`, `miles` y `CATALOGOS`: los importan las pruebas espejo
  de Node, aunque knip no lo vea.
- SDK: `pytest-asyncio`, que no se usaba. Se queda `EvalRunResult.url_hint` aunque nadie
  la lea: es API pública del paquete.
- `ANALISIS.md`, la auditoría ya cerrada, a `docs/auditoria-producto.md`.

**La suite entera en Windows**, con ClickHouse y Postgres: 953 bien, 4 saltadas, 20 fallos
y 2 errores. Ninguno venía de la limpieza:
- Las 19 de pantalla fallaban contra una `apps/web/out` de antes del último pull, sin
  `/clientes`. Reconstruida (`scripts/build_ui.py`), pasan todas.
- `test_otlp_json` borraba su base SQLite con la conexión abierta, y Windows no deja.
  Ahora usa `tmp_path`, y pasa.
- Las 2 de búsqueda por contenido en ClickHouse daban error: un `ALTER` se quedaba sin
  tiempo esperando el bloqueo de `spans`, con millones de spans de la prueba de carga en
  la base local. Con ellos la suite tardó más de dos horas en lugar de minutos. Quitados con
  `scripts/carga.py --borrar`, pasan.

## 2026-09-30 — Integraciones de TypeScript: LangGraph.js, el Agents SDK de OpenAI y Mastra

### D-170 — Los tres frameworks que faltaban, probados; y lo que Mastra no manda donde toca
Probados con agentes de Node contra `laplace ui` y el proveedor falso del banco
(`scripts/integraciones_js`), como los de D-165. Contra las API reales sigue sin
probarse: en este entorno no hay clave de proveedor.

* **LangGraph.js** (`@langchain/langgraph` 1.4.18, con la instrumentación de LangChain de
  OpenInference): llega todo sin tocar nada. Un grafo de dos nodos con el mismo prompt de
  sistema sale como dos pasos, porque el nodo es el sitio del paso (D-141). El banco lo
  exige.
* **El Agents SDK de OpenAI** (`@openai/agents` 0.18.0, con
  `@arizeai/openinference-instrumentation-openai-agents` 0.2.15): llega todo, con `run` y
  en streaming. La instrumentación lee las instrucciones del agente de la **respuesta**,
  que en la API real las devuelve; el proveedor falso no lo hacía y ahora sí. Era un
  hueco del banco, no de Laplace.
* **Mastra** (`@mastra/core` 1.72.0 con `@mastra/otel-exporter` 1.4.3): tokens, caché y
  coste llegaban bien. Tres cosas no:
  - **Los mensajes de entrada llegaban vacíos.** Mastra no los pone en su span de la
    llamada al modelo (`model_inference`), sino en el paso que la envuelve
    (`mastra.model_step.input`) y en la generación de encima. Sin ellos se perdía el
    prompt de sistema, y con él la identidad del paso: dos agentes con instrucciones
    distintas caían en uno. La ingesta los toma ahora del padre si está en el mismo lote,
    que es lo normal, porque el paso termina justo después de la llamada. Primero lee las
    claves y sólo parsea los spans que las tienen, y nunca pisa los mensajes que el span
    ya traiga. Si el padre llega en otro lote, la llamada se queda sin mensajes, como
    antes.
  - **El motivo de parada llegaba como la cadena `'["stop"]'`** en lugar de un array; se
    lee como lista.
  - **El proveedor venía con la API detrás** (`openai.chat`), como en el AI SDK
    (`openai.responses`, `anthropic.messages`): se queda con lo de antes del punto.

El banco pasa ahora por siete integraciones y 21 llamadas. Contra la ingesta de antes,
las de Mastra fallan por el prompt de sistema.

Pruebas: 5 nuevas en `test_convenciones.py`, con los atributos copiados de la traza de
verdad. Cada arreglo se ha roto a propósito y alguna prueba falla. La que exige no pisar
los mensajes propios no mordía, porque su span no estaba marcado como `model_inference`,
y se corrigió.

## 2026-09-30 — Deuda: los detalles sueltos

### D-171 — Ocho detalles de la lista de deuda, cerrados con su prueba
* **`laplace demo` en la consola de Windows.** La consola escribe en cp1252 y los textos
  llevan tildes y «comillas»: salían rotos. La línea de órdenes pide UTF-8 a su salida
  antes de escribir nada (`test_cli.py`).
* **El pie del gráfico de gasto por día** enseñaba la hora aunque el tramo fuera un día
  entero. `inicioDeTramo` sólo pone la hora a los tramos de menos de un día, en el
  Diagnóstico y en el Panel (`test_idioma.py`, con Node).
* **Un coste largo se salía de su caja en el grafo del agente.** La segunda línea de la
  caja se estrecha hasta el ancho con `textLength` cuando no cabe; el texto entero sigue
  en el `title`. La prueba de pantalla mide cada texto contra su caja.
* **La salida de Anthropic tenía dos formas.** Sin streaming se guardaban los bloques
  (con `citations`), y con él el texto. Ahora es el texto si todos los bloques son de
  texto, como con OpenAI; con una llamada a herramienta se guardan los bloques enteros.
* **Las tiradas de la demo salían fechadas hoy** aunque sus trazas fueran de ayer. Una
  tirada es de cuando corrió: la fecha del comienzo de su primera traza, nunca en el
  futuro (`test_evals.py`).
* **La cifra del problema del prompt de la demo cambiaba con la hora de la carga**, del
  53 % al 82 % según la medida. Tres causas, cada una con su arreglo:
  - los hitos del guion caían en periodos de 24 horas contados desde ahora; ahora son
    días de calendario;
  - el primer día del mes quedaba cortado por la ventana a la hora de la carga; ahora se
    generan días enteros dentro de ella;
  - el pico —alguien probando un modelo caro— contaba para la v2 y el tráfico de hoy lo
    diluía más o menos; ahora esas llamadas van fuera del prompt gestionado.

  A cualquier hora dice lo mismo: «un 26 % más», que es la diferencia real entre las dos
  versiones del prompt (`test_demo_estable.py`, a las 1, 9, 17 y 23 h). De paso, la demo
  garantiza tráfico en las dos últimas horas; antes lo daba la suerte de la semilla, y se
  acabó al mover el primer día.
* **La API de la lista de trazas recorría todo el histórico** si no se le pasaba ventana
  (D-008b). Sin `since`, usa los últimos 30 días; lo de antes se pide con `since`. Buscar
  una traza por el principio de su id sigue mirando todo, porque abre una concreta.
* **Al cargar se pedían dos veces `/api/projects` y `/api/auth/me`.** Las peticiones
  iguales en curso a la vez se hacen una sola vez. No es una caché: al llegar la
  respuesta se olvida, y la cancelación sigue siendo de cada llamante. La prueba de
  pantalla cuenta las peticiones.

Cada prueba falla sin su arreglo.

### D-172 — La guardia de las reglas nuevas
`test_catalogo_hallazgos` exigía ficha y trazas a cada tipo de hallazgo, pero nada
exigía que una regla nueva desambiguara su título (D-115) ni que entrara en el
descuento del doble conteo (D-117); la de Prompts (D-157) se hizo mirándolo a mano.

* **Estructural:** `GARANTIAS`, en `test_catalogo_hallazgos.py` (no en el motor: es texto
  para quien añade la regla, y en el motor lo tomaba por frase suelta
  `test_sin_frases_sueltas`), dice para cada `FindingKind`
  cómo se distingue su título y cómo evita reclamar dinero que ya reclama otra regla. La
  prueba exige una entrada por tipo: una regla nueva sin ella pone la suite en rojo el
  mismo día, como una sin ficha (D-113).
* **De comportamiento, sobre un mes de la demo**, con todas las patologías a la vez:
  - ningún par de hallazgos se titula igual;
  - lo que las reglas reclaman en un paso no pasa de lo que costó;
  - lo que las demás reclaman sobre las llamadas de la versión cara de un prompt, más
    lo que reclama la regla de prompts, no pasa de lo que costaron esas llamadas.

  Quitar el descuento de las reglas sobre trazas, o el de la de prompts, pone la suite en
  rojo. El título se desambigua en su prueba propia, con dos llamantes del mismo nombre,
  porque la demo no los tiene.

### D-173 — Retención por proyecto, y borrar lo de una persona o un cliente
`LAPLACE_RETENTION_DAYS` valía para toda la instalación (D-009 pedía por proyecto), y no
había forma de borrar los datos de un usuario final o de un cliente concreto, que es lo
que pide el cliente de un cliente.

* **Retención de un proyecto** (`GET` y `PUT /api/retention`, y en Ajustes): sus días se
  guardan en los ajustes del proyecto. La aplica el bucle de fondo, que ya tiene turno
  entre procesos, una vez al día, como la traída de Stripe (D-163). Manda la más corta
  entre la del proyecto y la de la instalación: un proyecto no puede guardar más de lo
  que la instalación permite. En ClickHouse, con la clave por día (D-168), el borrado
  sólo toca las partes con días viejos.
* **Borrar una persona o un cliente** (`DELETE /api/subjects`, y en Ajustes, repitiendo el
  id para confirmar): se lleva las trazas **enteras** en las que aparece, no sólo los
  spans que llevan el id. El id lo lleva la raíz, y dejar las llamadas hijas sería dejar
  justo su contenido. Queda anotado en la auditoría de la organización cuando hay
  cuentas.

Pruebas en `test_retencion.py`, en los dos almacenes:
- se borran las trazas enteras;
- la retención de un proyecto no toca otro;
- manda la más corta;
- el bucle de fondo la aplica una vez al día y no antes;
- la API pide confirmar.

Cada garantía se ha roto a propósito y alguna prueba falla. También una prueba de
pantalla en Ajustes.

**El fallo antiguo de la pantalla de Prompts** (`test_pantallas.py`, «no cargó en 15 s»),
explicado: no era lentitud, porque el Diagnóstico de la demo tarda 0,5 s en local. Si el
problema del prompt aparecía o no en la demo dependía de la hora de la carga (D-171), y
sin él el aviso de Prompts no sale nunca. Con la demo estable, `test_demo_estable` exige
que aparezca a cuatro horas distintas.

### D-174 — `globals.css` en 29 hojas, un índice de decisiones y documentación de usuario
* **`globals.css`** (4.100 líneas) se parte en `apps/web/app/estilos/`, una hoja por
  pantalla o pieza (`00-temas.css` a `28-ciclo.css`), que `layout.tsx` importa en orden.
  El orden importa: la cascada de antes se conserva, y el CSS compilado sale igual que
  el de antes salvo los saltos de línea entre ficheros. La primera regla `.hint`, pisada
  por la segunda, era código muerto y se quita. Las pruebas que leían `globals.css`
  (`test_tema.py`, `test_textos_web.py`) leen ahora las hojas en el orden de
  `layout.tsx`, con `hoja_de_estilos()` de `helpers.py`, así que una hoja nueva que no
  se importe no se cuela en la cuenta.
* **Índice por tema de `DECISIONS.md`** (`docs/decisiones-indice.md`): lo genera
  `scripts/indice_decisiones.py` a partir de las palabras de cada título, y
  `test_decisiones_indice.py` exige que esté al día. Generado y no escrito a mano,
  porque uno a mano se desfasa a la primera decisión nueva.
* **Documentación de usuario** en `docs/usuario/`, en Markdown plano: empezar,
  instrumentar, diagnóstico, probar, alertas y datos. Sin Mintlify, Docusaurus ni
  Starlight todavía: GitHub ya la pinta, y montar un sitio pide el dominio (sección 5 de
  la hoja de ruta). Se pasa a uno de ellos cuando haya dónde publicarlo.

### D-175 — Las pantallas con sesión, recorridas en un navegador
La auditoría del rediseño no llegó a las pantallas que sólo existen con cuentas.
`test_pantallas_cuentas.py` las recorre como una persona, escribiendo en los formularios,
sobre `laplace ui` con `LAPLACE_AUTH_REQUIRED=true`:

1. configurar la instalación;
2. la organización;
3. crear una clave;
4. invitar;
5. aceptar la invitación desde otro navegador;
6. entrar con la contraseña mala y con la buena;
7. entrar con la clave.

A cada pantalla se le exige lo mismo que a las demás: sin errores en la consola y sin
salirse por los lados a 1440 ni a 375 px. Cuando algo se sale, la prueba dice qué
elemento es. Encontró tres fallos:

* **La organización se salía 12 px en el móvil**: sus tres tablas (miembros, claves y
  registro) no llevaban el `tbl-scroll` que llevan las de las otras pantallas.
* **Quien entraba con la clave de un proyecto recién creado leía «Todavía no hay ningún
  proyecto»**, con un `init` de ejemplo para `project="mi-agente"`: justo el código que
  no tenía que copiar. `/api/projects` sólo listaba proyectos con datos. Ahora añade, a
  cero y al final, los que la identidad tiene nombrados (el de su clave, o los de sus
  organizaciones) y todavía no han mandado nada. Un admin de la instalación, que lo ve
  todo, no recibe una lista inventada.
* **Ese `init` de ejemplo no llevaba `api_key`**, y con cuentas la ingesta sin clave da
  401: parecía que no llegaba nada. En modo nube lleva `api_key="lp_…"`.

Las variantes de ClickHouse de `test_retencion.py` corren ahora en una base propia que
se tira al acabar, como `test_migrar_orden.py`. En la base compartida, el borrado ligero
reescribe las partes enteras en las que caen las trazas de la prueba. Con la carga de
escala delante, esas partes pesaban gigas y la prueba fallaba por falta de disco, no por
el código.

### D-176 — El contraste, medido sobre cada pantalla
`test_tema.py` mide cada tinta contra el cristal compuesto sobre el degradado, a partir
de los tokens (D-155). No ve lo que sólo existe en pantalla: los resplandores de detrás
del cristal, el fondo teñido de un chip o de una insignia, la opacidad de un
antepasado. `test_contraste_pantallas.py` mide lo que se pinta de verdad:

1. apunta cada trozo de texto visible, con su color compuesto con la opacidad de sus
   antepasados;
2. vuelve el texto transparente y hace una captura;
3. calcula el contraste contra los píxeles que quedan debajo de cada trozo.

Exige AA al percentil 10: 4,5:1, o 3:1 en letra grande. Se usa el percentil y no el
mínimo, porque en la caja de un texto caen bordes que no son su fondo. Corre en las
siete pantallas, en los cuatro temas, a 1440 px. La captura se lee en un `canvas` del
propio navegador: no hace falta Pillow.

Primera pasada: los dos temas de alto contraste pasaban en todo. Fallaban 45 textos
entre 3,5 y 4,49:1, casi todos en el claro, y todos de seis tintas:

| Tinta | Antes | Después |
|---|---|---|
| `--amber` (claro) | 42 % | 32 % de `--accent-3` hacia `#3a2400` |
| `--teal` (claro) | `#1d6a58` | `#14513f` (y su línea y su fondo) |
| `--iris` (claro, enlaces de las tarjetas) | 52 % | 40 % |
| `--rose` (claro) | `#a8283a` | `#962233` |
| `--muted` (claro) | `#6a504d` | `#604643` |
| `--muted` (oscuro, el título «Presupuesto» sobre el resplandor) | `#91a1b5` | `#9eaec2` |

Los tonos se mantienen y sólo se oscurecen (en el oscuro, se aclara). El chip «en
producción» de Prompts usaba su propia mezcla de fondo, más fuerte que la de los demás
chips verdes, y pasa a usar `--teal-bg`.

La prueba descarta lo que no se pinta. En la primera versión, los textos dentro de un
`<details>` cerrado daban 1,1:1: tienen cajas, pero no se ven. Ahora se filtran con
`checkVisibility()`.

### D-177 — Preagregados por minuto del Diagnóstico y clave por hora
El objetivo de la hoja de ruta era un Diagnóstico de 1,5 s para un día de un proyecto con
diez millones de spans al día. Con la clave por día (D-168) tardaba 6,5 s en el
contenedor de 4 núcleos. Se midió consulta a consulta antes de cambiar nada:

- Eran diez barridos de la ventana, uno por lector, a 0,5–1,4 s cada uno.
- **Lanzarlos a la vez no sirve**: 6,5 s en serie y 6,7 s en paralelo, porque ClickHouse
  ya satura los núcleos con uno solo. Había que leer menos.
- **La exclusión de las tiradas de evaluación** (`trace_id NOT IN (subconsulta)` en
  `RULES_WHERE`) comprobaba cada `trace_id` contra un conjunto casi siempre vacío:
  0,2–0,35 s por consulta. Ahora se buscan antes con un índice `bloom_filter` sobre
  `tags` y se pasan como lista, o nada si no hay ninguna (`_consulta`), con
  `test_reglas_evaluaciones.py` para los tres caminos.
- **Preagregados** (`storage/preagregados.py`, esquema en `clickhouse_schema.sql`). Son
  parciales por minuto en cuatro tablas:
  - `pre_pasos`: por paso, modelo y prompt, con sumas y estados para las medianas;
  - `pre_trazas`: por traza, con sus grupos como hashes, para contar ejecuciones
    distintas;
  - `pre_repes` y `pre_bucles`: sólo las parejas que pueden llegar a repetirse, que son
    las de dos o más en su hora y las de trazas que cruzan la hora.

  Se recalculan **por horas enteras desde `spans FINAL`, sustituyendo** al cálculo
  anterior. Así un lote reenviado y `recalcular_coste`, que vuelven a insertar spans, no
  cuentan dos veces: era la trampa escrita en la hoja de ruta.

  Cada escritura apunta sus horas en `pre_sucias`, con la hora del servidor de después
  de escribir. Una hora con un apunte posterior a su cálculo, o sin calcular, se lee en
  crudo **con la misma selección de parciales**, así que cada lector es una sola
  agregación y no hay dos cuentas que mantener.

  Un bucle de fondo recalcula lo pendiente cada 30 s. La hora en curso la deja hasta que
  acaba o lleva dos minutos quieta: mientras llegan datos se ensucia enseguida, y se lee
  en crudo igual.
- **Contar ejecuciones distintas fusionando estados de `uniqExact`** por minuto costaba
  más de 1 s por lector (18.000 estados pequeños). Se cuentan en `pre_trazas`, con
  `ARRAY JOIN` de los grupos de cada traza y `uniqExact` sobre filas estrechas: 0,15 s.
  `groupBitmap` era más lento aún (3,5 s). `uniqCombined64` era rápido, pero aproximado.
- **La clave por hora** (`ORDER BY (project_id, toStartOfHour(start_time), trace_id,
  span_id)`). Con la del día, leer en crudo la hora en curso, o los segundos del borde de
  la ventana, costaba el día entero, y eso es lo que se lee en cada consulta mientras
  llegan datos. `migrar_orden` migra desde las dos claves anteriores, y ahora pide 1,6
  veces la tabla libre: con 1,2 dejó empezar una copia de 148 millones de spans que no
  cabía, porque la copia recién escrita ocupa más que la tabla fusionada.
- La ventana de la API empieza en minuto entero; el final sigue siendo ahora.

**Medido** con el proyecto grande (10 millones de spans al día, 15 días guardados) en el
mismo contenedor:

| | Antes (D-168) | Con esto |
|---|---|---|
| Diagnóstico, 1 día | 6,5 s | **1,1 s** |
| Diagnóstico, 7 días | 55–66 s | 6 s |
| Recalcular una hora | — | 0,4 s |

En 7 días pesan los recuentos exactos de ejecuciones distintas y las medianas.

**Una diferencia con el crudo, dicha.** Una traza cuenta como tirada de evaluación si
lleva la etiqueta en un span a menos de una hora de la hora que se calcula; antes, si la
llevaba dentro de la ventana. Sólo cambia con una tirada de más de una hora que cruce el
borde de la ventana.

**Pruebas** (`test_preagregados.py`, en una base de ClickHouse propia): un mes de la demo
por la ingesta de verdad, con una de cada quince ejecuciones marcada como tirada de
evaluación. Se exige que el Diagnóstico entero y cada lector salgan iguales con y sin
preagregados:

- sin calcular y calculado, en ventanas de 30, 7 y 1 día y en una a destiempo;
- después de un reenvío y de un cambio de coste, antes y después de recalcular;
- después de borrar un cliente;
- con una repetición y un bucle partidos por el borde de una hora;
- y que la hora en curso no se recalcule mientras llegan datos.

Cada garantía se ha roto a propósito y alguna prueba falla: sin las trazas que cruzan la
hora, una hora sucia leída como limpia, el cálculo sin `FINAL`, las tiradas sin excluir
y el crudo sin quitar lo que ya sale de las tablas. La de las tiradas no fallaba al
principio, porque la demo no trae ninguna; ahora la prueba las pone.

Las cifras son de un ClickHouse con sólo el proyecto grande. La migración del conjunto
entero (148 millones de spans de seis proyectos) no cabía en el disco del contenedor, y
con el proyecto delante en la clave los demás no entran en sus lecturas.

## 2026-10-01 — Funciones: caché compartida, margen por cliente, diagnóstico con modelo, seguridad

### D-178 — El mismo prefijo en varios pasos, sin compartir la caché
La regla del contexto fijo mira cada paso por separado y supone, por prudencia, que la
caché no sobrevive de una ejecución a la siguiente. Un paso que se llama una vez por
ejecución no tiene nada que reutilizar. Pero un agente llama a menudo a varios pasos
**con las mismas instrucciones** desde sitios distintos del código, y dentro de una
ejecución la caché del proveedor les podría servir ese prefijo a todos menos al primero.
Paso a paso no se veía.

* **La huella del prefijo** (`prefix_hash`, contrato §8): la mitad «con las mismas
  instrucciones» de la identidad de paso (D-060), sin el sitio. La calcula la ingesta. Se
  guarda en los dos almacenes, en `ModelUsage` y en los preagregados (D-177). Las tablas
  de preagregados de antes ganan las columnas, y sus horas calculadas se marcan sucias
  para recalcularlas.
* **La regla** (`cache_compartida`) agrupa los usos por (prefijo, modelo) cuando hay dos
  pasos o más. Las ejecuciones distintas se piden aparte (`prefix_traces`): no son la
  suma de las de cada paso, porque una ejecución que llama a dos pasos cuenta una vez.
  - Lecturas que encontrarían la caché caliente: prefijo × (llamadas − ejecuciones).
  - Se quitan las que el contexto fijo ya reclama dentro de cada paso.
  - Sólo cuenta la parte del prefijo que no se sirve ya de caché, y si esa parte es
    menor que el 35 % no dice nada: OpenAI cachea sola los prefijos idénticos.
  - Se resta una escritura por ejecución.
  - Si a esos pasos se les recomienda un modelo más barato, se tarifa sobre ése, como
    el contexto fijo.
* **Un hallazgo de varios pasos** reparte su dinero entre ellos en proporción a sus
  llamadas (`Finding.step_shares`). Con eso suman igual lo reclamado por paso
  (`reparto`), la franja evitable del gráfico y el descuento de la regla de prompts, y
  la prueba de que ninguna regla reclama más de lo que costó el paso sigue valiendo.

Pruebas en `test_regla_cache_compartida.py`, en los dos almacenes:
- la huella no depende del sitio y cambia con las instrucciones;
- el dinero exacto del caso de libro (dos pasos, una vez cada uno por ejecución) y su
  reparto;
- el descuento cuando un paso se repite dentro de la ejecución;
- que no dispare con un solo paso, con prefijos distintos ni cuando el proveedor ya
  cachea el prefijo;
- que la ficha diga lo mismo que la tarjeta.

El catálogo siembra el tipo nuevo y le exige ficha y `GARANTIAS`, y la prueba de fuego
de D-177 compara también `prefix_traces`. Se rompieron a propósito el descuento, el
filtro de lo ya cacheado y el recuento de ejecuciones distintas, y alguna prueba falla.
El filtro no mordía al principio, porque la escritura ya se comía el ahorro del caso de
la prueba; ahora la prueba usa cuatro pasos.

**La demo no lo enseña**: ninguno de sus pasos comparte instrucciones con otro, y
añadirle esa patología movería cifras que fijan otras pruebas. Queda en la hoja de ruta.

### D-179 — Margen por cliente: lo evitable de cada uno, otras monedas y su columna
Lo que la Fase 6 (D-161 a D-163) dejó para después.

* **Cuánto de lo evitable es de cada cliente.** Antes se decía qué problemas pasaban en
  sus ejecuciones, no cuánto dinero suyo tiraban. Ahora cada problema se reparte entre
  los clientes en proporción a lo que gastó cada uno en el paso del problema (o en sus
  pasos, si abarca varios: `step_shares`, D-178). Es el mismo criterio de reparto que el
  gráfico del Diagnóstico (D-152), y la pantalla lo dice. El total de cada paso incluye
  el trabajo sin cliente, que no se le da a nadie, así que lo de los clientes nunca
  suma más que el problema (`customer_step_costs`, en los dos almacenes).
  - Cada cliente lleva lo evitable suyo en la ventana y al mes, y cada problema de su
    lista, su parte.
  - Con margen, también el que quedaría arreglándolos: «pierdes 120 $ al mes;
    arreglando sus problemas (45 $ al mes evitables) seguirías perdiendo 75 $».
  - Al mes se proyecta sobre la misma base que su coste, o el margen después de
    arreglar no querría decir nada.
* **Ingresos en otra moneda.** Lo que paga cada cliente se guarda en su moneda, y el
  proyecto tiene sus tipos de cambio (`/api/exchange-rates`, dólares por unidad), que
  pone el usuario. El margen convierte con ellos. Una moneda sin tipo no se convierte
  con uno inventado: el cliente sale como «falta el tipo de cambio» y la frase dice
  cuál. Las alertas de clientes que pierden dinero convierten igual.
  - Stripe guarda a cada cliente en la moneda en que factura, y sólo pasa a dólares,
    con esos tipos, al que factura en varias. Antes toda factura que no fuera en
    dólares se descartaba; ahora sólo las de una moneda sin tipo de un cliente con
    varias, y se dice.
* **La columna de cliente** en la lista de trazas, con su enlace al filtro por cliente,
  y en su CSV. Sólo aparece si alguna fila de la página lleva cliente: quien no usa
  `customer_id` no ve una columna vacía.

Pruebas (`test_margen_mas.py`, en los dos almacenes donde hay almacén):
- el reparto por cliente, y que lo de los clientes no pase del problema;
- el reparto de un problema de varios pasos;
- lo evitable de cada cliente sobre tráfico de verdad, con su margen arreglado;
- una moneda sin tipo y con tipo;
- la moneda y los tipos por la API, con sus validaciones;
- Stripe con monedas;
- el cliente de cada traza en la lista.

`test_pantallas.py` exige la columna de lo evitable con cifras y la de cliente en la
lista. Se rompieron a propósito el trabajo sin cliente en el reparto y la conversión
sin tipo, y alguna prueba falla. Las de Stripe y el margen que esperaban importes sin
moneda se han adaptado al cambio.

### D-180 — Diagnóstico con modelo, que cita los spans de los que sale
El hueco que el contrato reservaba desde la Fase 0 (`trace_diagnoses`,
`Trace.diagnosis`) ya se llena. Un modelo lee la traza y dice qué falló o qué sobró, con
las reglas de siempre:

* **Cada afirmación cita sus spans, y el servidor lo comprueba.** El prompt pone cada
  span con su id entre corchetes. El modelo devuelve sus afirmaciones con los ids que
  las sostienen. Una afirmación que cita un id que no está en la traza se tira entera,
  aunque cite otros buenos: no sabemos qué parte sale de la traza. También se tira la
  que no cita nada. Se cuentan las tiradas y la pantalla lo dice. Sin ninguna
  afirmación en pie no se guarda nada (422), y el diagnóstico anterior se queda.
* **El modelo no pone dinero.** `estimated_savings_usd` queda vacío; lo que se ahorraría
  lo calculan las reglas con la tabla de precios.
* **Lo que cuesta, medido** con la misma tabla y guardado en el diagnóstico, como el
  juez (D-088). La pantalla lo enseña.
* **Apagado por defecto.** Usa el proveedor del juez, y se enciende aparte con
  `LAPLACE_DIAGNOSIS_ENABLED`. `/api/judge` dice si está encendido, y la ficha de la
  traza sólo ofrece el botón entonces.
* **La traza es dato**, entre etiquetas `<dato>` como en el juez (D-130), y con topes:
  600 caracteres de entrada y salida por span, 150 spans y 900 tokens de respuesta. Un
  diagnóstico no puede costar más que el agente que diagnostica.
* **Acotado al proyecto**: se guarda y se lee por `(project_id, trace_id)` en SQLite y en
  Postgres. Un trace_id de otro proyecto da 404.

En la ficha de la traza, cada cita es un botón con el nombre del paso. Al pulsarlo, el
árbol selecciona ese span y abre sus antecesores.

Queda fuera, y se dice:
- un diagnóstico rechazado también cuesta, y ese coste no queda apuntado en ningún sitio;
- el borrado de un sujeto (D-118) no borra los diagnósticos, y sus afirmaciones pueden
  citar contenido. Borrar el proyecto sí los borra.

Pruebas (`test_diagnostico_modelo.py`, con un proveedor falso):
- se quedan las afirmaciones bien citadas, también con corchetes, y se tiran y cuentan
  las inventadas, las mezcladas y las vacías;
- el coste sale de la tabla;
- sin nada sostenido no hay diagnóstico;
- un `</dato>` del usuario no se sale de su bloque;
- se guarda en los dos almacenes, acotado al proyecto;
- por la API: apagado (503), de otro proyecto (404) y un rechazo que no pisa el
  anterior (422).

`test_pantallas.py` exige que la ficha enseñe las afirmaciones, las tiradas y el coste,
y que la cita seleccione su span. Se rompieron a propósito la comprobación de ids, la de
las citas mezcladas y la selección de la cita, y alguna prueba falla.

### D-181 — Redacción de datos personales y muestreo por cola, en el SDK
Las dos cosas pasan **en el proceso del usuario, antes de mandar nada**, en un
procesador (`laplace/_filtro.py`) que va delante del exportador de Laplace y sólo en
ese camino. Si la aplicación tenía su propio proveedor con otros exportadores, lo que
mandan ellos no cambia. Sin `redact` y sin `sample_rate`, el procesador no existe.

**Redacción** (`init(redact=True)` o `LAPLACE_REDACT`):
* Hay detectores para correos, teléfonos, tarjetas, IBAN, IP y claves de API (OpenAI,
  AWS, GitHub, Slack, JWT, `Bearer`).
  - Las tarjetas pasan por Luhn y el IBAN por el módulo 97.
  - Una tarjeta pide al menos 14 cifras y no todas iguales: un instante en
    milisegundos o un relleno de ceros no lo son.
  - Una fecha, un recuento de tokens o una versión tampoco son un teléfono.
  - Son conservadores a propósito. Lo que no pillen se cubre con un patrón o una función
    propia en la misma lista.
* **La marca lleva una huella** (`[email:3f2a9c1d]`). Con `[email]` a secas, dos
  peticiones que sólo se distinguen por el correo serían la misma. Eso daría
  repeticiones falsas, y pasos partidos o juntados, porque la identidad de paso sale del
  texto de las instrucciones (D-060).
  - La huella es un HMAC con `LAPLACE_REDACT_KEY`, o con la API key, así que no se
    deshace probando correos de un diccionario.
  - Sin ninguna de las dos, la clave es aleatoria por proceso, y un paso con un dato
    personal en las instrucciones se partiría entre procesos. Se dice en el README.
* **Se redacta todo menos la estructura.** Una lista explícita de prefijos se queda como
  está:
  - modelos, tokens, nombres de herramienta y de paso, tarifas y prompts gestionados;
  - los identificadores que pone el usuario (`user_id`, `customer_id`): el margen casa
    por ellos con Stripe, y quien los pone, los pone a propósito.
  - También se redactan los eventos de excepción y la descripción del estado de error.
* **Si redactar falla, el span sale sin el contenido**, con
  `laplace.redaction.failed`. Mandar un dato que el usuario pidió quitar es peor que
  perder un prompt. Es la única excepción a «el SDK nunca cambia lo que pasa si falla».

**Muestreo por cola** (`init(sample_rate=0.1)`):
* **Se decide al acabar la traza.** Al empezar no se sabe si va a fallar ni lo que va a
  gastar. Los spans se guardan en memoria hasta que acaba la raíz local.
* **Siempre se queda** con:
  - las trazas con un error (estado o evento de excepción);
  - las que pasan de `sample_keep_tokens` (20 000, entrada más salida de toda la
    traza) o de `sample_keep_ms`;
  - las de evaluaciones y replays, sin las que Probar no compara.
* **De lo demás, una de cada `1 / rate`**, elegida por el `trace_id` como
  `TraceIdRatioBased`. Dos servicios de la misma traza deciden lo mismo.
* **«Cara» son tokens y no dólares**: el SDK no tiene la tabla de precios, y los tokens
  son lo que se cobra.
* **Para que no crezca sin límite**, una traza que lleva 5 minutos sin cerrarse, que
  pasa de 5 000 spans o que sobra de 2 000 abiertas se manda entera, sin muestrear. Al
  cerrar el proceso se manda lo pendiente. Ante la duda se guarda, que es lo que había
  antes. Un span que acaba después de su raíz sigue la suerte de su traza.
* **Lo que se queda por azar lleva `laplace.sample.rate`** en todos sus spans. La
  ingesta lo guarda como `sample_rate` en los dos almacenes; un valor que no sea un
  número ≥ 1 vale 1.

**Lo que se dice**: con trazas muestreadas en la ventana, el Diagnóstico pone una
línea antes del dinero. Dice cuántas llegaron por azar, a cuántas representan y cuánto
costaría lo que no llegó (su coste por `rate − 1`). Se calcula en `coverage()`, en los
dos almacenes. No sube el nivel de la cobertura: muestrear es una decisión, no un fallo.

**Las cifras no se escalan.** Los totales, el ahorro y las reglas siguen siendo los de
lo que llegó. Escalar por el peso tocaría unas 85 sumas y 60 recuentos en dos almacenes
y sus preagregados, y la regla del producto es no dar una cifra que no se ha medido.
Como lo que falla y lo caro llega entero, lo que falta es justo lo barato y lo sano. Por
eso las cifras de abajo son un suelo, y la línea dice cuánto.

Pruebas (`test_redaccion_muestreo.py`):
- cada detector, y lo que no tienen que confundir;
- la marca estable, distinta para otro valor y dependiente de la clave;
- detectores a elegir y propios;
- de punta a punta por OTLP: el dato no sale, la estructura y el cliente no cambian, el
  paso y la repetición se siguen viendo;
- el fallo de la redacción;
- el muestreo con normales, fallidas, caras y evaluaciones, determinista y entero;
- el hijo tardío y el cierre;
- la validación de `init`;
- la ingesta;
- lo que dice el servidor, en los dos almacenes.

Se rompieron a propósito el error como motivo para guardar, la huella, la lista de
estructura, el vaciado al cerrar, la decisión de los tardíos, el `rate − 1` y el
relleno de ceros, y alguna prueba falla. La del cliente no mordía con un cliente sin
datos personales; ahora el cliente de la prueba es un correo.

### D-182 — Verificación de correo, SSO por OpenID Connect y SCIM
Lo que pide una empresa antes de dar de alta a su equipo, sobre las cuentas de D-127.

**Verificar el correo.**
* Un enlace de un solo uso, que caduca a los 2 días y se guarda como hash. Verifica el
  correo **al que se mandó**: si el de la cuenta cambia, ya no vale.
* Aceptar una invitación que llegó por correo también verifica, porque quien abre el
  enlace lee ese buzón. Una invitación que se pasó a mano, no.
* **Lo que exige estar verificado** es que Laplace mande correo en tu nombre. Una
  invitación que dice «fulano te ha invitado» con un fulano sin verificar serviría para
  suplantar a cualquiera desde nuestro servidor. Sin verificar, el enlace sale igual,
  para mandarlo a mano, y la respuesta dice por qué.
* Sin servidor de correo no hay cómo verificar. La interfaz lo dice y no ofrece el
  botón.

**SSO por OpenID Connect, no SAML.** Okta, Entra ID, Google Workspace, OneLogin, Auth0,
Keycloak y JumpCloud hablan OIDC, y OIDC se hace bien sin dependencias (`sso.py`, con
`urllib` como el juez). SAML se queda fuera a propósito: verificar una firma XML a mano
es justo donde se rompen los inicios de sesión (*signature wrapping*), y hacerlo bien
pide `xmlsec`, una biblioteca nativa que no queremos en cada instalación. Queda en la
hoja de ruta para quien lo necesite de verdad.
* **Flujo de código con secreto de cliente y PKCE.** El navegador nunca ve un token.
* **La firma del `id_token` no se comprueba, y el estándar lo permite** (OIDC Core
  3.1.3.7, punto 6). El token llega directamente del endpoint de tokens por TLS, en una
  petición que el servidor hace con su secreto. Por eso se exige `https` en el emisor y
  en los dos endpoints. Sí se comprueban:
  - el emisor, contra el documento de descubrimiento, que a su vez tiene que ser del
    emisor configurado;
  - la audiencia, y `azp` si hay varias;
  - la caducidad y el `iat`, con dos minutos de holgura;
  - el `nonce`.
* **`state` de un solo uso y atado al navegador**: en la base y en una cookie
  `httpOnly` de ese navegador. Sin la cookie, otro sitio podría terminar un inicio de
  sesión que empezó él y meterte en su cuenta. Un intento con un `state` bueno y otro
  navegador lo gasta. El `next` sólo puede ser una ruta propia.
* **Los dominios los pone quien administra la instalación**, no la organización. Un
  dominio es la afirmación de que esos correos son de esa organización, y no se la
  puede hacer uno mismo. No se aceptan dominios públicos (gmail.com y compañía), ni uno
  que ya sea de otra organización. El correo que manda el proveedor tiene que ser de
  uno de esos dominios, y un `email_verified: false` explícito se rechaza. Entra ID no
  lo manda: ahí el dominio aprobado es la garantía.
* **Nadie entra en la cuenta de otra organización.** Quien entra por SSO:
  - si ya vino antes, se le reconoce por su `sub` y no por el correo, que puede cambiar,
    y sólo si sigue en la organización: a quien un admin quita, no vuelve a entrar solo;
  - si tiene cuenta con ese correo, se enlaza **sólo si ya es miembro** de la
    organización. Si no, el proveedor de una organización entraría en la cuenta de
    alguien de otra;
  - si no tiene cuenta, se le crea, sin contraseña, verificado y con el rol por defecto
    que ponga la organización (nunca propietario).
* **Obligar a SSO**: los miembros con correo de los dominios de la organización no
  entran con contraseña. Se comprueba después de la contraseña, así que no dice qué
  cuentas lo tienen. El administrador de la instalación queda fuera: si el proveedor
  se cae, es quien puede quitar la obligación. Sin dominios no se puede obligar.
* **Una cuenta sin contraseña** tarda lo mismo en rechazar una contraseña que una con
  ella: se compara siempre contra un hash de relleno, y el tiempo no dice cuáles entran
  sólo por SSO.
* El secreto del cliente no vuelve nunca por la API. Guardar con el campo vacío lo
  conserva. El emisor se comprueba contra el proveedor al guardarlo, no el día que
  alguien intenta entrar.

**SCIM 2.0** (`/scim/v2`, `scim.py`): lo que Okta y Entra ID usan para dar de alta y de
baja sin que nadie toque Laplace.
* Hay `Users` (listar con `userName eq` o `externalId eq`, leer, crear, `PUT`, `PATCH`
  en las dos formas, con `path` y con objeto de valores, y `DELETE`),
  `ServiceProviderConfig` y `ResourceTypes`. **No hay `Groups`**: el rol es el rol por
  defecto del SSO, y se cambia en Laplace. Mapear grupos a roles es lo siguiente si
  alguien lo pide.
* **Desactivar y borrar quitan la pertenencia, no la cuenta.** La persona puede ser de
  otras organizaciones, y SCIM no toca nada fuera de la suya. Lo dado de baja se
  recuerda, porque el proveedor pregunta y espera `active: false`, no un 404.
* **No se deja a una organización sin propietario**: desactivar al último es un 409.
* La autenticación es una clave por organización (`lpscim_…`), que se crea en la
  interfaz, se enseña una vez y se guarda como hash. La ruta está fuera de `/api`, así
  que el middleware no la mira y la comprueba ella entera. En el modo local no existe.
* Un alta de un correo que ya tiene cuenta sólo le da la pertenencia: ni su contraseña
  ni sus otras organizaciones. Se verifica si el correo es de un dominio de la
  organización.

Todo queda en la auditoría de la organización, también los rechazos de SSO con su
motivo. Las tablas nuevas van en el mismo esquema en SQLite y en Postgres.

Pruebas (`test_empresa.py`, con un proveedor OIDC y un correo falsos):
- el enlace de verificación, de un solo uso y del correo al que se mandó;
- la invitación que no se manda en nombre de alguien sin verificar, y la que verifica
  al aceptarla;
- el viaje completo de SSO, con PKCE, alta, rol por defecto y reconocimiento por `sub`;
- ocho `id_token` que no cuadran;
- el `state` de otro navegador y el `next` hacia fuera;
- el proveedor que quiere entrar en una cuenta de otra organización, y quien ya no es
  miembro;
- dominios públicos, ajenos o puestos por la propia organización;
- la obligación de SSO;
- SCIM entero: alta, duplicado, filtros, las dos formas de `PATCH`, baja, el último
  propietario, la clave revocada y la clave de otra organización;
- SCIM en modo local;
- el recorrido de las tablas en SQLite y en Postgres.

`test_pantallas_cuentas.py` recorre la sección de SSO y SCIM, el botón de entrar con
SSO, la vuelta con error y la página de verificar. Se rompieron a propósito trece
comprobaciones, del `nonce` a la del correo del enlace, y alguna prueba falla. La del
correo del enlace no mordía al principio; ahora hay una prueba que cambia el correo.
