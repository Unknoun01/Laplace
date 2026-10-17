# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 3 de octubre de 2026, al
cerrar el bot de PR, más allá (D-190 a D-192). Aquí sólo está **lo que
queda**: lo hecho vive en `DECISIONS.md` y en la historia de git. Léelo entero antes de tocar nada; después lee `STATUS.md` y las
últimas entradas de `DECISIONS.md` (D-168 a D-177).

## 1. Qué es y hacia dónde va

Laplace observa agentes de IA: SDK de Python (OTel GenAI) → ingesta OTLP → coste por
tramos de tokens → cinco reglas de derroche (repetición, bucle, modelo caro, contexto
fijo y versión de prompt más cara) → interfaz en cinco idiomas con modo Sencillo/Avanzado,
tema oscuro por defecto, claro Rose Gold y alto contraste. Pestañas: Diagnóstico, Probar,
Trazas, Panel, Clientes, Prompts y Ajustes. Dos despliegues con paridad probada:
`laplace ui` (un proceso + SQLite) y Docker (ClickHouse + Postgres + Next).

**Posicionamiento decidido con el usuario:**
- Función principal: **ahorro verificado**, un ciclo en cuatro pasos —detectar, probar el
  arreglo sobre llamadas reales, arreglar, verificar— con la cifra «ahorrado y
  recuperable». Ya tiene su sitio en la interfaz (D-156), y el paso «probar» del modelo
  caro ya no pide código: `laplace replay` (D-167).
- La observabilidad completa se mantiene para el análisis en profundidad.
- **Margen por cliente:** hecho (D-161 a D-163), salvo lo que espera al usuario.
- **En vez de un editor tipo n8n:** un **plano de control**. Laplace no ejecuta agentes.
  - Hecho: el grafo de todo el proyecto en el Panel (D-188) y, desde Ajustes, tope por
    ejecución, bucles y parada que aplica el SDK con copia de reserva (D-187, D-189).
  - Fuera por decisión del usuario: modelo por paso desde la interfaz y repartos A/B.

## 2. Cómo se trabaja en este repo

- **Por fases:** una rama por fase y un commit por bloque. En la sesión del 28 de
  septiembre el usuario pidió **fusionar a `master` y subir directamente** cada bloque en
  verde (`git merge --no-ff`); si no dice otra cosa, se sigue así.
- **Nada entra sin su prueba.** Primero la prueba en rojo, después el arreglo.
- **Hay que comprobar que la prueba muerde:** romper el código a propósito y verla fallar.
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-193**.
- **Todo en español:** código, comentarios, commits y textos. Los textos de la interfaz y
  del backend, en los cinco idiomas a la vez (`apps/web/lib/mensajes/*.ts`,
  `apps/backend/laplace_backend/textos/*.json`); las pruebas exigen las mismas claves.
- **Los commits terminan con** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Reglas del producto que no se tocan:**
  - Nunca afirmar lo que no se sabe.
  - Un «no lo sabemos» tiene que ser verdad.
  - Un modelo sin tarifa no cuesta cero.
  - Sin muestra suficiente no hay porcentaje, y con márgenes solapados no hay ganador.
  - No proyectar desde menos de un día de datos.
  - No contar dos veces el mismo ahorro (entre reglas, los arreglos se componen: D-157).
  - Las tiradas de evaluación no son tráfico real: ni reglas, ni Prompts, ni margen.
  - Paridad entre SQLite y ClickHouse.
- **El dinero se formatea sólo en dos sitios:** `cifras.py` (backend) y `lib/format.ts` (web), que son espejo el uno del otro.
- **El color sale sólo de los tokens** de `app/estilos/00-temas.css`; `test_tema.py` mide el contraste
  de cada tinta en los cuatro temas.

**Entorno (Windows):**
- **Pruebas del backend y lint:**
  - `.venv/Scripts/python.exe -m pytest apps/backend/tests -q` (unos 5 minutos con los almacenes de nube).
  - `.venv/Scripts/ruff.exe check apps/backend packages/sdk-python`.
