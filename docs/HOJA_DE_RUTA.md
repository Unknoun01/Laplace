# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 26 de septiembre de 2026.
Léelo entero antes de tocar nada; después lee `STATUS.md` y las últimas entradas de
`DECISIONS.md` (D-134 a D-143).

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
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-147**.
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
- **Estado al cerrar esta sesión:** 723 pruebas pasan, 0 fallan y 4 se saltan (las que necesitan clave de proveedor).
- **Prueba de carga:** `python scripts/carga.py --spans 10000000` genera un día de tráfico en ClickHouse y mide las pantallas; `--borrar` lo quita. Con 10 millones hacen falta unos 8 GB para Docker.

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

**Fase 3, en la rama `fase3-integraciones`:**
- **D-136, fusionado:** `ingest/convenciones.py` traduce OpenInference y OpenLLMetry.
- **D-137:** Responses API de OpenAI (`create` y `parse`, sync, async y streaming), `chat.completions.parse`, `messages.stream()` y `messages.parse` de Anthropic. El rol `developer` cuenta como instrucciones. `with_raw_response` se lee (antes costaba cero medido) y una respuesta sin uso se estima. `test_proveedores_otras_rutas.py`.
- **D-138:** la tabla de LiteLLM como capa no verificada (`pricing/litellm_prices.json`, ~3.200 modelos), con `Cost.rate_unverified` aparte de la tarifa asumida, el nombre de gateway cobrado con su precio, `scripts/precios_litellm.py` y el trabajo semanal `precios-litellm.yml`. `test_precios_litellm.py`.
- **D-139:** guía de TypeScript probada (`docs/typescript.md`); OTLP/JSON con ids en hexadecimal; `gen_ai.provider.name` y mensajes en `parts`. `test_otlp_json.py`.
- **D-140:** la prueba inestable era `test_el_notificador_no_sigue_redirecciones` (servidor falso que no leía el cuerpo).
- **Contraste con LangGraph y con Node:** en `STATUS.md`, «Con un framework de verdad».

## 4. Pendiente, por orden

### Fase 3: cerrada y fusionada (D-141)
- Sin probar todavía: Anthropic por OpenInference-js/OpenLLMetry-js, el AI SDK de Vercel, LangChain.js y `client.beta.*` en Python.
- Paquete fino `@laplace/sdk` (`init`, `observe`, `getPrompt`) cuando el usuario reserve el scope de npm.

### Fase 4: escala, cerrada (D-142 a D-146)
**Hecho y medido:** repeticiones y bucles en dos fases (antes tumbaban la máquina), el Diagnóstico sin lecturas repetidas y con caché de un minuto en la nube, ingesta fuera del bucle de eventos con `async_insert` y el proyecto registrado una vez, payloads con `ZSTD(3)`.

**Resultado con 10 millones de spans/día:** Diagnóstico unos 4 s en la primera carga (objetivo 1,5 s, **sin cumplir**); Panel, lista y abrir traza por debajo de 1,5 s salvo la búsqueda (1,6 s).

**Lo que queda, por orden:**
1. **Hecho (D-143):** el Diagnóstico que alguien mira se recalcula en segundo plano antes de caducar; abrirlo es instantáneo salvo la primera vez (unos 4 s con 10 millones al día). **Preagregados, aparcados:** sólo bajarían esa primera vez a unos 2,5 s (repeticiones y bucles no se preagregan) y piden mucho cuidado; retomarlos si la primera carga llega a importar.
2. **Hecho (D-144):** búsqueda en el contenido de prompts, respuestas y herramientas. En ClickHouse con `ngrambf_v1(4)`: un id tarda 0,19 s (antes 1,17 s); un texto que sale en miles de trazas, 1,7 s. En SQLite se recorre, sin FTS5 (medido: pesa la agregación, no el texto).
3. **Hecho (D-145):** duración mediana y p95 y ejecuciones con error en el Panel, en lugar de la duración media.
4. **Pendiente, sin máquina para ello:** medir con semanas de histórico (hace falta una máquina con más memoria) para decidir sobre acotar por tiempo las subconsultas de `_where` y sobre `FINAL` con partes sin fusionar. Descartado con números: la clave de ordenación nueva y la tabla `trace_id → proyecto` (abrir una traza ya tarda 0,03 s) y lanzar las lecturas en paralelo.

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
- `test_pantallas.py`: las pantallas del Diagnóstico fallaban según la hora, porque la demo terminaba ayer a medianoche (arreglado, D-146). Un fallo anterior, en la pantalla de Prompts (no cargó en 15 s en una pasada completa), no se explica por eso y no se ha repetido.
- Tras la caída de Docker del 26 de septiembre quedaron apartadas `%LOCALAPPDATA%\Docker\run.viejo-*` y `docker-secrets-engine.viejo-*` con sockets bloqueados; se pueden borrar tras reiniciar Windows.
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
