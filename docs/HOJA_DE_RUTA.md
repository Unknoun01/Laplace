# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 26 de septiembre de 2026.
Léelo entero antes de tocar nada; después lee `STATUS.md` y las últimas entradas de
`DECISIONS.md` (D-134 a D-136).

## 1. Qué es y hacia dónde va

Laplace observa agentes de IA: SDK de Python (OTel GenAI) → ingesta OTLP → coste por
tramos de tokens → cuatro reglas de derroche (repetición, bucle, modelo caro, contexto
fijo) → interfaz en español con modo Sencillo/Avanzado. Dos despliegues con paridad
probada: `laplace ui` (un proceso + SQLite) y Docker (ClickHouse + Postgres + Next).

**Posicionamiento decidido con el usuario:**
- Función principal: **ahorro verificado**, un ciclo en cuatro pasos:
  1. Detectar el derroche.
  2. Probar el arreglo sobre llamadas reales.
  3. Arreglar.
  4. Verificar el ahorro después.

  La cifra principal del producto es «ahorrado y recuperable».
- La observabilidad completa se mantiene para el análisis en profundidad.
- **Idiomas:** inglés, español, chino, francés y alemán, con i18n real.
- **Primera función nueva, después de las fases técnicas:** margen por cliente final.
- **En vez de un editor tipo n8n:** un **plano de control**.
  - Grafo del agente sacado de las trazas.
  - Controles desde la interfaz que aplica el SDK: modelo por paso, tope de gasto, botón de parada y repartos A/B.
  - Siempre con copia de reserva si Laplace no responde.

  Laplace no ejecuta agentes; queda para después del margen por cliente, y hay que confirmarlo con el usuario antes de empezar.

## 2. Cómo se trabaja en este repo

- **Por fases:** una rama por fase y un commit por bloque. Se enseña al usuario y se fusiona a `master` sólo cuando lo aprueba (`git merge --no-ff`).
- **Nada entra sin su prueba.** Primero la prueba en rojo, después el arreglo.
- **Hay que comprobar que la prueba muerde:** romper el código a propósito y verla fallar.
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-137**.
- **Todo en español:** código, comentarios, commits y textos.
- **Los commits terminan con** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Reglas del producto que no se tocan:**
  - Nunca afirmar lo que no se sabe.
  - Un «no lo sabemos» tiene que ser verdad.
  - Un modelo sin tarifa no cuesta cero.
  - Sin muestra suficiente no hay porcentaje, y con márgenes solapados no hay ganador.
  - No proyectar desde menos de un día de datos.
  - No contar dos veces el mismo ahorro.
  - Paridad entre SQLite y ClickHouse.
- **El dinero se formatea sólo en dos sitios:** `cifras.py` (backend) y `lib/format.ts` (web), que son espejo el uno del otro.

**Entorno (Windows):**
- **Pruebas del backend y lint:**
  - `.venv/Scripts/python.exe -m pytest apps/backend/tests -q` (unos 2,5 minutos).
  - `.venv/Scripts/ruff.exe check apps/backend packages/sdk-python`.
- **Pruebas de la nube:** necesitan `docker compose up -d clickhouse postgres`. Sin eso, unas 75 se saltan.
- **Web:** `cd apps/web && npx tsc --noEmit`.
- **Tras tocar la web:** `.venv/Scripts/python.exe scripts/build_ui.py`, que construye `apps/web/out`, lo que sirve `laplace ui`.
- **Servidor local:** la configuración «local» de `.claude/launch.json` (puerto 8100). Tras tocar Python hay que reiniciarlo.
- **Demo:** `.venv/Scripts/python.exe -m laplace.cli demo --endpoint http://127.0.0.1:8100` carga un mes de datos.
- **Pantallas:** `test_pantallas.py` usa Playwright con Chromium, ya instalados en `.venv`.
- **Estado al cerrar esta sesión:** 575 pruebas pasan, 0 fallan y 4 se saltan (las que necesitan clave de proveedor).

## 3. Hecho

**Fase 1 (D-134), fusionada en `master`:**
- Prompts ya no confunde llamadas simultáneas con versiones.
- La ficha de «modelo caro» se explica según el reparto real del coste y avisa del riesgo con mucho contexto.
- Formato del dinero con dos cifras significativas entre 0,01 y 1.
- La cifra grande va en Inter; comillas “” dentro de «».
- Pestaña activa y orden de la columna en móvil corregidos.
- Las trazas se abren con el id corto.
- `@observe` soporta generadores (síncronos y asíncronos).
- `paquete.yml` construye los wheels, prueba una instalación limpia y publica con una etiqueta `v*`.