- **Pruebas de la nube:** necesitan `docker compose up -d clickhouse postgres`. Sin eso, unas 90 se saltan.
- **Web:** `cd apps/web && npx tsc --noEmit`.
- **Tras tocar la web:** `.venv/Scripts/python.exe scripts/build_ui.py`, que construye `apps/web/out`, lo que sirve `laplace ui`.
- **Servidor local:** la configuración «local» de `.claude/launch.json` (puerto 8100). Tras tocar Python hay que reiniciarlo.
- **Demo:** `.venv/Scripts/python.exe -m laplace.cli demo --endpoint http://127.0.0.1:8100` carga un mes de datos, con cuatro clientes (uno que hace perder dinero).
- **Pantallas:** `test_pantallas.py` usa Playwright con Chromium, ya instalados en `.venv`. Prueban la interfaz construida en `apps/web/out`: tras un pull, `scripts/build_ui.py` antes de pasarlas, o fallan contra la vieja.
- **Estado:** la última pasada completa (D-162, en Linux con ClickHouse y Postgres) dio 902 pruebas bien y 16 saltadas —las 4 vivas que necesitan clave de proveedor y 12 que necesitan Ollama con un modelo— y una de pantalla que dependía del reloj, arreglada después. Las del espejo con la web necesitan Node 22.6 o posterior.
- **Fuera de Windows** (contenedor Linux): `.venv/bin/python` en lugar de `.venv/Scripts/python.exe`, y Playwright 1.56 para el Chromium que ya trae la máquina. Docker no arranca solo: `dockerd &` y después `docker start laplace-clickhouse laplace-postgres` (el contenedor de la sesión se reinicia y hay que repetirlo). ClickHouse no arranca con el `ulimits` del compose en un contenedor sin permiso para subirlos: `docker run` sin esa línea. Ojo con `pkill -f "laplace.cli ui"` en la misma orden que lo arranca: se mata a sí misma.
- **Mapa del código (graphify):** `uv tool install graphifyy`, `graphify install` y `/graphify .` en Claude Code dejan en `graphify-out/` (fuera de git) un grafo navegable del repo con su informe (`GRAPH_REPORT.md`). Con el grafo hecho, `/graphify query "…"` responde sobre la arquitectura sin releer los ficheros, y `/graphify . --update` sólo vuelve a leer lo cambiado. En el contenedor de la nube hay que instalarlo en cada sesión.
- **Prueba de carga:** `python scripts/carga.py --spans 10000000` genera un día de tráfico en ClickHouse y mide las pantallas con el desglose de cada lectura; `--borrar` lo quita. Con 10 millones hacen falta unos 8 GB para Docker. Quitarlos al acabar: con ellos en la base, la suite tarda horas y los `ALTER` de las pruebas se quedan sin tiempo.

## 3. Pendiente, por orden

### Espera al usuario (sección 5)
- **Stripe contra la API real:** la traída de ingresos (D-162) y la diaria (D-163) están
  probadas contra una Stripe falsa con la forma documentada de las facturas. Con una
  clave `rk_test_…`, probar una traída de verdad y ajustar lo que no case.
- **Paquete fino de TypeScript `@laplace/sdk`** (`init`, `observe`, `setContext({
  customerId })`, `getPrompt`): espera el nombre del scope en npm. Mientras tanto, un
  agente en Node usa OpenInference/OpenLLMetry y los atributos `laplace.*` (probado, D-163).
- **Las 4 pruebas vivas** que validan las cifras contra la factura del proveedor.
- **`laplace replay` contra la API real** (D-167): probado con los clientes reales y el transporte falso; con una clave, pasar un replay de verdad y comprobar que lo gastado cuadra con la factura.

### Restos de fases cerradas
- **Integraciones:** probadas contra un proveedor falso (D-164, D-165 y D-170): `client.beta.*` en Python; Anthropic por OpenInference-js y OpenLLMetry-js; el AI SDK de Vercel 7; LangChain.js; LangGraph.js; el Agents SDK de OpenAI y Mastra. **Falta pasarlas contra las API reales**, que espera una clave de proveedor (sección 5). El banco (`scripts/integraciones_js`) tiene el proveedor y el uso fijos: para una pasada real hace falta apuntar los agentes a la API y un verificador que no espere el uso exacto.
- **Escala: ya no está en el objetivo (D-212).** D-177 dejó el día en 1,1 s y la semana en 6 s con diez millones de spans al día. Desde entonces han entrado cinco lecturas en crudo (D-178, D-193 a D-196), y medido otra vez (en el portátil, con mucho ruido) el día tarda 19–35 s y la semana 59–181 s. Lo que queda, por orden de lo que pesa:
  1. las tres lecturas en crudo (caché, JSON, truncadas), precalculadas por hora como el historial;
  2. la memoria del historial con las trazas que cruzan (3,8 GiB en una consulta);
  3. las candidatas de bucles y repeticiones, desde `pre_trazas`;
  4. los recuentos exactos y las medianas (el diseño está en D-212; el p95 es aproximado y no se puede exigir idéntico);
  5. un tope (TTL) para las tablas de registro de ClickHouse, que meten ruido en todas las medidas.
  Hay que volver a medir en el contenedor de 4 núcleos. Las instalaciones que ya existen se migran a mano con `python -m laplace_backend.storage.migrar_orden`.

