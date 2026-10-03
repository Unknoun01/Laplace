# Índice de decisiones

Generado por `scripts/indice_decisiones.py` a partir de `DECISIONS.md`: no se edita a
mano. Cada decisión cae en los temas cuyas palabras aparecen en su título, así que
una puede salir en varios, y alguna en ninguno: la lista completa, en orden, está al
final. 185 decisiones.

## Coste, precios y caché

- [D-005](../DECISIONS.md#d-005--el-coste-se-calcula-en-el-backend-en-la-ingesta-y-se-guarda-desglosado-por-span) — El coste se calcula en el backend, en la ingesta, y se guarda desglosado por span
- [D-006](../DECISIONS.md#d-006--la-tabla-de-precios-es-un-json-versionado-en-el-repo) — La tabla de precios es un JSON versionado en el repo
- [D-022](../DECISIONS.md#d-022--el-coste-se-guarda-y-se-muestra-en-usd) — El coste se guarda y se muestra en USD
- [D-034](../DECISIONS.md#d-034--un-hallazgo-que-no-cuesta-dinero-lo-dice-no-lo-disimula) — Un hallazgo que no cuesta dinero lo dice, no lo disimula
- [D-036](../DECISIONS.md#d-036--el-coste-se-muestra-en-usd-leyendo-la-moneda-de-la-api) — El coste se muestra en USD, leyendo la moneda de la API
- [D-039](../DECISIONS.md#d-039--ordenar-por-coste-no-es-paginable-con-cursor) — Ordenar por coste no es paginable con cursor
- [D-041](../DECISIONS.md#d-041--los-precios-se-verifican-contra-la-página-oficial-con-fuente-y-fecha) — Los precios se verifican contra la página oficial, con fuente y fecha
- [D-043](../DECISIONS.md#d-043--un-modelo-sin-tarifa-cuesta-no-lo-sabemos-no-cero) — Un modelo sin tarifa cuesta "no lo sabemos", no cero
- [D-044](../DECISIONS.md#d-044--streaming-se-acumulan-tokens-contenido-y-coste) — Streaming: se acumulan tokens, contenido y coste
- [D-050](../DECISIONS.md#d-050--input_tokens-es-el-total-facturable-con-la-caché-dentro) — `input_tokens` es el total facturable, con la caché dentro
- [D-051](../DECISIONS.md#d-051--ante-un-metro-de-facturación-que-no-se-puede-determinar-se-cobra-el-estándar-y-se-dice) — Ante un metro de facturación que no se puede determinar, se cobra el estándar y se dice
- [D-055](../DECISIONS.md#d-055--el-ahorro-de-la-regla-de-contexto-fijo-descuenta-lo-que-cuesta-escribir-la-caché) — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
- [D-057](../DECISIONS.md#d-057--cambiar-de-modelo-y-activar-la-caché-se-cuentan-encadenados-no-sumados) — Cambiar de modelo y activar la caché se cuentan encadenados, no sumados
- [D-063](../DECISIONS.md#d-063--el-descuento-quita-llamadas-no-sólo-tokens) — El descuento quita llamadas, no sólo tokens
- [D-076](../DECISIONS.md#d-076--un-hallazgo-con-coste-no-fiable-se-anuncia-como-suelo-nunca-como-cifra) — Un hallazgo con coste no fiable se anuncia como suelo, nunca como cifra
- [D-088](../DECISIONS.md#d-088--lo-que-cuesta-juzgar-es-coste-real-se-mide-igual-y-se-enseña-aparte) — Lo que cuesta juzgar es coste real, se mide igual y se enseña aparte
- [D-096](../DECISIONS.md#d-096--la-cobertura-va-delante-del-dinero-no-en-avanzado) — La cobertura va delante del dinero, no en Avanzado
- [D-101](../DECISIONS.md#d-101--escribir-en-caché-se-cobra-también-en-openai) — Escribir en caché se cobra, también en OpenAI
- [D-105](../DECISIONS.md#d-105--ollama-sí-tiene-caché-corrige-d-103-y-d-104) — Ollama sí tiene caché: corrige D-103 y D-104
- [D-108](../DECISIONS.md#d-108--las-reglas-de-dinero-tienen-que-funcionar-sin-tarifa) — Las reglas de dinero tienen que funcionar sin tarifa
- [D-111](../DECISIONS.md#d-111--leer-de-caché-también-se-cobra-y-la-regla-no-lo-miraba) — Leer de caché también se cobra, y la regla no lo miraba
- [D-141](../DECISIONS.md#d-141--cerrar-la-fase-3-el-doble-conteo-el-nodo-de-langgraph-y-la-tarifa-sin-verificar-en-lo-agregado) — Cerrar la Fase 3: el doble conteo, el nodo de LangGraph y la tarifa sin verificar en lo agregado
- [D-152](../DECISIONS.md#d-152--dónde-se-va-el-dinero-gasto-por-día-con-la-franja-evitable-y-coste-por-paso) — Dónde se va el dinero: gasto por día con la franja evitable y coste por paso
- [D-161](../DECISIONS.md#d-161--qué-clientes-te-hacen-perder-dinero) — Qué clientes te hacen perder dinero
- [D-178](../DECISIONS.md#d-178--el-mismo-prefijo-en-varios-pasos-sin-compartir-la-caché) — El mismo prefijo en varios pasos, sin compartir la caché
- [D-179](../DECISIONS.md#d-179--margen-por-cliente-lo-evitable-de-cada-uno-otras-monedas-y-su-columna) — Margen por cliente: lo evitable de cada uno, otras monedas y su columna
- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza

## Detección: reglas y hallazgos

- [D-032](../DECISIONS.md#d-032--las-pantallas-de-diagnóstico-necesitaban-adelantar-la-fase-2) — Las pantallas de diagnóstico necesitaban adelantar la Fase 2
- [D-033](../DECISIONS.md#d-033--las-reglas-no-pueden-solaparse-se-descuenta-el-doble-conteo) — Las reglas no pueden solaparse: se descuenta el doble conteo
- [D-034](../DECISIONS.md#d-034--un-hallazgo-que-no-cuesta-dinero-lo-dice-no-lo-disimula) — Un hallazgo que no cuesta dinero lo dice, no lo disimula
- [D-038](../DECISIONS.md#d-038--el-identificador-de-un-hallazgo-es-determinista-no-se-guarda) — El identificador de un hallazgo es determinista, no se guarda
- [D-047](../DECISIONS.md#d-047--el-modo-avanzado-se-oculta-en-diagnóstico-no-se-muestra-en-avanzado) — El modo avanzado se OCULTA en diagnóstico, no se muestra en avanzado
- [D-055](../DECISIONS.md#d-055--el-ahorro-de-la-regla-de-contexto-fijo-descuenta-lo-que-cuesta-escribir-la-caché) — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
- [D-056](../DECISIONS.md#d-056--el-agente-de-ejemplo-usa-modelos-vigentes-y-genera-contexto-fijo) — El agente de ejemplo usa modelos vigentes y genera contexto fijo
- [D-059](../DECISIONS.md#d-059--las-reglas-agrupan-por-nombre-del-paso-modelo--resuelto-en-d-060) — ~~Las reglas agrupan por (nombre del paso, modelo)~~ — resuelto en D-060
- [D-060](../DECISIONS.md#d-060--un-paso-es-llamada-hecha-desde-el-mismo-sitio-y-con-las-mismas-instrucciones) — Un paso es «llamada hecha desde el mismo sitio y con las mismas instrucciones»
- [D-061](../DECISIONS.md#d-061--el-descuento-anti-doble-conteo-cruza-por-paso-con-vuelta-al-nombre) — El descuento anti-doble-conteo cruza por paso, con vuelta al nombre
- [D-062](../DECISIONS.md#d-062--las-repeticiones-se-detectan-por-entrada-pero-se-reportan-por-paso) — Las repeticiones se detectan por entrada pero se reportan por paso
- [D-064](../DECISIONS.md#d-064--la-batería-del-doble-conteo-se-amplía-nunca-se-sustituye) — La batería del doble conteo se amplía, nunca se sustituye
- [D-074](../DECISIONS.md#d-074--una-alerta-por-hallazgo-y-periodo-de-calma-agrupación-y-silencio) — Una alerta por hallazgo y periodo de calma: agrupación *y* silencio
- [D-076](../DECISIONS.md#d-076--un-hallazgo-con-coste-no-fiable-se-anuncia-como-suelo-nunca-como-cifra) — Un hallazgo con coste no fiable se anuncia como suelo, nunca como cifra
- [D-106](../DECISIONS.md#d-106--el-sitio-de-un-paso-es-el-camino-de-llamada-no-el-nombre-de-la-función) — El sitio de un paso es el camino de llamada, no el nombre de la función
- [D-108](../DECISIONS.md#d-108--las-reglas-de-dinero-tienen-que-funcionar-sin-tarifa) — Las reglas de dinero tienen que funcionar sin tarifa
- [D-109](../DECISIONS.md#d-109--la-regla-de-bucles-que-era-el-diferenciador-y-no-existía) — La regla de bucles, que era el diferenciador y no existía
- [D-111](../DECISIONS.md#d-111--leer-de-caché-también-se-cobra-y-la-regla-no-lo-miraba) — Leer de caché también se cobra, y la regla no lo miraba
- [D-113](../DECISIONS.md#d-113--un-hallazgo-que-el-motor-encuentra-tiene-que-saber-explicarse) — Un hallazgo que el motor encuentra tiene que saber explicarse
- [D-117](../DECISIONS.md#d-117--la-quinta-cara-del-doble-conteo-el-bucle-contra-el-modelo-caro) — La quinta cara del doble conteo: el bucle contra el modelo caro
- [D-118](../DECISIONS.md#d-118--el-héroe-cuando-el-reparto-no-reparte-y-la-cifra-que-explica-el-hallazgo) — El héroe cuando el reparto no reparte, y la cifra que explica el hallazgo
- [D-119](../DECISIONS.md#d-119--un-bucle-visto-desde-dos-alturas-del-árbol-es-un-problema-no-dos) — Un bucle visto desde dos alturas del árbol es un problema, no dos
- [D-134](../DECISIONS.md#d-134--llamadas-simultáneas-la-ficha-del-modelo-caro-generadores-y-el-paquete) — Llamadas simultáneas, la ficha del modelo caro, generadores y el paquete
- [D-141](../DECISIONS.md#d-141--cerrar-la-fase-3-el-doble-conteo-el-nodo-de-langgraph-y-la-tarifa-sin-verificar-en-lo-agregado) — Cerrar la Fase 3: el doble conteo, el nodo de LangGraph y la tarifa sin verificar en lo agregado
- [D-143](../DECISIONS.md#d-143--el-diagnóstico-que-alguien-mira-se-recalcula-antes-de-caducar) — El Diagnóstico que alguien mira se recalcula antes de caducar
- [D-152](../DECISIONS.md#d-152--dónde-se-va-el-dinero-gasto-por-día-con-la-franja-evitable-y-coste-por-paso) — Dónde se va el dinero: gasto por día con la franja evitable y coste por paso
- [D-154](../DECISIONS.md#d-154--el-diagnóstico-medido-otra-vez-con-el-gráfico-dentro) — El Diagnóstico medido otra vez, con el gráfico dentro
- [D-156](../DECISIONS.md#d-156--el-producto-alrededor-del-ciclo-detectar-probar-arreglar-verificar) — El producto alrededor del ciclo: detectar, probar, arreglar, verificar
- [D-157](../DECISIONS.md#d-157--prompts-como-fuente-de-hallazgos) — Prompts como fuente de hallazgos
- [D-172](../DECISIONS.md#d-172--la-guardia-de-las-reglas-nuevas) — La guardia de las reglas nuevas
- [D-177](../DECISIONS.md#d-177--preagregados-por-minuto-del-diagnóstico-y-clave-por-hora) — Preagregados por minuto del Diagnóstico y clave por hora
- [D-178](../DECISIONS.md#d-178--el-mismo-prefijo-en-varios-pasos-sin-compartir-la-caché) — El mismo prefijo en varios pasos, sin compartir la caché
- [D-179](../DECISIONS.md#d-179--margen-por-cliente-lo-evitable-de-cada-uno-otras-monedas-y-su-columna) — Margen por cliente: lo evitable de cada uno, otras monedas y su columna
- [D-180](../DECISIONS.md#d-180--diagnóstico-con-modelo-que-cita-los-spans-de-los-que-sale) — Diagnóstico con modelo, que cita los spans de los que sale
- [D-183](../DECISIONS.md#d-183--las-medianas-del-uso-por-paso-la-misma-cuenta-en-los-dos-almacenes-y-una-llamada-con-respuesta-y-sin-recuento-ya-no-sale-gratis) — Las medianas del uso por paso, la misma cuenta en los dos almacenes; y una llamada con respuesta y sin recuento ya no sale gratis
- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza

## Probar: evaluaciones, juez y replay

- [D-035](../DECISIONS.md#d-035--no-se-muestra-ninguna-métrica-de-calidad-del-modelo-alternativo) — No se muestra ninguna métrica de calidad del modelo alternativo
- [D-083](../DECISIONS.md#d-083--el-veredicto-de-persona-y-el-de-máquina-no-se-mezclan-y-la-separación-es-estructural) — El veredicto de persona y el de máquina no se mezclan, y la separación es estructural
- [D-085](../DECISIONS.md#d-085--los-casos-salen-de-tráfico-real-y-se-puede-ver-de-dónde) — Los casos salen de tráfico real, y se puede ver de dónde
- [D-086](../DECISIONS.md#d-086--laplace-no-ejecuta-el-agente-de-nadie-la-tirada-la-corre-el-sdk) — Laplace no ejecuta el agente de nadie: la tirada la corre el SDK
- [D-087](../DECISIONS.md#d-087--ningún-porcentaje-de-acierto-sin-su-guarda-y-ningún-ganador-sin-margen) — Ningún porcentaje de acierto sin su guarda, y ningún ganador sin margen
- [D-089](../DECISIONS.md#d-089--qué-añade-avanzado-en-evaluaciones) — Qué añade Avanzado en Evaluaciones
- [D-094](../DECISIONS.md#d-094--en-una-comparación-a-vs-b-con-qué-prompt-corrió-cada-lado-sale-de-las-trazas) — En una comparación A vs B, con qué prompt corrió cada lado sale de las trazas
- [D-156](../DECISIONS.md#d-156--el-producto-alrededor-del-ciclo-detectar-probar-arreglar-verificar) — El producto alrededor del ciclo: detectar, probar, arreglar, verificar
- [D-160](../DECISIONS.md#d-160--prompts-sin-tiradas-de-evaluación-envoltorios-con--y-el-modelo-rápido-ponderado) — Prompts sin tiradas de evaluación, envoltorios con «=» y el modelo rápido ponderado
- [D-167](../DECISIONS.md#d-167--probar-el-modelo-barato-reenviando-las-llamadas-reales-sin-escribir-código) — Probar el modelo barato reenviando las llamadas reales, sin escribir código

## Prompts

- [D-006](../DECISIONS.md#d-006--la-tabla-de-precios-es-un-json-versionado-en-el-repo) — La tabla de precios es un JSON versionado en el repo
- [D-042](../DECISIONS.md#d-042--resolver-por-prefijo-no-puede-saltar-de-versión) — Resolver por prefijo no puede saltar de versión
- [D-090](../DECISIONS.md#d-090--la-versión-de-un-prompt-se-escribe-en-la-traza-y-sólo-si-se-ha-comprobado) — La versión de un prompt se escribe en la traza, y sólo si se ha comprobado
- [D-091](../DECISIONS.md#d-091--servir-prompts-mete-a-laplace-en-el-camino-caliente-y-eso-hay-que-pagarlo) — Servir prompts mete a Laplace en el camino caliente, y eso hay que pagarlo
- [D-092](../DECISIONS.md#d-092--un-pico-se-atribuye-a-un-prompt-por-las-trazas-nunca-por-la-hora-del-despliegue) — Un pico se atribuye a un prompt por las trazas, nunca por la hora del despliegue
- [D-093](../DECISIONS.md#d-093--sin-adoptar-la-gestión-de-prompts-la-pestaña-enseña-lo-que-dicen-las-trazas) — Sin adoptar la gestión de prompts, la pestaña enseña lo que dicen las trazas
- [D-094](../DECISIONS.md#d-094--en-una-comparación-a-vs-b-con-qué-prompt-corrió-cada-lado-sale-de-las-trazas) — En una comparación A vs B, con qué prompt corrió cada lado sale de las trazas
- [D-095](../DECISIONS.md#d-095--qué-añade-avanzado-en-prompts) — Qué añade Avanzado en Prompts
- [D-144](../DECISIONS.md#d-144--buscar-dentro-de-prompts-respuestas-y-herramientas) — Buscar dentro de prompts, respuestas y herramientas
- [D-157](../DECISIONS.md#d-157--prompts-como-fuente-de-hallazgos) — Prompts como fuente de hallazgos
- [D-160](../DECISIONS.md#d-160--prompts-sin-tiradas-de-evaluación-envoltorios-con--y-el-modelo-rápido-ponderado) — Prompts sin tiradas de evaluación, envoltorios con «=» y el modelo rápido ponderado

## Instrumentación: SDK e integraciones

- [D-001](../DECISIONS.md#d-001--los-tipos-compartidos-viven-en-el-sdk-laplaceschema-y-el-backend-depende-del-sdk) — Los tipos compartidos viven en el SDK (`laplace.schema`), y el backend depende del SDK
- [D-002](../DECISIONS.md#d-002--pydantic-v2-es-dependencia-del-sdk) — Pydantic v2 es dependencia del SDK
- [D-004](../DECISIONS.md#d-004--transporte-otlphttp-con-codificación-protobuf) — Transporte OTLP/HTTP con codificación protobuf
- [D-005](../DECISIONS.md#d-005--el-coste-se-calcula-en-el-backend-en-la-ingesta-y-se-guarda-desglosado-por-span) — El coste se calcula en el backend, en la ingesta, y se guarda desglosado por span
- [D-007](../DECISIONS.md#d-007--dedup_hash-se-calcula-en-la-ingesta-no-en-el-sdk) — `dedup_hash` se calcula en la ingesta, no en el SDK
- [D-012](../DECISIONS.md#d-012--frontend-nextjs-app-router--typescript--tailwind-sin-librería-de-componentes) — Frontend: Next.js (App Router) + TypeScript + Tailwind, sin librería de componentes
- [D-014](../DECISIONS.md#d-014--nombres-de-span-convención-genai-para-llm-nombre-propio-para-el-resto) — Nombres de span: convención GenAI para LLM, nombre propio para el resto
- [D-016](../DECISIONS.md#d-016--el-streaming-se-registra-pero-todavía-no-se-acumula) — El streaming se registra pero todavía no se acumula
- [D-044](../DECISIONS.md#d-044--streaming-se-acumulan-tokens-contenido-y-coste) — Streaming: se acumulan tokens, contenido y coste
- [D-065](../DECISIONS.md#d-065--dos-agentes-de-ejemplo-y-el-que-manda-es-el-mal-instrumentado) — Dos agentes de ejemplo, y el que manda es el mal instrumentado
- [D-086](../DECISIONS.md#d-086--laplace-no-ejecuta-el-agente-de-nadie-la-tirada-la-corre-el-sdk) — Laplace no ejecuta el agente de nadie: la tirada la corre el SDK
- [D-098](../DECISIONS.md#d-098--las-integraciones-se-prueban-contra-los-sdk-reales-con-el-transporte-falso) — Las integraciones se prueban contra los SDK reales, con el transporte falso
- [D-101](../DECISIONS.md#d-101--escribir-en-caché-se-cobra-también-en-openai) — Escribir en caché se cobra, también en OpenAI
- [D-136](../DECISIONS.md#d-136--las-trazas-de-openinference-y-openllmetry-entendidas) — Las trazas de OpenInference y OpenLLMetry, entendidas
- [D-139](../DECISIONS.md#d-139--agentes-en-typescript-la-guía-y-dos-fallos-de-la-ingesta-que-destapó) — Agentes en TypeScript: la guía, y dos fallos de la ingesta que destapó
- [D-141](../DECISIONS.md#d-141--cerrar-la-fase-3-el-doble-conteo-el-nodo-de-langgraph-y-la-tarifa-sin-verificar-en-lo-agregado) — Cerrar la Fase 3: el doble conteo, el nodo de LangGraph y la tarifa sin verificar en lo agregado
- [D-163](../DECISIONS.md#d-163--stripe-se-trae-solo-cada-día-y-el-cliente-desde-node-sin-sdk) — Stripe se trae solo cada día, y el cliente desde Node sin SDK
- [D-164](../DECISIONS.md#d-164--clientbeta-en-python-las-llamadas-que-no-se-veían) — `client.beta.*` en Python: las llamadas que no se veían
- [D-165](../DECISIONS.md#d-165--las-integraciones-de-typescript-que-faltaban-probadas-y-lo-que-se-perdía) — Las integraciones de TypeScript que faltaban, probadas: y lo que se perdía
- [D-170](../DECISIONS.md#d-170--los-tres-frameworks-que-faltaban-probados-y-lo-que-mastra-no-manda-donde-toca) — Los tres frameworks que faltaban, probados; y lo que Mastra no manda donde toca
- [D-181](../DECISIONS.md#d-181--redacción-de-datos-personales-y-muestreo-por-cola-en-el-sdk) — Redacción de datos personales y muestreo por cola, en el SDK
- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza

## Almacenes y escala

- [D-008](../DECISIONS.md#d-008--sin-vista-materializada-de-rollup-en-fase-1-group-by-sobre-spans-final) — Sin vista materializada de rollup en Fase 1: `GROUP BY` sobre `spans FINAL`
- [D-011](../DECISIONS.md#d-011--postgres-guarda-metadatos-y-las-tablas-reservadas-de-fases-34) — Postgres guarda metadatos y las tablas reservadas de Fases 3–4
- [D-015](../DECISIONS.md#d-015--el-almacenamiento-vive-detrás-de-un-protocol-spanstore) — El almacenamiento vive detrás de un `Protocol` (`SpanStore`)
- [D-020](../DECISIONS.md#d-020--un-cliente-de-clickhouse-por-hilo) — Un cliente de ClickHouse por hilo
- [D-021](../DECISIONS.md#d-021--postgres-se-publica-en-el-puerto-5433-del-host) — Postgres se publica en el puerto 5433 del host
- [D-026](../DECISIONS.md#d-026--final-sólo-donde-no-cuesta-fuera-de-la-apertura-de-traza) — `FINAL` sólo donde no cuesta: fuera de la apertura de traza
- [D-037](../DECISIONS.md#d-037--la-consulta-que-se-enseña-es-la-que-se-ejecuta) — La consulta que se enseña es la que se ejecuta
- [D-066](../DECISIONS.md#d-066--la-traducción-de-fila-a-modelo-se-comparte-entre-los-dos-almacenes) — La traducción de fila a modelo se comparte entre los dos almacenes
- [D-067](../DECISIONS.md#d-067--cómo-lo-hemos-detectado-enseña-la-consulta-del-almacén-que-respondió) — «Cómo lo hemos detectado» enseña la consulta del almacén que respondió
- [D-071](../DECISIONS.md#d-071--sqlite-deduplica-por-clave-primaria-no-por-motor-de-fusión) — SQLite deduplica por clave primaria, no por motor de fusión
- [D-082](../DECISIONS.md#d-082--la-duración-de-la-serie-se-redondea-al-milisegundo-en-sqlite) — La duración de la serie se redondea al milisegundo en SQLite
- [D-084](../DECISIONS.md#d-084--el-almacén-de-metadatos-pasa-a-tener-dos-implementaciones-de-verdad) — El almacén de metadatos pasa a tener dos implementaciones de verdad
- [D-099](../DECISIONS.md#d-099--ninguna-consulta-escoge-una-fila-cualquiera-y-hay-un-guardia-que-lo-vigila) — Ninguna consulta escoge «una fila cualquiera», y hay un guardia que lo vigila
- [D-129](../DECISIONS.md#d-129--la-deuda-de-la-auditoría-lo-que-escala-lo-que-se-reparte-y-lo-que-se-ve) — La deuda de la auditoría: lo que escala, lo que se reparte y lo que se ve
- [D-132](../DECISIONS.md#d-132--escalas-cerradas-contraste-aa-y-el-inicio-en-dos-columnas) — Escalas cerradas, contraste AA y el inicio en dos columnas
- [D-142](../DECISIONS.md#d-142--diez-millones-de-spans-al-día-qué-se-ha-medido-y-qué-se-ha-cambiado) — Diez millones de spans al día: qué se ha medido y qué se ha cambiado
- [D-168](../DECISIONS.md#d-168--la-clave-de-ordenación-por-día-su-migración-y-los-filtros-acotados) — La clave de ordenación por día, su migración y los filtros acotados
- [D-169](../DECISIONS.md#d-169--health-nombra-el-almacén-que-hay-y-la-limpieza) — `/health` nombra el almacén que hay, y la limpieza
- [D-173](../DECISIONS.md#d-173--retención-por-proyecto-y-borrar-lo-de-una-persona-o-un-cliente) — Retención por proyecto, y borrar lo de una persona o un cliente
- [D-183](../DECISIONS.md#d-183--las-medianas-del-uso-por-paso-la-misma-cuenta-en-los-dos-almacenes-y-una-llamada-con-respuesta-y-sin-recuento-ya-no-sale-gratis) — Las medianas del uso por paso, la misma cuenta en los dos almacenes; y una llamada con respuesta y sin recuento ya no sale gratis

## Interfaz

- [D-031](../DECISIONS.md#d-031--la-ui-reproduce-el-mock-se-cae-tailwind-y-se-usa-css-propio) — La UI reproduce el mock; se cae Tailwind y se usa CSS propio
- [D-032](../DECISIONS.md#d-032--las-pantallas-de-diagnóstico-necesitaban-adelantar-la-fase-2) — Las pantallas de diagnóstico necesitaban adelantar la Fase 2
- [D-040](../DECISIONS.md#d-040--el-proyecto-y-el-rango-viven-en-la-url) — El proyecto y el rango viven en la URL
- [D-048](../DECISIONS.md#d-048--qué-añade-avanzado-en-cada-pantalla) — Qué añade Avanzado en cada pantalla
- [D-058](../DECISIONS.md#d-058--los-días-observados-los-usa-todo-el-motor-no-sólo-el-héroe) — Los días observados los usa todo el motor, no sólo el héroe
- [D-069](../DECISIONS.md#d-069--la-interfaz-se-sirve-desde-el-mismo-origen-que-la-api-y-se-exporta-a-html) — La interfaz se sirve desde el mismo origen que la API, y se exporta a HTML
- [D-077](../DECISIONS.md#d-077--el-panel-mide-por-unidad-de-trabajo-y-dice-la-lectura-con-palabras) — El panel mide por unidad de trabajo, y dice la lectura con palabras
- [D-078](../DECISIONS.md#d-078--un-pico-se-atribuye-con-datos-o-se-dice-que-no-se-sabe) — Un pico se atribuye con datos o se dice que no se sabe
- [D-079](../DECISIONS.md#d-079--modo-en-vivo-por-polling-los-websockets-son-una-evolución-no-un-pendiente) — Modo en vivo por polling; los WebSockets son una evolución, no un pendiente
- [D-080](../DECISIONS.md#d-080--el-periodo-anterior-tiene-que-ser-utilizable-o-no-hay-comparación) — El periodo anterior tiene que ser utilizable, o no hay comparación
- [D-081](../DECISIONS.md#d-081--la-interfaz-del-árbol-gana-a-la-copia-del-paquete-y-se-dice-cuál-se-sirve) — La interfaz del árbol gana a la copia del paquete, y se dice cuál se sirve
- [D-092](../DECISIONS.md#d-092--un-pico-se-atribuye-a-un-prompt-por-las-trazas-nunca-por-la-hora-del-despliegue) — Un pico se atribuye a un prompt por las trazas, nunca por la hora del despliegue
- [D-118](../DECISIONS.md#d-118--el-héroe-cuando-el-reparto-no-reparte-y-la-cifra-que-explica-el-hallazgo) — El héroe cuando el reparto no reparte, y la cifra que explica el hallazgo
- [D-123](../DECISIONS.md#d-123--lo-que-el-usuario-decide-en-una-pantalla-y-no-en-variables-de-entorno) — Lo que el usuario decide, en una pantalla y no en variables de entorno
- [D-132](../DECISIONS.md#d-132--escalas-cerradas-contraste-aa-y-el-inicio-en-dos-columnas) — Escalas cerradas, contraste AA y el inicio en dos columnas
- [D-133](../DECISIONS.md#d-133--glassmorphism-rose-gold--amanecer-en-claro-y-tech-abisal-en-oscuro) — Glassmorphism, «Rose Gold & Amanecer» en claro y «Tech Abisal» en oscuro
- [D-134](../DECISIONS.md#d-134--llamadas-simultáneas-la-ficha-del-modelo-caro-generadores-y-el-paquete) — Llamadas simultáneas, la ficha del modelo caro, generadores y el paquete
- [D-135](../DECISIONS.md#d-135--lo-que-la-demo-de-un-mes-destapó-en-el-motor-y-las-pantallas-con-red) — Lo que la demo de un mes destapó en el motor, y las pantallas con red
- [D-145](../DECISIONS.md#d-145--mediana-p95-y-errores-en-el-panel-en-lugar-de-la-duración-media) — Mediana, p95 y errores en el Panel, en lugar de la duración media
- [D-153](../DECISIONS.md#d-153--el-grafo-del-agente-en-la-vista-de-traza) — El grafo del agente en la vista de traza
- [D-154](../DECISIONS.md#d-154--el-diagnóstico-medido-otra-vez-con-el-gráfico-dentro) — El Diagnóstico medido otra vez, con el gráfico dentro
- [D-155](../DECISIONS.md#d-155--el-oscuro-es-la-insignia-el-claro-neutro) — El oscuro es la insignia; el claro, neutro
- [D-158](../DECISIONS.md#d-158--el-claro-vuelve-a-ser-rose-gold-con-cristal-líquido) — El claro vuelve a ser Rose Gold, con cristal líquido
- [D-159](../DECISIONS.md#d-159--alto-contraste-para-el-claro-y-para-el-oscuro) — Alto contraste, para el claro y para el oscuro
- [D-174](../DECISIONS.md#d-174--globalscss-en-29-hojas-un-índice-de-decisiones-y-documentación-de-usuario) — `globals.css` en 29 hojas, un índice de decisiones y documentación de usuario
- [D-175](../DECISIONS.md#d-175--las-pantallas-con-sesión-recorridas-en-un-navegador) — Las pantallas con sesión, recorridas en un navegador
- [D-176](../DECISIONS.md#d-176--el-contraste-medido-sobre-cada-pantalla) — El contraste, medido sobre cada pantalla

## Alertas y presupuesto

- [D-074](../DECISIONS.md#d-074--una-alerta-por-hallazgo-y-periodo-de-calma-agrupación-y-silencio) — Una alerta por hallazgo y periodo de calma: agrupación *y* silencio
- [D-075](../DECISIONS.md#d-075--las-alertas-son-el-mismo-código-en-local-y-en-la-nube-el-estado-va-donde-ya-va-lo-mutable) — Las alertas son el mismo código en local y en la nube; el estado va donde ya va lo mutable
- [D-162](../DECISIONS.md#d-162--lo-que-quedaba-del-margen-por-cliente-sus-ejecuciones-sus-problemas-la-alerta-y-stripe) — Lo que quedaba del margen por cliente: sus ejecuciones, sus problemas, la alerta y Stripe
- [D-182](../DECISIONS.md#d-182--verificación-de-correo-sso-por-openid-connect-y-scim) — Verificación de correo, SSO por OpenID Connect y SCIM

## Cuentas, claves y seguridad

- [D-008](../DECISIONS.md#d-008--sin-vista-materializada-de-rollup-en-fase-1-group-by-sobre-spans-final) — Sin vista materializada de rollup en Fase 1: `GROUP BY` sobre `spans FINAL`
- [D-033](../DECISIONS.md#d-033--las-reglas-no-pueden-solaparse-se-descuenta-el-doble-conteo) — Las reglas no pueden solaparse: se descuenta el doble conteo
- [D-055](../DECISIONS.md#d-055--el-ahorro-de-la-regla-de-contexto-fijo-descuenta-lo-que-cuesta-escribir-la-caché) — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
- [D-057](../DECISIONS.md#d-057--cambiar-de-modelo-y-activar-la-caché-se-cuentan-encadenados-no-sumados) — Cambiar de modelo y activar la caché se cuentan encadenados, no sumados
- [D-071](../DECISIONS.md#d-071--sqlite-deduplica-por-clave-primaria-no-por-motor-de-fusión) — SQLite deduplica por clave primaria, no por motor de fusión
- [D-097](../DECISIONS.md#d-097--la-autenticación-deniega-por-defecto-y-el-modo-local-es-una-exención-escrita) — La autenticación deniega por defecto, y el modo local es una exención escrita
- [D-127](../DECISIONS.md#d-127--cuentas-las-personas-entran-los-agentes-escriben-con-clave) — Cuentas: las personas entran, los agentes escriben con clave
- [D-168](../DECISIONS.md#d-168--la-clave-de-ordenación-por-día-su-migración-y-los-filtros-acotados) — La clave de ordenación por día, su migración y los filtros acotados
- [D-169](../DECISIONS.md#d-169--health-nombra-el-almacén-que-hay-y-la-limpieza) — `/health` nombra el almacén que hay, y la limpieza
- [D-175](../DECISIONS.md#d-175--las-pantallas-con-sesión-recorridas-en-un-navegador) — Las pantallas con sesión, recorridas en un navegador
- [D-177](../DECISIONS.md#d-177--preagregados-por-minuto-del-diagnóstico-y-clave-por-hora) — Preagregados por minuto del Diagnóstico y clave por hora
- [D-183](../DECISIONS.md#d-183--las-medianas-del-uso-por-paso-la-misma-cuenta-en-los-dos-almacenes-y-una-llamada-con-respuesta-y-sin-recuento-ya-no-sale-gratis) — Las medianas del uso por paso, la misma cuenta en los dos almacenes; y una llamada con respuesta y sin recuento ya no sale gratis
- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza

## Margen por cliente

- [D-020](../DECISIONS.md#d-020--un-cliente-de-clickhouse-por-hilo) — Un cliente de ClickHouse por hilo
- [D-087](../DECISIONS.md#d-087--ningún-porcentaje-de-acierto-sin-su-guarda-y-ningún-ganador-sin-margen) — Ningún porcentaje de acierto sin su guarda, y ningún ganador sin margen
- [D-121](../DECISIONS.md#d-121--el-punto-ciego-de-d-097-era-una-fuga-entre-clientes) — El punto ciego de D-097 era una fuga entre clientes
- [D-122](../DECISIONS.md#d-122--diez-cosas-que-un-cliente-habría-visto-en-su-primera-tarde) — Diez cosas que un cliente habría visto en su primera tarde
- [D-161](../DECISIONS.md#d-161--qué-clientes-te-hacen-perder-dinero) — Qué clientes te hacen perder dinero
- [D-162](../DECISIONS.md#d-162--lo-que-quedaba-del-margen-por-cliente-sus-ejecuciones-sus-problemas-la-alerta-y-stripe) — Lo que quedaba del margen por cliente: sus ejecuciones, sus problemas, la alerta y Stripe
- [D-163](../DECISIONS.md#d-163--stripe-se-trae-solo-cada-día-y-el-cliente-desde-node-sin-sdk) — Stripe se trae solo cada día, y el cliente desde Node sin SDK
- [D-173](../DECISIONS.md#d-173--retención-por-proyecto-y-borrar-lo-de-una-persona-o-un-cliente) — Retención por proyecto, y borrar lo de una persona o un cliente
- [D-179](../DECISIONS.md#d-179--margen-por-cliente-lo-evitable-de-cada-uno-otras-monedas-y-su-columna) — Margen por cliente: lo evitable de cada uno, otras monedas y su columna

## Idiomas y textos

- [D-055](../DECISIONS.md#d-055--el-ahorro-de-la-regla-de-contexto-fijo-descuenta-lo-que-cuesta-escribir-la-caché) — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
- [D-056](../DECISIONS.md#d-056--el-agente-de-ejemplo-usa-modelos-vigentes-y-genera-contexto-fijo) — El agente de ejemplo usa modelos vigentes y genera contexto fijo
- [D-066](../DECISIONS.md#d-066--la-traducción-de-fila-a-modelo-se-comparte-entre-los-dos-almacenes) — La traducción de fila a modelo se comparte entre los dos almacenes
- [D-110](../DECISIONS.md#d-110--tres-textos-que-decían-algo-falso) — Tres textos que decían algo falso
- [D-147](../DECISIONS.md#d-147--cinco-idiomas-cómo-viaja-el-idioma-y-cómo-se-escribe-una-cifra) — Cinco idiomas: cómo viaja el idioma y cómo se escribe una cifra
- [D-148](../DECISIONS.md#d-148--el-producto-entero-en-cinco-idiomas) — El producto entero en cinco idiomas
- [D-149](../DECISIONS.md#d-149--los-errores-de-la-api-en-el-idioma-de-la-petición) — Los errores de la API, en el idioma de la petición
- [D-151](../DECISIONS.md#d-151--menos-texto-lo-que-puedes-dejar-de-pagar-un-distintivo-de-confianza-y-el-porqué-plegado) — Menos texto: lo que puedes dejar de pagar, un distintivo de confianza y el porqué plegado

## Demo y datos de ejemplo

- [D-056](../DECISIONS.md#d-056--el-agente-de-ejemplo-usa-modelos-vigentes-y-genera-contexto-fijo) — El agente de ejemplo usa modelos vigentes y genera contexto fijo
- [D-065](../DECISIONS.md#d-065--dos-agentes-de-ejemplo-y-el-que-manda-es-el-mal-instrumentado) — Dos agentes de ejemplo, y el que manda es el mal instrumentado
- [D-070](../DECISIONS.md#d-070--laplace-demo-manda-datos-inventados-y-lo-dice-tres-veces) — `laplace demo` manda datos inventados, y lo dice tres veces
- [D-072](../DECISIONS.md#d-072--la-demo-no-puede-sembrar-el-generador-de-números-aleatorios) — La demo no puede sembrar el generador de números aleatorios
- [D-100](../DECISIONS.md#d-100--los-nombres-de-modelo-de-los-tests-y-los-ejemplos-también-caducan) — Los nombres de modelo de los tests y los ejemplos también caducan
- [D-135](../DECISIONS.md#d-135--lo-que-la-demo-de-un-mes-destapó-en-el-motor-y-las-pantallas-con-red) — Lo que la demo de un mes destapó en el motor, y las pantallas con red
- [D-146](../DECISIONS.md#d-146--la-demo-llega-hasta-ahora) — La demo llega hasta ahora

## Datos, trazas y API

- [D-003](../DECISIONS.md#d-003--los-mensajes-se-guardan-como-json-en-gen_aiinputmessages--gen_aioutputmessages) — Los mensajes se guardan como JSON en `gen_ai.input.messages` / `gen_ai.output.messages`
- [D-005](../DECISIONS.md#d-005--el-coste-se-calcula-en-el-backend-en-la-ingesta-y-se-guarda-desglosado-por-span) — El coste se calcula en el backend, en la ingesta, y se guarda desglosado por span
- [D-008](../DECISIONS.md#d-008--sin-vista-materializada-de-rollup-en-fase-1-group-by-sobre-spans-final) — Sin vista materializada de rollup en Fase 1: `GROUP BY` sobre `spans FINAL`
- [D-008b](../DECISIONS.md#d-008b--la-lista-de-trazas-no-filtra-por-ventana-temporal-por-defecto) — La lista de trazas no filtra por ventana temporal por defecto
- [D-009](../DECISIONS.md#d-009--los-payloads-se-guardan-completos-y-sin-truncar) — Los payloads se guardan completos y sin truncar
- [D-014](../DECISIONS.md#d-014--nombres-de-span-convención-genai-para-llm-nombre-propio-para-el-resto) — Nombres de span: convención GenAI para LLM, nombre propio para el resto
- [D-015](../DECISIONS.md#d-015--el-almacenamiento-vive-detrás-de-un-protocol-spanstore) — El almacenamiento vive detrás de un `Protocol` (`SpanStore`)
- [D-017](../DECISIONS.md#d-017--límite-de-1-mib-por-payload-con-marca-explícita) — Límite de 1 MiB por payload, con marca explícita
- [D-023](../DECISIONS.md#d-023--el-cursor-de-paginación-es-inicio-trace_id-no-sólo-la-fecha) — El cursor de paginación es `(inicio, trace_id)`, no sólo la fecha
- [D-026](../DECISIONS.md#d-026--final-sólo-donde-no-cuesta-fuera-de-la-apertura-de-traza) — `FINAL` sólo donde no cuesta: fuera de la apertura de traza
- [D-027](../DECISIONS.md#d-027--índice-de-salto-sobre-trace_id) — Índice de salto sobre `trace_id`
- [D-028](../DECISIONS.md#d-028--el-flush-de-salida-tiene-un-tope-de-tiempo-que-se-cumple-de-verdad) — El flush de salida tiene un tope de tiempo que se cumple de verdad
- [D-036](../DECISIONS.md#d-036--el-coste-se-muestra-en-usd-leyendo-la-moneda-de-la-api) — El coste se muestra en USD, leyendo la moneda de la API
- [D-039](../DECISIONS.md#d-039--ordenar-por-coste-no-es-paginable-con-cursor) — Ordenar por coste no es paginable con cursor
- [D-040](../DECISIONS.md#d-040--el-proyecto-y-el-rango-viven-en-la-url) — El proyecto y el rango viven en la URL
- [D-045](../DECISIONS.md#d-045--no-se-inyecta-stream_options-en-la-petición-del-usuario) — No se inyecta `stream_options` en la petición del usuario
- [D-046](../DECISIONS.md#d-046--se-proyecta-sobre-los-días-observados-no-sobre-los-que-pide-el-selector) — Se proyecta sobre los días observados, no sobre los que pide el selector
- [D-069](../DECISIONS.md#d-069--la-interfaz-se-sirve-desde-el-mismo-origen-que-la-api-y-se-exporta-a-html) — La interfaz se sirve desde el mismo origen que la API, y se exporta a HTML
- [D-073](../DECISIONS.md#d-073--por-debajo-de-un-día-de-datos-no-se-proyecta-a-mes-se-enseña-lo-gastado) — Por debajo de un día de datos no se proyecta a mes; se enseña lo gastado
- [D-090](../DECISIONS.md#d-090--la-versión-de-un-prompt-se-escribe-en-la-traza-y-sólo-si-se-ha-comprobado) — La versión de un prompt se escribe en la traza, y sólo si se ha comprobado
- [D-092](../DECISIONS.md#d-092--un-pico-se-atribuye-a-un-prompt-por-las-trazas-nunca-por-la-hora-del-despliegue) — Un pico se atribuye a un prompt por las trazas, nunca por la hora del despliegue
- [D-093](../DECISIONS.md#d-093--sin-adoptar-la-gestión-de-prompts-la-pestaña-enseña-lo-que-dicen-las-trazas) — Sin adoptar la gestión de prompts, la pestaña enseña lo que dicen las trazas
- [D-094](../DECISIONS.md#d-094--en-una-comparación-a-vs-b-con-qué-prompt-corrió-cada-lado-sale-de-las-trazas) — En una comparación A vs B, con qué prompt corrió cada lado sale de las trazas
- [D-136](../DECISIONS.md#d-136--las-trazas-de-openinference-y-openllmetry-entendidas) — Las trazas de OpenInference y OpenLLMetry, entendidas
- [D-137](../DECISIONS.md#d-137--las-otras-puertas-al-modelo-responses-api-parse-messagesstream-y-la-respuesta-cruda) — Las otras puertas al modelo: Responses API, `parse`, `messages.stream()` y la respuesta cruda
- [D-142](../DECISIONS.md#d-142--diez-millones-de-spans-al-día-qué-se-ha-medido-y-qué-se-ha-cambiado) — Diez millones de spans al día: qué se ha medido y qué se ha cambiado
- [D-149](../DECISIONS.md#d-149--los-errores-de-la-api-en-el-idioma-de-la-petición) — Los errores de la API, en el idioma de la petición
- [D-153](../DECISIONS.md#d-153--el-grafo-del-agente-en-la-vista-de-traza) — El grafo del agente en la vista de traza
- [D-160](../DECISIONS.md#d-160--prompts-sin-tiradas-de-evaluación-envoltorios-con--y-el-modelo-rápido-ponderado) — Prompts sin tiradas de evaluación, envoltorios con «=» y el modelo rápido ponderado
- [D-166](../DECISIONS.md#d-166--con-semanas-de-histórico-una-ventana-de-un-día-lee-el-mes-entero) — Con semanas de histórico, una ventana de un día lee el mes entero
- [D-173](../DECISIONS.md#d-173--retención-por-proyecto-y-borrar-lo-de-una-persona-o-un-cliente) — Retención por proyecto, y borrar lo de una persona o un cliente
- [D-174](../DECISIONS.md#d-174--globalscss-en-29-hojas-un-índice-de-decisiones-y-documentación-de-usuario) — `globals.css` en 29 hojas, un índice de decisiones y documentación de usuario
- [D-180](../DECISIONS.md#d-180--diagnóstico-con-modelo-que-cita-los-spans-de-los-que-sale) — Diagnóstico con modelo, que cita los spans de los que sale
- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza

## Pruebas y método

- [D-024](../DECISIONS.md#d-024--los-alias-de-las-agregaciones-no-pueden-llamarse-como-su-columna) — Los alias de las agregaciones no pueden llamarse como su columna
- [D-025](../DECISIONS.md#d-025--hay-dos-niveles-de-prueba-y-el-segundo-se-salta-solo) — Hay dos niveles de prueba, y el segundo se salta solo
- [D-029](../DECISIONS.md#d-029--las-pruebas-borran-sus-datos-al-terminar) — Las pruebas borran sus datos al terminar
- [D-038](../DECISIONS.md#d-038--el-identificador-de-un-hallazgo-es-determinista-no-se-guarda) — El identificador de un hallazgo es determinista, no se guarda
- [D-049](../DECISIONS.md#d-049--las-piezas-compartidas-de-los-tests-viven-en-helperspy-no-en-conftestpy) — Las piezas compartidas de los tests viven en `helpers.py`, no en `conftest.py`
- [D-051](../DECISIONS.md#d-051--ante-un-metro-de-facturación-que-no-se-puede-determinar-se-cobra-el-estándar-y-se-dice) — Ante un metro de facturación que no se puede determinar, se cobra el estándar y se dice
- [D-053](../DECISIONS.md#d-053--auditoría-de-identificadores-no-había-ninguno-inventado-faltaban-cinco-reales) — Auditoría de identificadores: no había ninguno inventado, faltaban cinco reales
- [D-098](../DECISIONS.md#d-098--las-integraciones-se-prueban-contra-los-sdk-reales-con-el-transporte-falso) — Las integraciones se prueban contra los SDK reales, con el transporte falso
- [D-100](../DECISIONS.md#d-100--los-nombres-de-modelo-de-los-tests-y-los-ejemplos-también-caducan) — Los nombres de modelo de los tests y los ejemplos también caducan
- [D-116](../DECISIONS.md#d-116--un-test-que-falla-por-el-reloj-es-peor-que-no-tenerlo) — Un test que falla por el reloj es peor que no tenerlo
- [D-128](../DECISIONS.md#d-128--la-auditoría-de-septiembre-nueve-costuras-y-una-red-por-cada-una) — La auditoría de septiembre: nueve costuras, y una red por cada una
- [D-129](../DECISIONS.md#d-129--la-deuda-de-la-auditoría-lo-que-escala-lo-que-se-reparte-y-lo-que-se-ve) — La deuda de la auditoría: lo que escala, lo que se reparte y lo que se ve
- [D-140](../DECISIONS.md#d-140--la-prueba-inestable-era-un-servidor-falso-que-no-leía-el-cuerpo) — La prueba inestable era un servidor falso que no leía el cuerpo
- [D-150](../DECISIONS.md#d-150--los-pendientes-de-la-auditoría-del-rediseño-a1-a2-b1-y-la-maquetación) — Los pendientes de la auditoría del rediseño (A1, A2, B1) y la maquetación
- [D-171](../DECISIONS.md#d-171--ocho-detalles-de-la-lista-de-deuda-cerrados-con-su-prueba) — Ocho detalles de la lista de deuda, cerrados con su prueba

## Proyecto y empaquetado

- [D-002](../DECISIONS.md#d-002--pydantic-v2-es-dependencia-del-sdk) — Pydantic v2 es dependencia del SDK
- [D-013](../DECISIONS.md#d-013--empaquetado-python-con-hatchling-gestor-js-npm) — Empaquetado Python con Hatchling; gestor JS: npm
- [D-018](../DECISIONS.md#d-018--python-310-como-mínimo) — Python 3.10 como mínimo
- [D-019](../DECISIONS.md#d-019--sin-cli-laplace-todavía) — Sin CLI `laplace` todavía
- [D-020](../DECISIONS.md#d-020--un-cliente-de-clickhouse-por-hilo) — Un cliente de ClickHouse por hilo
- [D-030](../DECISIONS.md#d-030--fichero-de-licencia) — Fichero de licencia
- [D-068](../DECISIONS.md#d-068--los-drivers-de-la-nube-son-un-extra-no-una-dependencia) — Los drivers de la nube son un extra, no una dependencia
- [D-075](../DECISIONS.md#d-075--las-alertas-son-el-mismo-código-en-local-y-en-la-nube-el-estado-va-donde-ya-va-lo-mutable) — Las alertas son el mismo código en local y en la nube; el estado va donde ya va lo mutable
- [D-081](../DECISIONS.md#d-081--la-interfaz-del-árbol-gana-a-la-copia-del-paquete-y-se-dice-cuál-se-sirve) — La interfaz del árbol gana a la copia del paquete, y se dice cuál se sirve
- [D-097](../DECISIONS.md#d-097--la-autenticación-deniega-por-defecto-y-el-modo-local-es-una-exención-escrita) — La autenticación deniega por defecto, y el modo local es una exención escrita
- [D-102](../DECISIONS.md#d-102--un-modelo-local-para-ejercitar-el-camino-entero-sin-pagar-nada) — Un modelo local para ejercitar el camino entero sin pagar nada
- [D-112](../DECISIONS.md#d-112--las-medias-que-quedaban-y-la-nube-ejecutada-de-verdad) — Las medias que quedaban, y la nube ejecutada de verdad
- [D-121](../DECISIONS.md#d-121--el-punto-ciego-de-d-097-era-una-fuga-entre-clientes) — El punto ciego de D-097 era una fuga entre clientes
- [D-122](../DECISIONS.md#d-122--diez-cosas-que-un-cliente-habría-visto-en-su-primera-tarde) — Diez cosas que un cliente habría visto en su primera tarde
- [D-126](../DECISIONS.md#d-126--el-sql-de-nube-de-d-122-a-d-125-ejecutado) — El SQL de nube de D-122 a D-125, ejecutado
- [D-134](../DECISIONS.md#d-134--llamadas-simultáneas-la-ficha-del-modelo-caro-generadores-y-el-paquete) — Llamadas simultáneas, la ficha del modelo caro, generadores y el paquete
- [D-161](../DECISIONS.md#d-161--qué-clientes-te-hacen-perder-dinero) — Qué clientes te hacen perder dinero
- [D-162](../DECISIONS.md#d-162--lo-que-quedaba-del-margen-por-cliente-sus-ejecuciones-sus-problemas-la-alerta-y-stripe) — Lo que quedaba del margen por cliente: sus ejecuciones, sus problemas, la alerta y Stripe
- [D-163](../DECISIONS.md#d-163--stripe-se-trae-solo-cada-día-y-el-cliente-desde-node-sin-sdk) — Stripe se trae solo cada día, y el cliente desde Node sin SDK
- [D-164](../DECISIONS.md#d-164--clientbeta-en-python-las-llamadas-que-no-se-veían) — `client.beta.*` en Python: las llamadas que no se veían
- [D-173](../DECISIONS.md#d-173--retención-por-proyecto-y-borrar-lo-de-una-persona-o-un-cliente) — Retención por proyecto, y borrar lo de una persona o un cliente
- [D-179](../DECISIONS.md#d-179--margen-por-cliente-lo-evitable-de-cada-uno-otras-monedas-y-su-columna) — Margen por cliente: lo evitable de cada uno, otras monedas y su columna

## Todas, en orden

**2026-09-06 — Fase 0 + arranque de Fase 1**

- [D-001](../DECISIONS.md#d-001--los-tipos-compartidos-viven-en-el-sdk-laplaceschema-y-el-backend-depende-del-sdk) — Los tipos compartidos viven en el SDK (`laplace.schema`), y el backend depende del SDK
- [D-002](../DECISIONS.md#d-002--pydantic-v2-es-dependencia-del-sdk) — Pydantic v2 es dependencia del SDK
- [D-003](../DECISIONS.md#d-003--los-mensajes-se-guardan-como-json-en-gen_aiinputmessages--gen_aioutputmessages) — Los mensajes se guardan como JSON en `gen_ai.input.messages` / `gen_ai.output.messages`
- [D-004](../DECISIONS.md#d-004--transporte-otlphttp-con-codificación-protobuf) — Transporte OTLP/HTTP con codificación protobuf
- [D-005](../DECISIONS.md#d-005--el-coste-se-calcula-en-el-backend-en-la-ingesta-y-se-guarda-desglosado-por-span) — El coste se calcula en el backend, en la ingesta, y se guarda desglosado por span
- [D-006](../DECISIONS.md#d-006--la-tabla-de-precios-es-un-json-versionado-en-el-repo) — La tabla de precios es un JSON versionado en el repo
- [D-007](../DECISIONS.md#d-007--dedup_hash-se-calcula-en-la-ingesta-no-en-el-sdk) — `dedup_hash` se calcula en la ingesta, no en el SDK
- [D-008](../DECISIONS.md#d-008--sin-vista-materializada-de-rollup-en-fase-1-group-by-sobre-spans-final) — Sin vista materializada de rollup en Fase 1: `GROUP BY` sobre `spans FINAL`
- [D-008b](../DECISIONS.md#d-008b--la-lista-de-trazas-no-filtra-por-ventana-temporal-por-defecto) — La lista de trazas no filtra por ventana temporal por defecto
- [D-009](../DECISIONS.md#d-009--los-payloads-se-guardan-completos-y-sin-truncar) — Los payloads se guardan completos y sin truncar
- [D-010](../DECISIONS.md#d-010--sin-auth-en-la-fase-1-clerk-cuando-llegue-el-cloud) — Sin auth en la Fase 1; Clerk cuando llegue el cloud
- [D-011](../DECISIONS.md#d-011--postgres-guarda-metadatos-y-las-tablas-reservadas-de-fases-34) — Postgres guarda metadatos y las tablas reservadas de Fases 3–4
- [D-012](../DECISIONS.md#d-012--frontend-nextjs-app-router--typescript--tailwind-sin-librería-de-componentes) — Frontend: Next.js (App Router) + TypeScript + Tailwind, sin librería de componentes
- [D-013](../DECISIONS.md#d-013--empaquetado-python-con-hatchling-gestor-js-npm) — Empaquetado Python con Hatchling; gestor JS: npm
- [D-014](../DECISIONS.md#d-014--nombres-de-span-convención-genai-para-llm-nombre-propio-para-el-resto) — Nombres de span: convención GenAI para LLM, nombre propio para el resto
- [D-015](../DECISIONS.md#d-015--el-almacenamiento-vive-detrás-de-un-protocol-spanstore) — El almacenamiento vive detrás de un `Protocol` (`SpanStore`)
- [D-016](../DECISIONS.md#d-016--el-streaming-se-registra-pero-todavía-no-se-acumula) — El streaming se registra pero todavía no se acumula
- [D-017](../DECISIONS.md#d-017--límite-de-1-mib-por-payload-con-marca-explícita) — Límite de 1 MiB por payload, con marca explícita
- [D-018](../DECISIONS.md#d-018--python-310-como-mínimo) — Python 3.10 como mínimo
- [D-019](../DECISIONS.md#d-019--sin-cli-laplace-todavía) — Sin CLI `laplace` todavía
- [D-020](../DECISIONS.md#d-020--un-cliente-de-clickhouse-por-hilo) — Un cliente de ClickHouse por hilo
- [D-021](../DECISIONS.md#d-021--postgres-se-publica-en-el-puerto-5433-del-host) — Postgres se publica en el puerto 5433 del host
- [D-022](../DECISIONS.md#d-022--el-coste-se-guarda-y-se-muestra-en-usd) — El coste se guarda y se muestra en USD
- [D-023](../DECISIONS.md#d-023--el-cursor-de-paginación-es-inicio-trace_id-no-sólo-la-fecha) — El cursor de paginación es `(inicio, trace_id)`, no sólo la fecha
- [D-024](../DECISIONS.md#d-024--los-alias-de-las-agregaciones-no-pueden-llamarse-como-su-columna) — Los alias de las agregaciones no pueden llamarse como su columna
- [D-025](../DECISIONS.md#d-025--hay-dos-niveles-de-prueba-y-el-segundo-se-salta-solo) — Hay dos niveles de prueba, y el segundo se salta solo

**2026-09-06 — Hallazgos de la verificación de la Fase 0-1**

- [D-026](../DECISIONS.md#d-026--final-sólo-donde-no-cuesta-fuera-de-la-apertura-de-traza) — `FINAL` sólo donde no cuesta: fuera de la apertura de traza
- [D-027](../DECISIONS.md#d-027--índice-de-salto-sobre-trace_id) — Índice de salto sobre `trace_id`
- [D-028](../DECISIONS.md#d-028--el-flush-de-salida-tiene-un-tope-de-tiempo-que-se-cumple-de-verdad) — El flush de salida tiene un tope de tiempo que se cumple de verdad
- [D-029](../DECISIONS.md#d-029--las-pruebas-borran-sus-datos-al-terminar) — Las pruebas borran sus datos al terminar
- [D-030](../DECISIONS.md#d-030--fichero-de-licencia) — Fichero de licencia

**2026-09-07 — Interfaz definitiva y motor de detección (Fase 2)**

- [D-031](../DECISIONS.md#d-031--la-ui-reproduce-el-mock-se-cae-tailwind-y-se-usa-css-propio) — La UI reproduce el mock; se cae Tailwind y se usa CSS propio
- [D-032](../DECISIONS.md#d-032--las-pantallas-de-diagnóstico-necesitaban-adelantar-la-fase-2) — Las pantallas de diagnóstico necesitaban adelantar la Fase 2
- [D-033](../DECISIONS.md#d-033--las-reglas-no-pueden-solaparse-se-descuenta-el-doble-conteo) — Las reglas no pueden solaparse: se descuenta el doble conteo
- [D-034](../DECISIONS.md#d-034--un-hallazgo-que-no-cuesta-dinero-lo-dice-no-lo-disimula) — Un hallazgo que no cuesta dinero lo dice, no lo disimula
- [D-035](../DECISIONS.md#d-035--no-se-muestra-ninguna-métrica-de-calidad-del-modelo-alternativo) — No se muestra ninguna métrica de calidad del modelo alternativo
- [D-036](../DECISIONS.md#d-036--el-coste-se-muestra-en-usd-leyendo-la-moneda-de-la-api) — El coste se muestra en USD, leyendo la moneda de la API
- [D-037](../DECISIONS.md#d-037--la-consulta-que-se-enseña-es-la-que-se-ejecuta) — La consulta que se enseña es la que se ejecuta
- [D-038](../DECISIONS.md#d-038--el-identificador-de-un-hallazgo-es-determinista-no-se-guarda) — El identificador de un hallazgo es determinista, no se guarda
- [D-039](../DECISIONS.md#d-039--ordenar-por-coste-no-es-paginable-con-cursor) — Ordenar por coste no es paginable con cursor
- [D-040](../DECISIONS.md#d-040--el-proyecto-y-el-rango-viven-en-la-url) — El proyecto y el rango viven en la URL

**2026-09-07 — Deuda que invalidaba las cifras (§1) y modo dual real (§2)**

- [D-041](../DECISIONS.md#d-041--los-precios-se-verifican-contra-la-página-oficial-con-fuente-y-fecha) — Los precios se verifican contra la página oficial, con fuente y fecha
- [D-042](../DECISIONS.md#d-042--resolver-por-prefijo-no-puede-saltar-de-versión) — Resolver por prefijo no puede saltar de versión
- [D-043](../DECISIONS.md#d-043--un-modelo-sin-tarifa-cuesta-no-lo-sabemos-no-cero) — Un modelo sin tarifa cuesta "no lo sabemos", no cero
- [D-044](../DECISIONS.md#d-044--streaming-se-acumulan-tokens-contenido-y-coste) — Streaming: se acumulan tokens, contenido y coste
- [D-045](../DECISIONS.md#d-045--no-se-inyecta-stream_options-en-la-petición-del-usuario) — No se inyecta `stream_options` en la petición del usuario
- [D-046](../DECISIONS.md#d-046--se-proyecta-sobre-los-días-observados-no-sobre-los-que-pide-el-selector) — Se proyecta sobre los días observados, no sobre los que pide el selector
- [D-047](../DECISIONS.md#d-047--el-modo-avanzado-se-oculta-en-diagnóstico-no-se-muestra-en-avanzado) — El modo avanzado se OCULTA en diagnóstico, no se muestra en avanzado
- [D-048](../DECISIONS.md#d-048--qué-añade-avanzado-en-cada-pantalla) — Qué añade Avanzado en cada pantalla
- [D-049](../DECISIONS.md#d-049--las-piezas-compartidas-de-los-tests-viven-en-helperspy-no-en-conftestpy) — Las piezas compartidas de los tests viven en `helpers.py`, no en `conftest.py`
- [D-050](../DECISIONS.md#d-050--input_tokens-es-el-total-facturable-con-la-caché-dentro) — `input_tokens` es el total facturable, con la caché dentro
- [D-051](../DECISIONS.md#d-051--ante-un-metro-de-facturación-que-no-se-puede-determinar-se-cobra-el-estándar-y-se-dice) — Ante un metro de facturación que no se puede determinar, se cobra el estándar y se dice
- [D-052](../DECISIONS.md#d-052--el-sufijo-de-prefijo-válido-es-una-lista-cerrada-de-palabras-no-cualquier-palabra) — El sufijo de prefijo válido es una lista cerrada de palabras, no cualquier palabra
- [D-053](../DECISIONS.md#d-053--auditoría-de-identificadores-no-había-ninguno-inventado-faltaban-cinco-reales) — Auditoría de identificadores: no había ninguno inventado, faltaban cinco reales
- [D-054](../DECISIONS.md#d-054--el-descuento-de-lote-y-el-recargo-regional-viven-en-sources-no-en-cada-modelo) — El descuento de lote y el recargo regional viven en `sources`, no en cada modelo
- [D-055](../DECISIONS.md#d-055--el-ahorro-de-la-regla-de-contexto-fijo-descuenta-lo-que-cuesta-escribir-la-caché) — El ahorro de la regla de contexto fijo descuenta lo que cuesta escribir la caché
- [D-056](../DECISIONS.md#d-056--el-agente-de-ejemplo-usa-modelos-vigentes-y-genera-contexto-fijo) — El agente de ejemplo usa modelos vigentes y genera contexto fijo
- [D-057](../DECISIONS.md#d-057--cambiar-de-modelo-y-activar-la-caché-se-cuentan-encadenados-no-sumados) — Cambiar de modelo y activar la caché se cuentan encadenados, no sumados
- [D-058](../DECISIONS.md#d-058--los-días-observados-los-usa-todo-el-motor-no-sólo-el-héroe) — Los días observados los usa todo el motor, no sólo el héroe
- [D-059](../DECISIONS.md#d-059--las-reglas-agrupan-por-nombre-del-paso-modelo--resuelto-en-d-060) — ~~Las reglas agrupan por (nombre del paso, modelo)~~ — resuelto en D-060
- [D-060](../DECISIONS.md#d-060--un-paso-es-llamada-hecha-desde-el-mismo-sitio-y-con-las-mismas-instrucciones) — Un paso es «llamada hecha desde el mismo sitio y con las mismas instrucciones»
- [D-061](../DECISIONS.md#d-061--el-descuento-anti-doble-conteo-cruza-por-paso-con-vuelta-al-nombre) — El descuento anti-doble-conteo cruza por paso, con vuelta al nombre
- [D-062](../DECISIONS.md#d-062--las-repeticiones-se-detectan-por-entrada-pero-se-reportan-por-paso) — Las repeticiones se detectan por entrada pero se reportan por paso
- [D-063](../DECISIONS.md#d-063--el-descuento-quita-llamadas-no-sólo-tokens) — El descuento quita llamadas, no sólo tokens
- [D-064](../DECISIONS.md#d-064--la-batería-del-doble-conteo-se-amplía-nunca-se-sustituye) — La batería del doble conteo se amplía, nunca se sustituye
- [D-065](../DECISIONS.md#d-065--dos-agentes-de-ejemplo-y-el-que-manda-es-el-mal-instrumentado) — Dos agentes de ejemplo, y el que manda es el mal instrumentado
- [D-066](../DECISIONS.md#d-066--la-traducción-de-fila-a-modelo-se-comparte-entre-los-dos-almacenes) — La traducción de fila a modelo se comparte entre los dos almacenes
- [D-067](../DECISIONS.md#d-067--cómo-lo-hemos-detectado-enseña-la-consulta-del-almacén-que-respondió) — «Cómo lo hemos detectado» enseña la consulta del almacén que respondió
- [D-068](../DECISIONS.md#d-068--los-drivers-de-la-nube-son-un-extra-no-una-dependencia) — Los drivers de la nube son un extra, no una dependencia
- [D-069](../DECISIONS.md#d-069--la-interfaz-se-sirve-desde-el-mismo-origen-que-la-api-y-se-exporta-a-html) — La interfaz se sirve desde el mismo origen que la API, y se exporta a HTML
- [D-070](../DECISIONS.md#d-070--laplace-demo-manda-datos-inventados-y-lo-dice-tres-veces) — `laplace demo` manda datos inventados, y lo dice tres veces
- [D-071](../DECISIONS.md#d-071--sqlite-deduplica-por-clave-primaria-no-por-motor-de-fusión) — SQLite deduplica por clave primaria, no por motor de fusión
- [D-072](../DECISIONS.md#d-072--la-demo-no-puede-sembrar-el-generador-de-números-aleatorios) — La demo no puede sembrar el generador de números aleatorios

**2026-09-08 — La proyección deja de mentir, y las alertas a Slack (§3)**

- [D-073](../DECISIONS.md#d-073--por-debajo-de-un-día-de-datos-no-se-proyecta-a-mes-se-enseña-lo-gastado) — Por debajo de un día de datos no se proyecta a mes; se enseña lo gastado
- [D-074](../DECISIONS.md#d-074--una-alerta-por-hallazgo-y-periodo-de-calma-agrupación-y-silencio) — Una alerta por hallazgo y periodo de calma: agrupación *y* silencio
- [D-075](../DECISIONS.md#d-075--las-alertas-son-el-mismo-código-en-local-y-en-la-nube-el-estado-va-donde-ya-va-lo-mutable) — Las alertas son el mismo código en local y en la nube; el estado va donde ya va lo mutable
- [D-076](../DECISIONS.md#d-076--un-hallazgo-con-coste-no-fiable-se-anuncia-como-suelo-nunca-como-cifra) — Un hallazgo con coste no fiable se anuncia como suelo, nunca como cifra

**2026-09-08 — El panel (§4) y el modo en vivo**

- [D-077](../DECISIONS.md#d-077--el-panel-mide-por-unidad-de-trabajo-y-dice-la-lectura-con-palabras) — El panel mide por unidad de trabajo, y dice la lectura con palabras
- [D-078](../DECISIONS.md#d-078--un-pico-se-atribuye-con-datos-o-se-dice-que-no-se-sabe) — Un pico se atribuye con datos o se dice que no se sabe
- [D-079](../DECISIONS.md#d-079--modo-en-vivo-por-polling-los-websockets-son-una-evolución-no-un-pendiente) — Modo en vivo por polling; los WebSockets son una evolución, no un pendiente
- [D-080](../DECISIONS.md#d-080--el-periodo-anterior-tiene-que-ser-utilizable-o-no-hay-comparación) — El periodo anterior tiene que ser utilizable, o no hay comparación
- [D-081](../DECISIONS.md#d-081--la-interfaz-del-árbol-gana-a-la-copia-del-paquete-y-se-dice-cuál-se-sirve) — La interfaz del árbol gana a la copia del paquete, y se dice cuál se sirve
- [D-082](../DECISIONS.md#d-082--la-duración-de-la-serie-se-redondea-al-milisegundo-en-sqlite) — La duración de la serie se redondea al milisegundo en SQLite

**2026-09-12 — Evaluaciones (§5)**

- [D-083](../DECISIONS.md#d-083--el-veredicto-de-persona-y-el-de-máquina-no-se-mezclan-y-la-separación-es-estructural) — El veredicto de persona y el de máquina no se mezclan, y la separación es estructural
- [D-084](../DECISIONS.md#d-084--el-almacén-de-metadatos-pasa-a-tener-dos-implementaciones-de-verdad) — El almacén de metadatos pasa a tener dos implementaciones de verdad
- [D-085](../DECISIONS.md#d-085--los-casos-salen-de-tráfico-real-y-se-puede-ver-de-dónde) — Los casos salen de tráfico real, y se puede ver de dónde
- [D-086](../DECISIONS.md#d-086--laplace-no-ejecuta-el-agente-de-nadie-la-tirada-la-corre-el-sdk) — Laplace no ejecuta el agente de nadie: la tirada la corre el SDK
- [D-087](../DECISIONS.md#d-087--ningún-porcentaje-de-acierto-sin-su-guarda-y-ningún-ganador-sin-margen) — Ningún porcentaje de acierto sin su guarda, y ningún ganador sin margen
- [D-088](../DECISIONS.md#d-088--lo-que-cuesta-juzgar-es-coste-real-se-mide-igual-y-se-enseña-aparte) — Lo que cuesta juzgar es coste real, se mide igual y se enseña aparte
- [D-089](../DECISIONS.md#d-089--qué-añade-avanzado-en-evaluaciones) — Qué añade Avanzado en Evaluaciones

**2026-09-12 — Prompts (§6)**

- [D-090](../DECISIONS.md#d-090--la-versión-de-un-prompt-se-escribe-en-la-traza-y-sólo-si-se-ha-comprobado) — La versión de un prompt se escribe en la traza, y sólo si se ha comprobado
- [D-091](../DECISIONS.md#d-091--servir-prompts-mete-a-laplace-en-el-camino-caliente-y-eso-hay-que-pagarlo) — Servir prompts mete a Laplace en el camino caliente, y eso hay que pagarlo
- [D-092](../DECISIONS.md#d-092--un-pico-se-atribuye-a-un-prompt-por-las-trazas-nunca-por-la-hora-del-despliegue) — Un pico se atribuye a un prompt por las trazas, nunca por la hora del despliegue
- [D-093](../DECISIONS.md#d-093--sin-adoptar-la-gestión-de-prompts-la-pestaña-enseña-lo-que-dicen-las-trazas) — Sin adoptar la gestión de prompts, la pestaña enseña lo que dicen las trazas
- [D-094](../DECISIONS.md#d-094--en-una-comparación-a-vs-b-con-qué-prompt-corrió-cada-lado-sale-de-las-trazas) — En una comparación A vs B, con qué prompt corrió cada lado sale de las trazas
- [D-095](../DECISIONS.md#d-095--qué-añade-avanzado-en-prompts) — Qué añade Avanzado en Prompts

**2026-09-12 — Cobertura, autenticación y proveedores de verdad**

- [D-096](../DECISIONS.md#d-096--la-cobertura-va-delante-del-dinero-no-en-avanzado) — La cobertura va delante del dinero, no en Avanzado
- [D-097](../DECISIONS.md#d-097--la-autenticación-deniega-por-defecto-y-el-modo-local-es-una-exención-escrita) — La autenticación deniega por defecto, y el modo local es una exención escrita
- [D-098](../DECISIONS.md#d-098--las-integraciones-se-prueban-contra-los-sdk-reales-con-el-transporte-falso) — Las integraciones se prueban contra los SDK reales, con el transporte falso

**2026-09-12 — Determinismo, escritura de caché y nombres de modelo**

- [D-099](../DECISIONS.md#d-099--ninguna-consulta-escoge-una-fila-cualquiera-y-hay-un-guardia-que-lo-vigila) — Ninguna consulta escoge «una fila cualquiera», y hay un guardia que lo vigila
- [D-100](../DECISIONS.md#d-100--los-nombres-de-modelo-de-los-tests-y-los-ejemplos-también-caducan) — Los nombres de modelo de los tests y los ejemplos también caducan
- [D-101](../DECISIONS.md#d-101--escribir-en-caché-se-cobra-también-en-openai) — Escribir en caché se cobra, también en OpenAI

**2026-09-17 — Los proveedores, contra un modelo local**

- [D-102](../DECISIONS.md#d-102--un-modelo-local-para-ejercitar-el-camino-entero-sin-pagar-nada) — Un modelo local para ejercitar el camino entero sin pagar nada
- [D-103](../DECISIONS.md#d-103--lo-simulado-en-un-fichero-aparte-y-con-el-alcance-escrito-en-la-cabecera) — Lo simulado, en un fichero aparte y con el alcance escrito en la cabecera
- [D-104](../DECISIONS.md#d-104--el-alcance-escrito-tres-veces-porque-es-lo-que-se-malinterpreta) — El alcance, escrito tres veces, porque es lo que se malinterpreta

**2026-09-18 — Una corrección, y el agente mediocre**

- [D-105](../DECISIONS.md#d-105--ollama-sí-tiene-caché-corrige-d-103-y-d-104) — Ollama sí tiene caché: corrige D-103 y D-104

**2026-09-21 — Lo que el agente mediocre destapó**

- [D-106](../DECISIONS.md#d-106--el-sitio-de-un-paso-es-el-camino-de-llamada-no-el-nombre-de-la-función) — El sitio de un paso es el camino de llamada, no el nombre de la función
- [D-107](../DECISIONS.md#d-107--no-lo-sabemos-no-es-cero-y-ahora-hay-un-guardia-que-lo-impide) — «No lo sabemos» no es «cero», y ahora hay un guardia que lo impide
- [D-108](../DECISIONS.md#d-108--las-reglas-de-dinero-tienen-que-funcionar-sin-tarifa) — Las reglas de dinero tienen que funcionar sin tarifa
- [D-109](../DECISIONS.md#d-109--la-regla-de-bucles-que-era-el-diferenciador-y-no-existía) — La regla de bucles, que era el diferenciador y no existía
- [D-110](../DECISIONS.md#d-110--tres-textos-que-decían-algo-falso) — Tres textos que decían algo falso

**2026-09-22 — La nube, ejecutada; y las medias que quedaban**

- [D-111](../DECISIONS.md#d-111--leer-de-caché-también-se-cobra-y-la-regla-no-lo-miraba) — Leer de caché también se cobra, y la regla no lo miraba
- [D-112](../DECISIONS.md#d-112--las-medias-que-quedaban-y-la-nube-ejecutada-de-verdad) — Las medias que quedaban, y la nube ejecutada de verdad
- [D-113](../DECISIONS.md#d-113--un-hallazgo-que-el-motor-encuentra-tiene-que-saber-explicarse) — Un hallazgo que el motor encuentra tiene que saber explicarse
- [D-114](../DECISIONS.md#d-114--un-motivo-no-basta-con-que-exista-tiene-que-ser-cierto) — Un motivo no basta con que exista: tiene que ser cierto
- [D-115](../DECISIONS.md#d-115--lo-que-d-106-dejó-detrás-step_key-leído-con-su-significado-anterior) — Lo que D-106 dejó detrás: `step_key` leído con su significado anterior
- [D-116](../DECISIONS.md#d-116--un-test-que-falla-por-el-reloj-es-peor-que-no-tenerlo) — Un test que falla por el reloj es peor que no tenerlo
- [D-117](../DECISIONS.md#d-117--la-quinta-cara-del-doble-conteo-el-bucle-contra-el-modelo-caro) — La quinta cara del doble conteo: el bucle contra el modelo caro
- [D-118](../DECISIONS.md#d-118--el-héroe-cuando-el-reparto-no-reparte-y-la-cifra-que-explica-el-hallazgo) — El héroe cuando el reparto no reparte, y la cifra que explica el hallazgo
- [D-119](../DECISIONS.md#d-119--un-bucle-visto-desde-dos-alturas-del-árbol-es-un-problema-no-dos) — Un bucle visto desde dos alturas del árbol es un problema, no dos
- [D-120](../DECISIONS.md#d-120--un-solo-sitio-que-sabe-escribir-un-número) — Un solo sitio que sabe escribir un número
- [D-121](../DECISIONS.md#d-121--el-punto-ciego-de-d-097-era-una-fuga-entre-clientes) — El punto ciego de D-097 era una fuga entre clientes
- [D-122](../DECISIONS.md#d-122--diez-cosas-que-un-cliente-habría-visto-en-su-primera-tarde) — Diez cosas que un cliente habría visto en su primera tarde
- [D-123](../DECISIONS.md#d-123--lo-que-el-usuario-decide-en-una-pantalla-y-no-en-variables-de-entorno) — Lo que el usuario decide, en una pantalla y no en variables de entorno
- [D-124](../DECISIONS.md#d-124--el-rigor-delante-tapaba-lo-que-el-rigor-protege) — El rigor delante tapaba lo que el rigor protege
- [D-125](../DECISIONS.md#d-125--que-se-vea-como-un-producto-sin-cambiar-lo-que-dice) — Que se vea como un producto, sin cambiar lo que dice
- [D-126](../DECISIONS.md#d-126--el-sql-de-nube-de-d-122-a-d-125-ejecutado) — El SQL de nube de D-122 a D-125, ejecutado
- [D-127](../DECISIONS.md#d-127--cuentas-las-personas-entran-los-agentes-escriben-con-clave) — Cuentas: las personas entran, los agentes escriben con clave
- [D-128](../DECISIONS.md#d-128--la-auditoría-de-septiembre-nueve-costuras-y-una-red-por-cada-una) — La auditoría de septiembre: nueve costuras, y una red por cada una
- [D-129](../DECISIONS.md#d-129--la-deuda-de-la-auditoría-lo-que-escala-lo-que-se-reparte-y-lo-que-se-ve) — La deuda de la auditoría: lo que escala, lo que se reparte y lo que se ve
- [D-130](../DECISIONS.md#d-130--lo-que-d-129-dejó-anotado-hecho) — Lo que D-129 dejó anotado, hecho
- [D-131](../DECISIONS.md#d-131--p3-lo-menor-que-también-se-equivocaba-en-silencio) — P3: lo menor, que también se equivocaba en silencio

**2026-09-24 — Revisión de diseño del inicio**

- [D-132](../DECISIONS.md#d-132--escalas-cerradas-contraste-aa-y-el-inicio-en-dos-columnas) — Escalas cerradas, contraste AA y el inicio en dos columnas

**2026-09-24 — Rediseño visual: cristal y dos temas**

- [D-133](../DECISIONS.md#d-133--glassmorphism-rose-gold--amanecer-en-claro-y-tech-abisal-en-oscuro) — Glassmorphism, «Rose Gold & Amanecer» en claro y «Tech Abisal» en oscuro

**2026-09-26 — Fase 1 de la auditoría: lo que la pantalla afirmaba sin ser cierto**

- [D-134](../DECISIONS.md#d-134--llamadas-simultáneas-la-ficha-del-modelo-caro-generadores-y-el-paquete) — Llamadas simultáneas, la ficha del modelo caro, generadores y el paquete
- [D-135](../DECISIONS.md#d-135--lo-que-la-demo-de-un-mes-destapó-en-el-motor-y-las-pantallas-con-red) — Lo que la demo de un mes destapó en el motor, y las pantallas con red

**2026-09-26 — Fase 3: integraciones**

- [D-136](../DECISIONS.md#d-136--las-trazas-de-openinference-y-openllmetry-entendidas) — Las trazas de OpenInference y OpenLLMetry, entendidas
- [D-137](../DECISIONS.md#d-137--las-otras-puertas-al-modelo-responses-api-parse-messagesstream-y-la-respuesta-cruda) — Las otras puertas al modelo: Responses API, `parse`, `messages.stream()` y la respuesta cruda
- [D-138](../DECISIONS.md#d-138--la-tabla-de-litellm-como-capa-no-verificada) — La tabla de LiteLLM como capa no verificada
- [D-139](../DECISIONS.md#d-139--agentes-en-typescript-la-guía-y-dos-fallos-de-la-ingesta-que-destapó) — Agentes en TypeScript: la guía, y dos fallos de la ingesta que destapó
- [D-140](../DECISIONS.md#d-140--la-prueba-inestable-era-un-servidor-falso-que-no-leía-el-cuerpo) — La prueba inestable era un servidor falso que no leía el cuerpo
- [D-141](../DECISIONS.md#d-141--cerrar-la-fase-3-el-doble-conteo-el-nodo-de-langgraph-y-la-tarifa-sin-verificar-en-lo-agregado) — Cerrar la Fase 3: el doble conteo, el nodo de LangGraph y la tarifa sin verificar en lo agregado

**2026-09-26 — Fase 4: escala**

- [D-142](../DECISIONS.md#d-142--diez-millones-de-spans-al-día-qué-se-ha-medido-y-qué-se-ha-cambiado) — Diez millones de spans al día: qué se ha medido y qué se ha cambiado
- [D-143](../DECISIONS.md#d-143--el-diagnóstico-que-alguien-mira-se-recalcula-antes-de-caducar) — El Diagnóstico que alguien mira se recalcula antes de caducar
- [D-144](../DECISIONS.md#d-144--buscar-dentro-de-prompts-respuestas-y-herramientas) — Buscar dentro de prompts, respuestas y herramientas
- [D-145](../DECISIONS.md#d-145--mediana-p95-y-errores-en-el-panel-en-lugar-de-la-duración-media) — Mediana, p95 y errores en el Panel, en lugar de la duración media
- [D-146](../DECISIONS.md#d-146--la-demo-llega-hasta-ahora) — La demo llega hasta ahora
- [D-147](../DECISIONS.md#d-147--cinco-idiomas-cómo-viaja-el-idioma-y-cómo-se-escribe-una-cifra) — Cinco idiomas: cómo viaja el idioma y cómo se escribe una cifra
- [D-148](../DECISIONS.md#d-148--el-producto-entero-en-cinco-idiomas) — El producto entero en cinco idiomas
- [D-149](../DECISIONS.md#d-149--los-errores-de-la-api-en-el-idioma-de-la-petición) — Los errores de la API, en el idioma de la petición
- [D-150](../DECISIONS.md#d-150--los-pendientes-de-la-auditoría-del-rediseño-a1-a2-b1-y-la-maquetación) — Los pendientes de la auditoría del rediseño (A1, A2, B1) y la maquetación
- [D-151](../DECISIONS.md#d-151--menos-texto-lo-que-puedes-dejar-de-pagar-un-distintivo-de-confianza-y-el-porqué-plegado) — Menos texto: lo que puedes dejar de pagar, un distintivo de confianza y el porqué plegado
- [D-152](../DECISIONS.md#d-152--dónde-se-va-el-dinero-gasto-por-día-con-la-franja-evitable-y-coste-por-paso) — Dónde se va el dinero: gasto por día con la franja evitable y coste por paso
- [D-153](../DECISIONS.md#d-153--el-grafo-del-agente-en-la-vista-de-traza) — El grafo del agente en la vista de traza

**2026-09-28 — Fase 5: el cierre**

- [D-154](../DECISIONS.md#d-154--el-diagnóstico-medido-otra-vez-con-el-gráfico-dentro) — El Diagnóstico medido otra vez, con el gráfico dentro
- [D-155](../DECISIONS.md#d-155--el-oscuro-es-la-insignia-el-claro-neutro) — El oscuro es la insignia; el claro, neutro
- [D-156](../DECISIONS.md#d-156--el-producto-alrededor-del-ciclo-detectar-probar-arreglar-verificar) — El producto alrededor del ciclo: detectar, probar, arreglar, verificar
- [D-157](../DECISIONS.md#d-157--prompts-como-fuente-de-hallazgos) — Prompts como fuente de hallazgos

**2026-09-28 — El tema claro y el alto contraste**

- [D-158](../DECISIONS.md#d-158--el-claro-vuelve-a-ser-rose-gold-con-cristal-líquido) — El claro vuelve a ser Rose Gold, con cristal líquido
- [D-159](../DECISIONS.md#d-159--alto-contraste-para-el-claro-y-para-el-oscuro) — Alto contraste, para el claro y para el oscuro

**2026-09-28 — Tres decisiones que estaban abiertas**

- [D-160](../DECISIONS.md#d-160--prompts-sin-tiradas-de-evaluación-envoltorios-con--y-el-modelo-rápido-ponderado) — Prompts sin tiradas de evaluación, envoltorios con «=» y el modelo rápido ponderado

**2026-09-28 — Fase 6: margen por cliente**

- [D-161](../DECISIONS.md#d-161--qué-clientes-te-hacen-perder-dinero) — Qué clientes te hacen perder dinero
- [D-162](../DECISIONS.md#d-162--lo-que-quedaba-del-margen-por-cliente-sus-ejecuciones-sus-problemas-la-alerta-y-stripe) — Lo que quedaba del margen por cliente: sus ejecuciones, sus problemas, la alerta y Stripe
- [D-163](../DECISIONS.md#d-163--stripe-se-trae-solo-cada-día-y-el-cliente-desde-node-sin-sdk) — Stripe se trae solo cada día, y el cliente desde Node sin SDK

**2026-09-28 — Restos de fases cerradas: las integraciones sin probar**

- [D-164](../DECISIONS.md#d-164--clientbeta-en-python-las-llamadas-que-no-se-veían) — `client.beta.*` en Python: las llamadas que no se veían
- [D-165](../DECISIONS.md#d-165--las-integraciones-de-typescript-que-faltaban-probadas-y-lo-que-se-perdía) — Las integraciones de TypeScript que faltaban, probadas: y lo que se perdía

**2026-09-29 — Escala: dos semanas de histórico**

- [D-166](../DECISIONS.md#d-166--con-semanas-de-histórico-una-ventana-de-un-día-lee-el-mes-entero) — Con semanas de histórico, una ventana de un día lee el mes entero

**2026-09-29 — Funciones diferenciales: el replay contrafactual**

- [D-167](../DECISIONS.md#d-167--probar-el-modelo-barato-reenviando-las-llamadas-reales-sin-escribir-código) — Probar el modelo barato reenviando las llamadas reales, sin escribir código

**2026-09-29 — Escala: la tabla ordenada por día**

- [D-168](../DECISIONS.md#d-168--la-clave-de-ordenación-por-día-su-migración-y-los-filtros-acotados) — La clave de ordenación por día, su migración y los filtros acotados
- [D-169](../DECISIONS.md#d-169--health-nombra-el-almacén-que-hay-y-la-limpieza) — `/health` nombra el almacén que hay, y la limpieza

**2026-09-30 — Integraciones de TypeScript: LangGraph.js, el Agents SDK de OpenAI y Mastra**

- [D-170](../DECISIONS.md#d-170--los-tres-frameworks-que-faltaban-probados-y-lo-que-mastra-no-manda-donde-toca) — Los tres frameworks que faltaban, probados; y lo que Mastra no manda donde toca

**2026-09-30 — Deuda: los detalles sueltos**

- [D-171](../DECISIONS.md#d-171--ocho-detalles-de-la-lista-de-deuda-cerrados-con-su-prueba) — Ocho detalles de la lista de deuda, cerrados con su prueba
- [D-172](../DECISIONS.md#d-172--la-guardia-de-las-reglas-nuevas) — La guardia de las reglas nuevas
- [D-173](../DECISIONS.md#d-173--retención-por-proyecto-y-borrar-lo-de-una-persona-o-un-cliente) — Retención por proyecto, y borrar lo de una persona o un cliente
- [D-174](../DECISIONS.md#d-174--globalscss-en-29-hojas-un-índice-de-decisiones-y-documentación-de-usuario) — `globals.css` en 29 hojas, un índice de decisiones y documentación de usuario
- [D-175](../DECISIONS.md#d-175--las-pantallas-con-sesión-recorridas-en-un-navegador) — Las pantallas con sesión, recorridas en un navegador
- [D-176](../DECISIONS.md#d-176--el-contraste-medido-sobre-cada-pantalla) — El contraste, medido sobre cada pantalla
- [D-177](../DECISIONS.md#d-177--preagregados-por-minuto-del-diagnóstico-y-clave-por-hora) — Preagregados por minuto del Diagnóstico y clave por hora

**2026-10-01 — Funciones: caché compartida, margen por cliente, diagnóstico con modelo, seguridad**

- [D-178](../DECISIONS.md#d-178--el-mismo-prefijo-en-varios-pasos-sin-compartir-la-caché) — El mismo prefijo en varios pasos, sin compartir la caché
- [D-179](../DECISIONS.md#d-179--margen-por-cliente-lo-evitable-de-cada-uno-otras-monedas-y-su-columna) — Margen por cliente: lo evitable de cada uno, otras monedas y su columna
- [D-180](../DECISIONS.md#d-180--diagnóstico-con-modelo-que-cita-los-spans-de-los-que-sale) — Diagnóstico con modelo, que cita los spans de los que sale
- [D-181](../DECISIONS.md#d-181--redacción-de-datos-personales-y-muestreo-por-cola-en-el-sdk) — Redacción de datos personales y muestreo por cola, en el SDK
- [D-182](../DECISIONS.md#d-182--verificación-de-correo-sso-por-openid-connect-y-scim) — Verificación de correo, SSO por OpenID Connect y SCIM

**2026-10-03 — Dos cosas de una sesión local que no estaban en master**

- [D-183](../DECISIONS.md#d-183--las-medianas-del-uso-por-paso-la-misma-cuenta-en-los-dos-almacenes-y-una-llamada-con-respuesta-y-sin-recuento-ya-no-sale-gratis) — Las medianas del uso por paso, la misma cuenta en los dos almacenes; y una llamada con respuesta y sin recuento ya no sale gratis

**2026-10-03 — `laplace.guard`: cortar la ejecución mientras pasa**

- [D-184](../DECISIONS.md#d-184--un-límite-de-gasto-y-de-bucles-en-el-sdk-con-la-misma-cuenta-que-la-traza) — Un límite de gasto y de bucles en el SDK, con la misma cuenta que la traza
