# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 28 de septiembre de 2026.
Aquí sólo está **lo que queda**: lo hecho vive en `DECISIONS.md` y en la historia de git.
Léelo entero antes de tocar nada; después lee `STATUS.md` y las últimas entradas de
`DECISIONS.md` (D-147 a D-157).

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
- **Idiomas:** español, inglés, portugués de Brasil, francés y chino simplificado, con i18n real (hecho, D-147 y D-148).
- **Primera función nueva, después de las fases técnicas:** margen por cliente final (hecha, D-161 y D-162).
- **En vez de un editor tipo n8n:** un **plano de control**.
  - Grafo del agente sacado de las trazas (ya existe por traza, D-153; falta el de todo el proyecto).
  - El ciclo ya tiene su sitio en la interfaz (D-156): Probar es el paso 2, la ficha dice
    en qué paso está cada problema y el héroe suma lo ya ahorrado.
  - Controles desde la interfaz que aplica el SDK: modelo por paso, tope de gasto, botón de parada y repartos A/B.
  - Siempre con copia de reserva si Laplace no responde.

  Laplace no ejecuta agentes; queda para después del margen por cliente, y hay que confirmarlo con el usuario antes de empezar.

## 2. Cómo se trabaja en este repo

- **Por fases:** una rama por fase y un commit por bloque. Se enseña al usuario y se fusiona a `master` sólo cuando lo aprueba (`git merge --no-ff`).
- **Nada entra sin su prueba.** Primero la prueba en rojo, después el arreglo.
- **Hay que comprobar que la prueba muerde:** romper el código a propósito y verla fallar.
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-164**.
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
- **Estado:** la última pasada completa (D-157, en Linux con ClickHouse y Postgres) dio 853 pruebas bien y 16 saltadas: las 4 vivas que necesitan clave de proveedor y 12 que necesitan Ollama con un modelo. Las del espejo con la web necesitan Node 22.6 o posterior.
- **Fuera de Windows** (contenedor Linux): `.venv/bin/python` en lugar de `.venv/Scripts/python.exe`, y Playwright 1.56 para el Chromium que ya trae la máquina. ClickHouse no arranca con el `ulimits` del compose en un contenedor sin permiso para subirlos: `docker run` sin esa línea.
- **Prueba de carga:** `python scripts/carga.py --spans 10000000` genera un día de tráfico en ClickHouse y mide las pantallas; `--borrar` lo quita. Con 10 millones hacen falta unos 8 GB para Docker.

## 3. Pendiente, por orden

### Fase 5: cerrada y fusionada en `master`
La medida con la prueba de carga (D-154), el oscuro por defecto (D-155), el rediseño
alrededor del ciclo (D-156), Prompts como fuente de hallazgos (D-157), el claro Rose Gold
con cristal líquido (D-158), el alto contraste en dark luxury y en claro (D-159) y las tres
decisiones que estaban abiertas (D-160).

### Restos de fases cerradas
- **Fase 3:** sin probar todavía Anthropic por OpenInference-js/OpenLLMetry-js, el AI SDK de Vercel, LangChain.js y `client.beta.*` en Python. Paquete fino `@laplace/sdk` (`init`, `observe`, `getPrompt`) cuando el usuario reserve el scope de npm.
- **Fase 4:** medir con semanas de histórico (hace falta una máquina con más memoria) para decidir sobre acotar por tiempo las subconsultas de `_where` y sobre `FINAL` con partes sin fusionar. El Diagnóstico tarda unos 4 s la primera vez con 10 millones de spans al día en el portátil (objetivo 1,5 s); los preagregados lo bajarían a unos 2,5 s y están aparcados hasta que esa primera carga importe. Si se hacen, la serie del gráfico (`step_cost_series`, un 13 % del Diagnóstico, D-154) entra en la lista: tiene exactamente la forma de un preagregado por hora.

### Fase 6: cerrada (D-161, D-162)
`customer_id` de punta a punta, ingresos a mano o desde Stripe, margen al mes con el
aviso de quién hace perder dinero (en la pestaña, en el Diagnóstico y como alerta), y de
cada cliente a sus ejecuciones y a los problemas que pasan en ellas. Queda:
- **Probar Stripe contra la API real** con una clave de pruebas (`rk_test_…`): está hecho
  contra una Stripe falsa con la forma documentada.
- El paquete fino de TypeScript (`@laplace/sdk`) tendrá que llevar `customerId` en su
  `setContext`. Hasta entonces, un agente en Node pone el atributo `laplace.customer.id`
  (probado, D-163; está en `docs/typescript.md`).

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