### Siguiente: funciones diferenciales (confirmar el orden con el usuario)
- **Replay contrafactual, más allá** (hecho: lo básico, D-167; las herramientas, D-205; las imágenes, los bloques entre proveedores y la API Responses, D-213; el botón en la versión local, D-214). Queda pasarlo contra las API reales (espera una clave, sección 5).
- **Bot de pull requests, más allá** (hecho: el cambio de modelo, D-185; `cache_control` y el tope de vueltas sobre el árbol de sintaxis, D-190; el webhook que marca el hallazgo arreglado al fusionar, D-191; sus pantallas, D-192; la
  frontera en el despliegue a producción por el aviso `deployment_status`, D-204). Lo que queda:
  - arreglos que no se derivan del código (acortar un contexto, cambiar un prompt): harían falta un modelo que los escriba y una persona que los mire, y por eso no se hicieron (D-190);
  - otros lenguajes: desde D-218 el bot encuentra la llamada con los atributos estándar
    `code.*` de cualquier SDK, pero sólo escribe arreglos en Python. Para TypeScript y
    JavaScript haría falta leer el árbol de sintaxis, y el camino sería `tree-sitter`
    como extra opcional;
  - la señal de despliegue: el aviso `deployment_status` (D-204) y, si no llega,
    `service.version` cuando se puede atar al commit de la fusión (D-216). Sin ninguna de
    las dos, la frontera sigue en la fusión, y la nota lo dice.
- **Plano de control, más allá** (lo básico está hecho, D-187 a D-189: tope, bucles y
  parada desde Ajustes, y el grafo del proyecto en el Panel):
  - Modelo por paso desde la interfaz y repartos A/B: **fuera por decisión del usuario**;
    no empezar sin que lo pida.
  - La parada alcanza ya las llamadas a OpenAI y Anthropic que traza OpenInference u
    OpenLLMetry en un proceso Python con `laplace.init()` (D-206), y la pantalla dice a
    qué no llega: Node y otros lenguajes (espera el SDK de TypeScript) y los procesos sin
    el SDK. Desde D-215 se guarda de dónde viene cada span y se dice con un número qué
    parte del tráfico queda fuera; los spans de antes no lo traen.
  - El grafo del proyecto marca los pasos con hallazgos (D-203), y al pulsar un paso
    lleva a sus trazas y a las fichas de sus problemas (D-217). Desde él no se puede
    poner el tope ni la parada, ni ignorar un hallazgo: se dejó fuera, con el motivo en
    D-217.
- **Reglas nuevas** (cada una entra por `detect()` y `detail()`, con su prueba de catálogo;
  fase `fase-reglas-diagnostico`). Hechas: la salida truncada y rehecha (D-193; la
  «descartada» no se puede saber y queda fuera), los reintentos por JSON mal formado
  (D-194) y el historial que crece sin límite (D-195, sólo tokens). Lo que queda, cada
  una con su decisión de diseño pendiente:
  - hechas en `fase-reglas-nuevas-2`: la prueba de catálogo de OpenAI, que destapó una
    ficha que pedía `cache_control` (D-196), la medida de las tres lecturas en crudo
    con la carga arreglada (D-197), y la regla de la API de lotes (D-199): con la marca
    `laplace-puede-esperar` hay dinero, sin ella una pregunta;
  - la memoria del historial, de 1,09 GiB a 188 MiB con un filtro previo que sí
    descarta (D-198), y el tiempo, de 1,77 s a 1,28 s en 7 días con la tabla
    `pre_historial` (D-207). Lo que queda son las trazas que cruzan las horas y
    componer las filas, unos 0,3 s cada una; en un día no gana nada;
  - caché semántica hecha (D-200): la misma pregunta salvo los números y la misma
    respuesta exacta en ejecuciones distintas, «hasta X» sin dinero en el total. Lo
    parecido, con simhash, también está (`cache_parecida`, D-219), sin dinero en el total.
    Con embeddings no: pediría un modelo;
  - lotes, el peor caso sin medir: muchas ejecuciones sin usuario en un proyecto que sí
    lo pone en otras (la carga no tiene ese tráfico) (D-199);
  - el historial con dinero: un tope que se pueda defender, sacado de un replay con
    evaluación (D-195);