**Fase 2 (D-135), fusionada en `master`:**
- **Demo de un mes** («Vuelos Laplace», unas 814 ejecuciones):
  - reloj simulado sobre la ingesta real;
  - prompt v1→v2 con fechas;
  - un pico por gpt-5.5-pro;
  - un 3 % de errores;
  - anotaciones, una comparación A/B y una repetición arreglada.

  Código en `laplace/demo.py` y `laplace_backend/demo.py`; `laplace demo` llama a `POST /api/demo`.
- **Arreglos del motor:**
  - Los bucles se agrupan por paso, no por pregunta.
  - Los títulos se desambiguan por modelo, por versión de prompt, por dónde cambian las instrucciones o como «variante n».
  - Las tiradas de evaluación (etiqueta `laplace-eval`) no generan hallazgos.
  - El contexto fijo ya no dice «no está en la tabla» de un modelo que sí está.
  - La versión de prompt baja no se pone delante del dinero.
  - Lo que dejó de ocurrir se aparta como «ya no ocurre» y no suma al ahorro.
  - Una repetición o un bucle cuyo nombre comparten varios pasos lleva la pista de sus instrucciones.
- **Pantallas en Chromium**, en CI.

**Fase 3, en curso en la rama `fase3-integraciones`, sin fusionar:**
- **Hecho (D-136):** `ingest/convenciones.py` traduce OpenInference y OpenLLMetry. Pruebas en `test_convenciones.py`.

## 4. Pendiente, por orden

### Fase 3: integraciones (terminar)
1. **SDK de OpenAI:**
   - instrumentar `client.responses.create` (síncrono, asíncrono y streaming): la Responses API, que es la que usa el Agents SDK;
   - instrumentar `chat.completions.parse`.

   Hoy sólo está `chat.completions.create` (`packages/sdk-python/laplace/integrations/openai.py`).
2. **SDK de Anthropic:** instrumentar el ayudante `client.messages.stream()` (gestor de contexto). Hoy sólo está `messages.create`.
3. **Tabla de precios:**
   - La base amplia sale de la tabla de LiteLLM (`model_prices_and_context_window.json`), cargada como capa inferior.
   - Encima va la capa verificada propia (`pricing/model_prices.json`), que manda cuando coinciden.
   - Todo lo que salga sólo de LiteLLM se marca como «tarifa no verificada», igual que ya se marca la tarifa asumida.
   - Hoy hay 56 modelos, sólo de OpenAI y Anthropic. La versión `2026.09.07b` caduca el 7 de octubre y hay tests que fallan solos cuando pasa.
   - Añadir un trabajo semanal en CI que compare las tarifas y abra una PR.
4. **Guía para agentes en TypeScript:** usar OpenLLMetry-js u OpenInference-js y apuntarlos a Laplace. Después, un paquete fino `@laplace/sdk` con `init`, `observe` y `getPrompt` (hay que reservar el scope de npm; `laplace` está cogido).
5. Contrastar con trazas reales de al menos un framework (por ejemplo LangGraph con `openinference-instrumentation-langchain`) y anotar en `STATUS.md` qué se ve y qué no.

### Fase 4: escala y rendimiento (ClickHouse)
- `async_insert=1, wait_for_async_insert=1` en las inserciones: hoy se hace una inserción síncrona por petición.
- Clave de orden `(project_id, toDate(start_time), trace_id, span_id)` más una tabla o proyección `trace_id → proyecto` para abrir trazas por id.
- `CODEC(ZSTD(3))` en las columnas de payload.
- Quitar `FINAL` de las consultas calientes (hay 28) y usar agregación con `argMax`.
- Vistas materializadas por hora (proyecto, paso y modelo) para el Panel y el Diagnóstico, y una caché de 60 s de `overview`.
- Acotar por la ventana de tiempo las subconsultas de filtros de `_where`: hoy recorren todo el histórico.
- Búsqueda en el contenido de prompts y respuestas: `tokenbf_v1` o `ngrambf_v1` en ClickHouse y FTS5 en SQLite. Hoy sólo se busca por nombre e id.
- En la ingesta:
  - `parse_spans` a un hilo aparte (`run_in_threadpool`);
  - caché de `ensure_project`.
- Latencia p50/p95 y tasa de error en el Panel.
- Prueba de carga con un objetivo escrito, por ejemplo 10 millones de spans al día con el Diagnóstico por debajo de 1,5 s.

