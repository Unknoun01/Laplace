# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 28 de septiembre de 2026, al
cerrar las fases 5 y 6. Aquí sólo está **lo que queda**: lo hecho vive en `DECISIONS.md`
y en la historia de git. Léelo entero antes de tocar nada; después lee `STATUS.md` y las
últimas entradas de `DECISIONS.md` (D-154 a D-163).

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
  recuperable». Ya tiene su sitio en la interfaz (D-156); lo que le falta es automatizar
  el paso «probar» (el replay, sección 3).
- La observabilidad completa se mantiene para el análisis en profundidad.
- **Margen por cliente:** hecho (D-161 a D-163), salvo lo que espera al usuario.
- **En vez de un editor tipo n8n:** un **plano de control**. Laplace no ejecuta agentes.
  - El grafo del agente existe por traza (D-153); falta el de todo el proyecto.
  - Controles desde la interfaz que aplica el SDK: modelo por paso, tope de gasto, botón
    de parada y repartos A/B, siempre con copia de reserva si Laplace no responde.
  - Hay que confirmarlo con el usuario antes de empezar.

## 2. Cómo se trabaja en este repo

- **Por fases:** una rama por fase y un commit por bloque. En la sesión del 28 de
  septiembre el usuario pidió **fusionar a `master` y subir directamente** cada bloque en
  verde (`git merge --no-ff`); si no dice otra cosa, se sigue así.
- **Nada entra sin su prueba.** Primero la prueba en rojo, después el arreglo.
- **Hay que comprobar que la prueba muerde:** romper el código a propósito y verla fallar.
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-165**.
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
- **El color sale sólo de los tokens** de `globals.css`; `test_tema.py` mide el contraste
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
- **Pantallas:** `test_pantallas.py` usa Playwright con Chromium, ya instalados en `.venv`.
- **Estado:** la última pasada completa (D-162, en Linux con ClickHouse y Postgres) dio 902 pruebas bien y 16 saltadas —las 4 vivas que necesitan clave de proveedor y 12 que necesitan Ollama con un modelo— y una de pantalla que dependía del reloj, arreglada después. Las del espejo con la web necesitan Node 22.6 o posterior.
- **Fuera de Windows** (contenedor Linux): `.venv/bin/python` en lugar de `.venv/Scripts/python.exe`, y Playwright 1.56 para el Chromium que ya trae la máquina. Docker no arranca solo: `dockerd &` y después `docker start laplace-clickhouse laplace-postgres` (el contenedor de la sesión se reinicia y hay que repetirlo). ClickHouse no arranca con el `ulimits` del compose en un contenedor sin permiso para subirlos: `docker run` sin esa línea. Ojo con `pkill -f "laplace.cli ui"` en la misma orden que lo arranca: se mata a sí misma.
- **Prueba de carga:** `python scripts/carga.py --spans 10000000` genera un día de tráfico en ClickHouse y mide las pantallas con el desglose de cada lectura; `--borrar` lo quita. Con 10 millones hacen falta unos 8 GB para Docker.

## 3. Pendiente, por orden

### Espera al usuario (sección 5)
- **Stripe contra la API real:** la traída de ingresos (D-162) y la diaria (D-163) están
  probadas contra una Stripe falsa con la forma documentada de las facturas. Con una
  clave `rk_test_…`, probar una traída de verdad y ajustar lo que no case.
- **Paquete fino de TypeScript `@laplace/sdk`** (`init`, `observe`, `setContext({
  customerId })`, `getPrompt`): espera el nombre del scope en npm. Mientras tanto, un
  agente en Node usa OpenInference/OpenLLMetry y los atributos `laplace.*` (probado, D-163).
- **Las 4 pruebas vivas** que validan las cifras contra la factura del proveedor.

### Restos de fases cerradas
- **Integraciones sin probar:** Anthropic por OpenInference-js/OpenLLMetry-js, el AI SDK de Vercel, LangChain.js y `client.beta.*` en Python.
- **Escala:** medir con semanas de histórico (hace falta una máquina con más memoria) para decidir sobre acotar por tiempo las subconsultas de `_where` y sobre `FINAL` con partes sin fusionar. El Diagnóstico tarda unos 4 s la primera vez con 10 millones de spans al día en el portátil (objetivo 1,5 s); los preagregados por hora lo bajarían y están aparcados hasta que esa primera carga importe. Si se hacen, entran el uso por paso, el resumen, la cobertura y la serie del gráfico (`step_cost_series`, un 13 % del Diagnóstico, D-154).

### Siguiente: funciones diferenciales (confirmar el orden con el usuario)
- **Replay contrafactual:** reenviar, con tope de gasto y permiso, las llamadas reales de un paso al modelo barato y compararlas con el juez. Sólo llamadas hoja sin herramientas con efectos. Automatiza el paso «probar» del ciclo, que hoy es guardar el conjunto y lanzar la tirada a mano.
- **`laplace.guard(max_usd_per_run, max_loop)`:** cortacircuitos en el SDK.
- **Bot de pull requests** (GitHub App): cambio de modelo, `cache_control`, `max_iterations`. `step_site` dice dónde está el código.
- **Plano de control** (sección 1), empezando por el grafo del agente de todo el proyecto.
- **Reglas nuevas** (cada una entra por `detect()` y `detail()`, con su prueba de catálogo):
  - lecturas de caché que se pagan (OpenAI);
  - caché semántica (prompts casi iguales);
  - salida truncada o descartada;
  - reintentos por JSON mal formado;
  - historial que crece sin límite;
  - trabajo que podría ir a la Batch API.
