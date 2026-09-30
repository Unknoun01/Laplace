# Hoja de ruta de Laplace

Documento de traspaso entre sesiones. Última actualización: 28 de septiembre de 2026,
tras probar las integraciones que faltaban (D-164 y D-165) y con el replay contrafactual
(D-167). Aquí sólo está **lo que
queda**: lo hecho vive en `DECISIONS.md` y en la historia de git. Léelo entero antes de tocar nada; después lee `STATUS.md` y las
últimas entradas de `DECISIONS.md` (D-154 a D-168).

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
- **Cada cambio con criterio lleva su entrada `D-xxx` en `DECISIONS.md`.** La siguiente libre es **D-175**.
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
- **Escala (en curso; decidido con el usuario: las dos cosas, en este orden).** La tabla ya se ordena por día y los filtros se acotan a la ventana (D-168): con 14 días guardados, el Diagnóstico de un día tarda 5–6,5 s en el contenedor de 4 núcleos y el de 7 días casi un minuto. El objetivo es 1,5 s.
  1. **Primero, repeticiones y bucles** (44 % del Diagnóstico). Los preagregados no los tocan, porque miran dentro de cada traza. Recorren la ventana dos veces: una para buscar candidatas, calculando un hash de dos cadenas en cada fila, y otra con `FINAL`. Además, `RULES_WHERE`, que usan casi todas las lecturas, lleva una subconsulta que vuelve a leer la columna `tags` de toda la ventana para excluir las tiradas de evaluación. Antes de cambiar nada se mide cada consulta por separado (filas, bytes y tiempo); las ideas de partida son una columna materializada con ese hash y un índice sobre `tags`.
  2. **Después, los preagregados por hora** para el resto (uso por paso, resumen, cobertura, serie del gráfico y prompts). **La trampa:** recalcular costes al cambiar una tarifa (`recalcular_coste`) y un lote reenviado vuelven a insertar spans, así que una tabla que sume al insertar contaría dos veces. Hay que recalcular cada hora desde `spans FINAL` y guardar el resultado reemplazando el anterior. Los agregados que no se suman (trazas distintas, trazas de ejemplo, medianas) se guardan como estados de ClickHouse. Hay que controlar qué horas están sucias y leer en crudo las horas del borde de la ventana. La prueba de fuego es que el Diagnóstico con preagregados salga idéntico al calculado en crudo.
  - La estimación con los números de D-168 es que el día baja a unos 3 s con los preagregados solos; hace falta lo primero para acercarse a 1,5 s. Los datos de carga (14 días, 140 millones de spans) se están regenerando para medir.
  - Las instalaciones que ya existen se migran a mano con `python -m laplace_backend.storage.migrar_orden`.

### Siguiente: funciones diferenciales (confirmar el orden con el usuario)
- **Replay contrafactual, más allá** (lo básico está hecho, D-167): llamadas con herramientas (habría que simular sus resultados con los grabados, sin ejecutarlas), imágenes y bloques entre proveedores, y un botón en la versión local que lo lance sin copiar la orden.
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
- La documentación de usuario (`docs/usuario/`, D-174) es Markdown plano: pasarla a un sitio (Mintlify, Docusaurus o Starlight) cuando haya dominio.
- `STATUS.md` se puso al día al cerrar las fases 5 y 6: mantenerlo así al cerrar cada fase.
- Tras la caída de Docker del 26 de septiembre quedaron apartadas `%LOCALAPPDATA%\Docker\run.viejo-*` y `docker-secrets-engine.viejo-*` con sockets bloqueados; se pueden borrar tras reiniciar Windows.

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