- **Lo que dejaron D-178 a D-182**, hecho: los diagnósticos con modelo se borran con su
  sujeto, y el rechazado deja apuntado su coste (D-208); la caché compartida en la demo
  (D-209); con muestreo, lo que falta del gasto sale como un intervalo, sin tocar los
  hallazgos ni la proyección (D-210); SAML con `xmlsec` opcional, y los grupos de SCIM
  dan los roles (D-211). SAML está probado contra un proveedor de identidad falso: falta
  uno de verdad.

## 4. Deuda y detalles sueltos
- **Los navegadores que no son Chromium**, que la auditoría del rediseño no revisó
  (`docs/auditoria-rediseno.md`). Safari trata distinto `backdrop-filter` y `color-mix`,
  y todo el color sale de `color-mix`; el claro Rose Gold depende del cristal y de
  `saturate()`. En el contenedor sólo hay Chromium, así que hace falta un Mac o
  WebKit de Playwright. Las pantallas con sesión y los flujos de escritura ya se
  recorren en `test_pantallas_cuentas.py` (D-175).
- **En español a propósito, y por decidir si sigue así** (D-148): la línea de órdenes
  (`laplace ui`, `laplace demo`) y los logs.
- **Móvil: sin app, y la web adaptada se queda como está** (decisión del usuario, 17 de
  octubre). La barra de siete pestañas ocupa tres filas en móvil (3 + 3 + 1): cabe y se
  prueba. No hay que tocarla salvo que se añada otra pestaña.
- La documentación de usuario (`docs/usuario/`, D-174) es Markdown plano: pasarla a un sitio (Mintlify, Docusaurus o Starlight) cuando haya dominio.
- `STATUS.md` se puso al día al cerrar las fases 5 y 6: mantenerlo así al cerrar cada fase.
- Tras la caída de Docker del 26 de septiembre quedaron apartadas `%LOCALAPPDATA%\Docker\run.viejo-*` y `docker-secrets-engine.viejo-*` con sockets bloqueados; se pueden borrar tras reiniciar Windows.

## 5. Lo que sólo puede hacer el usuario
- Reservar `laplace-trace` y `laplace-backend` en PyPI y registrar este repositorio como «trusted publisher» con el entorno `pypi`.
- Reservar un scope en npm, por ejemplo `@laplace-ai`, y decir cuál es: el paquete de TypeScript espera el nombre.
- Decidir el dominio: `laplace.dev`, `laplace.ai` y `laplace.sh` están cogidos; `uselaplace.com` parecía libre, pero hay que confirmarlo.
- Poner una clave de proveedor para las 4 pruebas vivas que validan las cifras contra la factura real (`LAPLACE_LIVE_TESTS=1`).
- Una clave de pruebas de Stripe (`rk_test_…`) para probar la traída de ingresos contra la API real.
- **Registrar la GitHub App de Laplace** (D-185): permisos de contenido y pull requests, y poner `LAPLACE_GITHUB_APP_ID` y `LAPLACE_GITHUB_APP_PRIVATE_KEY` en el despliegue. Para el aviso al fusionar (D-191), suscribirla al evento *Pull request* con la URL `…/api/github/webhook` y poner su secreto en `LAPLACE_GITHUB_WEBHOOK_SECRET`. Para la señal de despliegue (D-204), suscribirla también a *Deployment status* con el permiso *Deployments: read*. Sin App, el bot funciona con un token por proyecto y el webhook se pone a mano en cada repositorio.

## 6. Negocio (del informe de auditoría)
- **Mensaje:** «Laplace encuentra el dinero que tu agente tira, te dice cómo arreglarlo y demuestra cuánto has dejado de pagar». Complementario a Langfuse, no rival.
- **Cliente objetivo:** startups y scaleups con agentes en producción que gastan entre 2.000 y 100.000 $/mes en modelos.
- **Precios propuestos:**
  - Local gratis;
  - Free en la nube: 50.000 spans y 14 días;
  - Team: 49 $/mes;
  - Business: 299 $/mes;
  - Enterprise: desde 18.000 $/año.

  Sin cobro por usuario, y garantía de «10 veces lo que pagas o te devolvemos el mes».
- **Ganchos de venta:**
  - `laplace audit` con un informe compartible;
  - SEO con la tabla de precios;
  - guía de migración desde Helicone, que está en modo mantenimiento;
  - lanzamiento en Show HN y Product Hunt;
  - el mercado hispano como cabeza de playa.
- **Informe completo:** https://claude.ai/artifact/TKcgFgKjLfhabr3AARWZMo (privado del usuario).