- **Caché compartida entre pasos:** hoy el contexto fijo se mira paso a paso, y la caché de un prefijo común a dos pasos distintos no se ve.
- **Margen por cliente, más allá:** cuánto de lo evitable de cada problema es de cada
  cliente (hoy se dice qué problemas pasan en sus ejecuciones, no cuánto dinero suyo
  tiran); ingresos en otra moneda con el tipo de cambio que ponga el usuario, como el de
  euros (hoy no se convierten); y la columna de cliente en la lista de trazas y en su CSV.
- **Diagnóstico con modelo:** cada afirmación cita spans concretos (el hueco está reservado en el contrato, el esquema y la API).
- **Seguridad y empresa:**
  - redacción de datos personales en el SDK (`init(redact=...)`) y muestreo que siempre guarde errores y trazas caras;
  - SSO/SAML, SCIM y verificación de correo.

## 4. Deuda y detalles sueltos
- **Guardia estructural para reglas nuevas.** `test_catalogo_hallazgos` exige ficha y
  trazas a cada tipo, pero nada exige que una regla nueva desambigüe su título (D-115) ni
  que entre en el descuento del doble conteo (D-117). La de Prompts (D-157) se hizo
  mirándolo a mano.
- **Retención por proyecto** (D-009): hoy `LAPLACE_RETENTION_DAYS` vale para toda la
  instalación. Y no hay forma de borrar los datos de un usuario final (`user_id`) o de un
  cliente (`customer_id`) concreto, que es lo que pediría un cliente de un cliente.
- **La API de la lista de trazas escanea sin ventana** si no se le pasa una (D-008b). La
  interfaz siempre la pasa; un cliente de la API, no tiene por qué.
- **Lo que la auditoría del rediseño no revisó** (`docs/auditoria-rediseno.md`): las
  pantallas con sesión de la versión Docker (organización, invitaciones, claves), los
  flujos de escritura de punta a punta y los navegadores que no son Chromium (Safari
  trata distinto `backdrop-filter` y `color-mix`, y todo el color sale de `color-mix`;
  el claro Rose Gold depende del cristal y de `saturate()`).
- **El contraste se mide sobre los tokens, no sobre cada pantalla** (D-155): los
  resplandores de detrás del cristal y los degradados de los botones no entran en la
  cuenta.
- **En español a propósito, y por decidir si sigue así** (D-148): la línea de órdenes
  (`laplace ui`, `laplace demo`) y los logs.
- **La barra con siete pestañas** ocupa tres filas en móvil (3 + 3 + 1). Cabe y se prueba,
  pero pide otra forma (agrupar Prompts y Ajustes, o un menú) si se añade alguna más.
- `globals.css` pasa de 4.100 líneas en un solo fichero: partirlo.
- Al cargar se piden dos veces `/api/projects` y `/api/auth/me` (y el Diagnóstico pide
  además el margen por cliente para su aviso).
- `/health` en modo local dice `clickhouse: true, postgres: true`.
- `laplace demo` escribe caracteres rotos en la consola de Windows (la salida no va en UTF-8).
- `DECISIONS.md` pasa de 220 KB: hace falta un índice por tema y documentación de cara al usuario aparte (Mintlify, Docusaurus o Starlight).
- `STATUS.md` se puso al día al cerrar las fases 5 y 6: mantenerlo así al cerrar cada fase.
- `test_pantallas.py`: un fallo antiguo en la pantalla de Prompts (no cargó en 15 s en una pasada completa) no se ha vuelto a ver ni se ha explicado.
- Tras la caída de Docker del 26 de septiembre quedaron apartadas `%LOCALAPPDATA%\Docker\run.viejo-*` y `docker-secrets-engine.viejo-*` con sockets bloqueados; se pueden borrar tras reiniciar Windows.
- Las tiradas de evaluación se registran con la fecha de ahora aunque sus trazas sean de ayer (en la demo).
- El pie del gráfico de gasto por día (D-152) usa `dayHour` y enseña la hora aunque el tramo sea un día.
- En el grafo del agente (D-153), un coste largo («0,003072 US$») se sale del borde de su caja.
- En la demo, el problema de la versión del prompt sale entre un 53 % y un 70 % más caro
  según la hora a la que se carga: es real (depende del reparto de tráfico por horas),
  pero una demo que cambia de cifra entre dos cargas desconcierta al enseñarla.

## 5. Lo que sólo puede hacer el usuario
- Reservar `laplace-trace` y `laplace-backend` en PyPI y registrar este repositorio como «trusted publisher» con el entorno `pypi`.
- Reservar un scope en npm, por ejemplo `@laplace-ai`, y decir cuál es: el paquete de TypeScript espera el nombre.
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