## 4. Deuda y detalles sueltos

### Del repaso de la historia (28 de septiembre)
Lo que `DECISIONS.md`, `STATUS.md`, `ANALISIS.md` y `docs/` dejaron dicho como pendiente y
no estaba en esta hoja. Lo que se resolvió después (el streaming de D-016, la marca de
tarifa sin verificar en lo agregado de D-141, el wheel con la interfaz dentro, el
volumen medido) no está.
- **Guardia estructural para reglas nuevas.** `test_catalogo_hallazgos` exige ficha y
  trazas a cada tipo, pero nada exige que una regla nueva desambigüe su título (D-115) ni
  que entre en el descuento del doble conteo (D-117). La de Prompts (D-157) se hizo
  mirándolo a mano, que es justo lo que `STATUS.md` pide no tener que hacer.
- **Retención por proyecto** (D-009): hoy `LAPLACE_RETENTION_DAYS` vale para toda la
  instalación. Y no hay forma de borrar los datos de un usuario final concreto (`user_id`),
  que es lo que pediría un cliente de un cliente.
- **La API de la lista de trazas escanea sin ventana** si no se le pasa una (D-008b). La
  interfaz siempre la pasa; un cliente de la API, no tiene por qué.
- **Lo que la auditoría del rediseño no revisó** (`docs/auditoria-rediseno.md`): las
  pantallas con sesión de la versión Docker (organización, invitaciones, claves), los
  flujos de escritura de punta a punta y los navegadores que no son Chromium (Safari
  trata distinto `backdrop-filter` y `color-mix`, y todo el color sale de `color-mix`).
- **El contraste se mide sobre los tokens, no sobre cada pantalla** (D-155): los
  resplandores de detrás del cristal y los degradados de los botones no entran en la
  cuenta.
- **En español a propósito, y por decidir si sigue así** (D-148): la línea de órdenes
  (`laplace ui`, `laplace demo`) y los logs.
- **Las alertas incluyen la regla nueva** `prompt_caro` desde D-157: una instalación con
  alertas encendidas puede recibir avisos de un tipo que no conocía. Se silencia por
  proyecto en Ajustes.

### De antes
- `globals.css` tiene 3.631 líneas en un solo fichero: partirlo.
- Al cargar se piden dos veces `/api/projects` y `/api/auth/me`.
- `/health` en modo local dice `clickhouse: true, postgres: true`.
- `laplace demo` escribe caracteres rotos en la consola de Windows (la salida no va en UTF-8).
- `DECISIONS.md` pesa 150 KB: hace falta un índice por tema y documentación de cara al usuario aparte (Mintlify, Docusaurus o Starlight).
- `STATUS.md` se ha puesto al día al cerrar la Fase 5 («Qué queda» y «No aguanta todavía»): mantenerlo así al cerrar cada fase.
- `test_pantallas.py`: un fallo antiguo en la pantalla de Prompts (no cargó en 15 s en una pasada completa) no se ha vuelto a ver ni se ha explicado. El otro que sólo salía fuera de Windows («Tableau de bord» a 375 px) está arreglado (D-156).
- Tras la caída de Docker del 26 de septiembre quedaron apartadas `%LOCALAPPDATA%\Docker\run.viejo-*` y `docker-secrets-engine.viejo-*` con sockets bloqueados; se pueden borrar tras reiniciar Windows.
- Las tiradas de evaluación se registran con la fecha de ahora aunque sus trazas sean de ayer (en la demo).
- El pie del gráfico de gasto por día (D-152) usa `dayHour` y enseña la hora aunque el tramo sea un día.
- En el grafo del agente (D-153), un coste largo («0,003072 US$») se sale del borde de su caja.

## 5. Lo que sólo puede hacer el usuario
- Reservar `laplace-trace` y `laplace-backend` en PyPI y registrar este repositorio como «trusted publisher» con el entorno `pypi`.
- Reservar un scope en npm, por ejemplo `@laplace-ai`.
- Decidir el dominio: `laplace.dev`, `laplace.ai` y `laplace.sh` están cogidos; `uselaplace.com` parecía libre, pero hay que confirmarlo.
- Poner una clave de proveedor para las 4 pruebas vivas que validan las cifras contra la factura real (`LAPLACE_LIVE_TESTS=1`).
- Una clave de pruebas de Stripe (`rk_test_…`) para probar la traída de ingresos contra la API real.

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