### Fase 5: interfaz e internacionalización
- **i18n en cinco idiomas:**
  - `next-intl` en la web;
  - en el backend, claves de mensaje con parámetros en lugar de frases hechas (los títulos de los hallazgos salen del motor);
  - formato de números y moneda por idioma (en español `14,64 US$`, en inglés `$14.64`), sin romper el espejo `cifras.py` ↔ `format.ts`;
  - cuidado con las fuentes para el chino.
- **Menos texto:**
  - una frase y un «¿por qué?» plegable;
  - las guardas se muestran como un distintivo de confianza, no como un párrafo;
  - el héroe dice «Puedes dejar de pagar hasta X».
- **Gráficos en el Diagnóstico:** gasto de 30 días con la franja evitable superpuesta y coste por paso.
- **Grafo del agente** en la vista de traza.
- **Maquetación:**
  - a 1440 px sobra un tercio de la pantalla;
  - en móvil la navegación esconde «Prompts» y «Ajustes».
- **Identidad visual:** el tema oscuro como insignia y un tema claro neutro en lugar del rosa.
- **Rediseño alrededor del ciclo:** Evaluaciones pasa a ser el paso «probar» y Prompts una fuente más de hallazgos.
- **Pendientes de `docs/auditoria-rediseno.md`** que no se hayan cerrado: A1, A2 y B1.

### Fase 6: margen por cliente (primera función nueva elegida)
- `customer_id` en `set_context` y en el contrato, con paridad en los dos almacenes.
- Ingresos por cliente: primero a mano en Ajustes; después desde Stripe.
- Pantalla con coste, ingresos y margen por cliente, y el aviso de «este cliente te hace perder dinero».

### Después: funciones diferenciales (confirmar el orden con el usuario)
- **Replay contrafactual:** reenviar, con tope de gasto y permiso, las llamadas reales de un paso al modelo barato y compararlas con el juez. Sólo llamadas hoja sin herramientas con efectos. Es la prueba del paso 2 del ciclo.
- **`laplace.guard(max_usd_per_run, max_loop)`:** cortacircuitos en el SDK.
- **Bot de pull requests** (GitHub App): cambio de modelo, `cache_control`, `max_iterations`. `step_site` dice dónde está el código.
- **Plano de control** (sección 1).
- **Reglas nuevas:**
  - lecturas de caché que se pagan (OpenAI);
  - caché semántica (prompts casi iguales);
  - salida truncada o descartada;
  - reintentos por JSON mal formado;
  - historial que crece sin límite;
  - trabajo que podría ir a la Batch API.
- **Caché compartida entre pasos:** hoy el contexto fijo se mira paso a paso, y la caché de un prefijo común a dos pasos distintos no se ve.
- **Fase 3 original:** diagnóstico con modelo, en el que cada afirmación cite spans concretos.
- **Seguridad y empresa:**
  - redacción de datos personales en el SDK (`init(redact=...)`) y muestreo que siempre guarde errores y trazas caras;
  - SSO/SAML, SCIM y verificación de correo.

## 5. Deuda y detalles sueltos
- `globals.css` tiene 3.265 líneas en un solo fichero: partirlo.
- Al cargar se piden dos veces `/api/projects` y `/api/auth/me`.
- `/health` en modo local dice `clickhouse: true, postgres: true`.
- `laplace demo` escribe caracteres rotos en la consola de Windows (la salida no va en UTF-8).
- `DECISIONS.md` pesa 150 KB: hace falta un índice por tema y documentación de cara al usuario aparte (Mintlify, Docusaurus o Starlight).
- En `STATUS.md`, «Qué queda» está desfasado: actualizarlo al cerrar cada fase.
- Las tiradas de evaluación se registran con la fecha de ahora aunque sus trazas sean de ayer (en la demo).

## 6. Lo que sólo puede hacer el usuario
- Reservar `laplace-trace` y `laplace-backend` en PyPI y registrar este repositorio como «trusted publisher» con el entorno `pypi`.
- Reservar un scope en npm, por ejemplo `@laplace-ai`.
- Decidir el dominio: `laplace.dev`, `laplace.ai` y `laplace.sh` están cogidos; `uselaplace.com` parecía libre, pero hay que confirmarlo.
- Poner una clave de proveedor para las 4 pruebas vivas que validan las cifras contra la factura real (`LAPLACE_LIVE_TESTS=1`).

## 7. Negocio (del informe de auditoría)
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
