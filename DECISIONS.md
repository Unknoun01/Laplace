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

