# Laplace

Observabilidad y optimización de agentes de IA.

Cuando un agente falla, tarda o dispara la factura de tokens, los logs normales no
sirven: no capturan el árbol de decisiones. Laplace lo hace visible. Instalas una
librería con una línea y cada ejecución queda registrada como una traza jerárquica: qué
paso se dio, qué prompt se envió, qué respondió el modelo, qué herramienta se llamó,
cuántos tokens y cuánto costó cada nodo.

La función principal es el **ahorro verificado**, un ciclo en cuatro pasos: **detectar**
el derroche, **probar** el arreglo sobre llamadas reales, **arreglar** y **verificar** el
ahorro después. La cifra que lo resume es «ahorrado y recuperable». La observabilidad
completa sigue ahí para el análisis en profundidad. La interfaz y el motor hablan
español, inglés, portugués de Brasil, francés y chino simplificado.

## Estado

| Fase | Qué es | Estado |
|------|--------|--------|
| 0 | Monorepo, `docker-compose`, contrato de traza, esquemas | ✅ |
| 1.4 | SDK de Python (`@observe` + auto-instrumentación) | ✅ |
| 1.5 | Ingesta OTLP + API de lectura | ✅ |
| 1.6 | Lista de trazas + vista de árbol | ✅ |
| 1.7 | Modo local `laplace ui` con SQLite | ✅ |
| 2 | Detección de derroche y **panel de ahorro** | ✅ |
| 2.b | Alertas a Slack cuando se supera un umbral | ✅ |
| 4.a | **Panel** por unidad de trabajo, picos atribuidos, modo en vivo | ✅ |
| 5 | **Evaluaciones**: anotación, conjuntos de casos y A vs B | ✅ |
| 6 | **Prompts**: versiones, diff, rollback y métricas por versión | ✅ |
| 3 | **Diagnóstico automático** con modelo | pendiente |

Las fases 2 y 3 son el producto, no extras: son lo que separa a Laplace de un visor de
trazas.

## Las dos mitades de la interfaz

El mismo producto sirve a dos públicos con un interruptor. En modo **Sencillo** se habla en
cristiano y manda el dinero; en **Avanzado** aparece la capa técnica: la consulta que
disparó cada alerta, los atributos de cada span, el árbol completo y la exportación en
JSON. El modo es global y se recuerda. El tema oscuro va por defecto; en Ajustes se puede
elegir el claro, que es neutro, o seguir al sistema.

- **Diagnóstico** (`/`) — cuánto te cuesta el agente, cuánto puedes dejar de pagar, cuánto
  has dejado de pagar ya con lo arreglado, y las cosas que arreglar ordenadas por dinero
  recuperable, con el gasto por día y por paso. Encabezado, cuando hace falta, por
  cuánto de tu agente entendemos: una cifra de ahorro sin eso no se puede leer.
- **Probar** (`/evaluaciones`) — el segundo paso del ciclo: los arreglos que propone el
  Diagnóstico y si ya tienen su prueba, y si la versión nueva acierta igual y cuesta
  menos.
- **Panel** (`/panel`) — si el gasto sube porque hay más trabajo o porque el trabajo se
  ha encarecido, dicho con palabras, y los tramos que se salen de lo normal con su causa.
- **Prompts** (`/prompts`) — qué versión está en producción, qué cambió entre una y otra,
  y qué costó y qué acertó cada una sobre el tráfico que la usó. Una versión que encarece
  el agente sale además en el Diagnóstico.
- **Problema** (`/problema?id=…`) — qué pasa, por qué, cómo se ha detectado, cómo se
  arregla y cuánto te ahorras, con el cálculo detrás, y en qué paso del ciclo está.
- **Trazas** (`/trazas`) — exploración libre: filtros, búsqueda y orden por coste,
  exportar a CSV y guardar lo filtrado como conjunto de casos.
- **Traza** (`/traza?id=…`) — el grafo del agente y el árbol navegable, con coste por rama,
  repeticiones marcadas y los problemas detectados que pasan en esa ejecución.
- **Ajustes** (`/ajustes`) — presupuesto mensual, alertas (Slack, webhook y correo),
  tarifas propias, equivalente en euros y borrado del proyecto.

## Qué detecta hoy

Cinco reglas deterministas, sin modelo de por medio
([`insights/`](apps/backend/laplace_backend/insights/), una por módulo):

| Regla | Qué busca | Cómo calcula el ahorro |
|-------|-----------|------------------------|
| Repetición | El mismo paso, con la misma entrada, 3+ veces en una ejecución | Coste íntegro de las copias sobrantes |
| Bucle | El mismo paso llamado una y otra vez sin que su resultado avance, aunque la entrada cambie (un contador de intentos) | Coste de las vueltas que no aportaron nada |
| Modelo caro | Un paso con salida media corta que usa un modelo con alternativa más barata | Diferencia de tarifa sobre los tokens reales |
| Contexto fijo | Un prompt con un suelo grande de tokens que se reenvía sin caché | Diferencia entre tarifa normal y de caché, menos lo que cuesta escribirla |
| Prompt más caro | La versión de un prompt gestionado que corre ahora cuesta por ejecución más que la anterior | Diferencia por ejecución sobre el tráfico de la versión nueva, escalada por lo que ya reclaman las otras reglas |

Cinco cosas que **no** hace, a propósito: no inventa dinero donde no lo hay (un bucle de
herramientas no gasta tokens, así que enseña el tiempo perdido y lo dice), no cuenta dos
veces el mismo ahorro cuando dos reglas se solapan, no afirma que un modelo más barato
acertará igual (eso lo dicen las evaluaciones, y la ficha lleva a ellas), no presenta como
completo un total al que le faltan pasos cuyo modelo no tiene tarifa conocida, y **no
proyecta un mes desde una hora de datos**: por debajo de un día observado enseña lo
gastado de verdad con su ventana, y dice cuánto falta para la previsión (D-073).

## Avisar cuando algo se dispara

Alertas a Slack cuando un hallazgo supera su umbral, **apagadas por defecto** y con las
mismas variables en la nube y en `laplace ui`:

```bash
export LAPLACE_ALERTS_ENABLED=true
export LAPLACE_ALERTS_SLACK_WEBHOOK="https://hooks.slack.com/services/…"
export LAPLACE_ALERTS_BASE_URL="http://localhost:3000"   # para el enlace a la ficha
```

El mensaje lleva el hallazgo, el dinero y un enlace directo a su ficha. Lo que más
trabajo cuesta aquí es **no ser ruidoso**, porque una alerta que se repite se ignora y
después se ignoran todas:

- **Un mensaje por proyecto**, con todo lo que vence junto. Nunca uno por hallazgo.
- **Periodo de calma por hallazgo** (24 h por defecto): tras avisar no se vuelve a avisar
  de lo mismo, aunque el problema siga ahí y aunque haya empeorado.
- **Umbral, silencio y reglas silenciadas por proyecto**, en un JSON que se versiona
  (`LAPLACE_ALERTS_CONFIG_PATH`):

  ```json
  {
    "defaults": {"min_usd": 1.0, "quiet_hours": 24},
    "projects": {
      "cobros":  {"min_usd": 0.5, "quiet_hours": 6},
      "juguete": {"muted": true},
      "soporte": {"muted_kinds": ["repeticion"]}
    }
  }
  ```

- El umbral se mide en **dinero ya gastado**, no proyectado: así un proyecto recién
  instalado también puede avisar.
- Si el coste de un hallazgo no es fiable —modelo sin tarifa, o metro de facturación sin
  confirmar— la alerta dice «al menos 5,00 $» y explica por qué. Nunca afirma una cifra
  que el motor de precios sabe incompleta.

`GET /api/alerts` enseña la configuración en vigor y qué saltaría ahora mismo, sin mandar
nada.

## Después de ver un problema: arreglarlo y comprobarlo

Cada ficha termina con dos botones: **«Lo he arreglado: compruébalo»** y **«No es un
problema para mí»**. Marcar guarda el momento, y a partir de ahí se compara lo que costaba
cada ejecución antes con lo que cuesta después:

> En las 42 ejecuciones desde que lo marcaste no ha vuelto a aparecer. Antes se iban
> $0,2016 por ejecución: a ese ritmo, llevas $8,47 sin gastar.

Por ejecución y no en totales, porque con menos tráfico después el total baja solo. Con
menos de cinco ejecuciones después no se dice nada. Y si sigue saliendo igual, **vuelve a
la lista** como reaparecido aunque esté marcado: fiarse de lo que dice el usuario sin
mirar sería esconder justo el caso que importa. Lo arreglado y lo ignorado no cuentan en
el evitable del inicio ni mandan alertas.

Antes de eso, la ficha lleva a **Probar**: guarda las ejecuciones reales de ese paso como
conjunto de casos y da la línea para lanzarlas con el arreglo puesto —el modelo barato,
la versión anterior del prompt—, porque el ahorro está medido y la calidad no. La ficha
dice en qué paso del ciclo está cada problema, y el Diagnóstico suma lo ya ahorrado.

## Presupuesto, alertas y quién gasta

**Presupuesto mensual** sobre el mes natural, en Ajustes. El inicio dice cuánto llevas y,
con un día de datos del mes, a cuánto cerrarás; avisa al 80 % y al 100 %, una vez cada uno.

**Alertas desde la interfaz**, por proyecto: Slack, un webhook cualquiera (Teams, Discord,
n8n…) y correo. Las URL de webhook son secretos y la API no las devuelve nunca enteras; el
servidor de correo va en el entorno (`LAPLACE_SMTP_*`). Hay un botón de mensaje de prueba.

**Quién gasta más**, en el Panel: el gasto por usuario y por sesión, con lo que cuesta cada
ejecución y enlace a sus trazas. Sale de `laplace.set_context(user_id=…, session_id=…)`,
y lo que no dice de quién es se cuenta aparte.

## Vigilar sin convertirse en un Grafana peor

La pestaña **Panel** existe por dos ideas, y sin ellas no valdría la pena:

**Coste por unidad de trabajo, no totales.** Si los tokens suben un 40 % pero hay un 40 %
más de ejecuciones, no pasa nada; si suben con las mismas ejecuciones, hay degradación.
Las métricas protagonistas son todas por ejecución —coste, tokens, pasos, duración— y los
totales van debajo, como contexto. Y la lectura se dice en una frase, no se deduce de dos
líneas:

> **Subida sin más ejecuciones: revisar.**
> El gasto sube un 232 %, las ejecuciones se mantienen y el coste de cada una sube un
> 232 %. El mismo trabajo está costando más que antes, así que esto no lo explica la
> demanda.

**Atribución de picos.** Un tramo cuyo coste *por ejecución* se dispara —no su gasto: una
hora punta no es una anomalía— se investiga y se dice qué cambió alrededor: un modelo que
aparece por primera vez en las trazas, una herramienta nueva, un paso que se lleva el
sobrecoste. Con enlace directo a las trazas de ese tramo. **Si no se puede atribuir con
datos reales, dice «No identificamos la causa»**; nunca insinúa una correlación.

El explorador tiene además **modo en vivo**: las trazas nuevas se van añadiendo según
llegan, con indicador y botón de pausa. Es polling cada cinco segundos contra la misma
API de lectura, no WebSockets (D-079).

## Saber si responde bien, antes de desplegar

Diagnóstico responde a «¿cuesta lo que debe?». La pestaña **Probar** (antes Evaluaciones) responde a
«¿responde bien?», y sobre todo a la pregunta que se hace antes de un despliegue:

> **B acierta igual —hasta donde se puede saber— y cuesta un 90 % menos por caso.**
> A acierta 87 % (26 de 30) y B, 90 % (27 de 30). Con estos casos, A está entre 70 % y
> 95 %, y B entre 74 % y 97 %. Los dos márgenes se solapan, así que esa diferencia cabe
> dentro del azar de la muestra. Para separarlas hacen falta más casos anotados, no otra
> lectura de éstos.

Tres piezas:

**Anotar.** Marcas una ejecución como buena o mala, con comentario, desde la traza
abierta o desde una fila del explorador. Opcionalmente un **LLM-as-judge** hace lo mismo
automáticamente, respondiendo sólo a dos preguntas: ¿hizo lo que se le pedía?, ¿se
inventó algo? El veredicto de las personas y el del modelo **nunca se mezclan**: se
guardan aparte, se calculan aparte y se pintan en bloques distintos, y si se contradicen
la pantalla lo dice. Y lo que cuesta correr el juez se mide con la misma tabla de precios
que tu gasto y se enseña separado: sería un chiste saber menos de nuestra factura que de
la tuya.

**Conjuntos de casos.** Colecciones de trazas **reales**, creadas desde un filtro del
explorador. No hay casos inventados: cada uno guarda de qué ejecución salió, y el
conjunto guarda el filtro con el que se formó.

**A vs B.** Dos tiradas del mismo conjunto, con acierto y coste a la vez. La tirada la
lanza el SDK **en tu proceso** —Laplace no ejecuta tu agente (D-086)—:

```python
import laplace
laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")
laplace.run_dataset("regresiones", mi_agente, variant="prompt-v3")
```

Hay un ejemplo completo en
[`examples/evaluar_dos_versiones.py`](examples/evaluar_dos_versiones.py).

Dos reglas que la pantalla no se salta. **Ningún porcentaje sin su guarda**: con menos de
diez casos anotados se enseñan los casos en bruto («3 de 4») y se dice que no bastan, en
vez de un «75 %» que no informa de nada. Y **si los márgenes se solapan no hay ganador**:
la respuesta es «no se distinguen con estos casos», porque declarar una mejora sobre una
diferencia que cabe dentro del azar es exactamente cómo se despliega una regresión. El
coste, en cambio, va sin margen: no es una muestra, es la factura.

## Los prompts, con lo que cuestan y lo que aciertan al lado

Un gestor de prompts con diff y rollback lo tiene cualquiera. Lo que no tiene nadie más
es poder leer esto y decidir con ello:

> **v8** · $0.0039 por ejecución · 94 %
> **v7** · $0.0071 por ejecución · 93 %
>
> v8 acierta igual —hasta donde se puede saber— y cuesta un 45 % menos por ejecución.

Las dos cifras salen del tráfico real que usó cada versión, no de una estimación: el SDK
sirve el prompt y **deja escrito en cada traza con qué versión se ejecutó**.

```python
import laplace
laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")

sistema = laplace.get_prompt("atencion", fallback=SISTEMA_DEL_CODIGO)
respuesta = cliente.messages.create(
    model="claude-haiku-4-5",
    system=sistema.render(empresa="Vuelos Laplace"),
    messages=[{"role": "user", "content": pregunta}],
)
```

Hay un ejemplo completo en
[`examples/prompt_gestionado.py`](examples/prompt_gestionado.py).

**Laplace sigue sin ejecutar nada tuyo**: sólo sirve texto. Y como pedirlo pone a Laplace
en el camino caliente de tu agente, el SDK está escrito para que un Laplace caído no sea
tu problema: cachea un minuto, sirve la copia guardada aunque esté vencida, y si no la
hay usa el `fallback=` que le pases. Ese tráfico de reserva se cuenta **aparte**, no como
la versión de producción: sumarlo falsearía justo la cifra que estás mirando.

Las mismas guardas que en Evaluaciones, porque son literalmente las mismas funciones: con
menos de diez ejecuciones anotadas no hay porcentaje, y si dos versiones tienen los
márgenes solapados no hay ganador.

**Si no mueves tus prompts aquí, la pestaña no se queda en blanco.** La identidad de un
paso ya incluye la huella de sus instrucciones, así que Laplace puede decirte qué pasos
han cambiado de prompt, cuándo y qué costó cada juego de instrucciones, sin que hagas
nada. Es menos —no hay texto completo, ni diff, ni rollback— y la pantalla lo dice.

Un cambio de versión también es **una causa de pico** en el panel, pero sólo cuando lo
dicen las trazas: una versión que aparece en el tramo caro y no aparecía antes. Nunca «el
despliegue fue a las 14:02 y el pico empezó a las 14:00», que es una coincidencia y no
una causa.

## Antes de creerte una cifra: cuánto entendemos

El peor fallo que puede tener un producto así es **indistinguible del éxito**. Si Laplace
no reconoce los pasos de tu agente, las reglas no encuentran nada, la pantalla dice «no
estás tirando dinero ahora mismo» y eso parece una buena noticia. No lo es: significa que
no te entendemos.

Por eso el inicio mide cuatro cosas y las dice **antes** que el dinero cuando alguna va
mal: qué parte de tus llamadas tiene **paso que se distingue de los demás**, **tarifa
conocida**, **tokens del proveedor** (no estimados por nosotros) y **versión de prompt**.
Con todo en verde se queda en una línea; con algo en ámbar, se pone delante:

> **Hay 1 paso cuyas instrucciones cambian en casi cada ejecución: «resumir». Sobre ellos
> no podemos decirte nada.**
> Cuando el prompt de un paso lleva datos variables dentro —una fecha, un nombre—, cada
> llamada parece un paso distinto y las reglas no tienen dos llamadas que comparar: se
> callan. No es que ese paso esté bien, es que no lo hemos mirado.

Y cada señal dice qué hacer para subirla, no sólo que está baja. Cuando la cobertura es
mala, «no hay nada que arreglar» deja de ser un mensaje verde y pasa a ser **«no hemos
encontrado nada que arreglar, pero no hemos podido mirarlo todo»**.

## Quién puede leer y quién puede escribir

El **modo local no tiene cuentas por diseño**: es un proceso en tu portátil con tus
trazas. La **instalación de nube pide entrar** en todo lo que toca datos, y eso no depende
de que cada endpoint se acuerde de comprobarlo: hay un middleware que deniega por defecto y
una lista blanca de una sola entrada (`/health`). Una ruta nueva nace protegida.

**Personas: cuentas, organizaciones y roles.** Al arrancar por primera vez, el servidor
escribe en su log un código de un solo uso; con él se crea en `/configurar` la primera
cuenta, que administra la instalación y se queda con los proyectos que ya tengan datos.

```bash
docker compose logs backend | grep configurar
```

Desde ahí, todo se hace en **Organización**: invitar por enlace (y por correo, si hay
`LAPLACE_SMTP_*`), cambiar roles, quitar a alguien —que cierra sus sesiones al momento—,
y un registro de actividad. Cuatro roles:

| Rol | Puede |
|-----|-------|
| lector | ver |
| miembro | además anotar ejecuciones, marcar problemas, prompts y conjuntos |
| admin | además miembros, claves, alertas, presupuesto y borrar proyectos |
| propietario | además hacer y deshacer propietarios; la organización nunca se queda sin uno |

Contraseñas con `scrypt`; sesión en cookie `httpOnly` guardada como hash, que caduca a los
30 días; cambiar la contraseña cierra las demás sesiones; cinco intentos fallidos frenan
un cuarto de hora; y toda escritura con sesión lleva una cabecera propia que un
formulario de otro sitio no puede poner.

**Agentes: claves de API.** Se crean en Organización → Claves, por proyecto, con
caducidad opcional y fecha de último uso; escribir un nombre de proyecto nuevo lo crea a
nombre de la organización. La clave se enseña una vez: sólo se guarda su SHA-256. Va en
`Authorization: Bearer` y nunca en la URL, porque lo que va en la URL acaba en los logs de
cualquier proxy. Ata el proyecto en los dos sentidos: **con esa clave sólo se escriben
spans de ese proyecto** —si llegan de otro se rechaza el lote y se dice— y **sólo se leen
sus trazas**. Para scripts sigue existiendo la línea de comandos:

```bash
docker compose exec backend python -m laplace_backend.keys create --project mi-agente
```

Lo que todavía **no** hay: entrar con Google/GitHub (SSO) ni verificación de email.

## Los precios

`apps/backend/laplace_backend/pricing/model_prices.json` lleva versión, y cada modelo
declara de qué fuente oficial salen sus números y cuándo se verificó.

**La tabla caduca, y rápido.** No es una formalidad: el 30 de julio de 2026 OpenAI
recortó un modelo un 20 % y otro un 80 % el mismo día, y hay tarifas promocionales con
fecha de vencimiento. Hay que reverificarla **cada 30 días** y subir `version`. Tres
tests fallan solos si no se hace: uno cuando una fuente pasa de 30 días, otro cuando una
tarifa promocional (campo `expires`) ha vencido, y otro cuando aparece en las trazas un
modelo que no está en la tabla.

**Debajo va la tabla de LiteLLM**, en `litellm_prices.json`: unos 3.000 modelos más
(Gemini, Mistral, DeepSeek, Bedrock, Azure, OpenRouter…). Sólo se usa para lo que la
tabla propia no resuelve, y lo que sale de ella se marca como **tarifa no verificada**:
no es un suelo como la tarifa asumida, porque puede quedarse corta o pasarse. Un nombre
de gateway que LiteLLM conoce (`eu.anthropic.…`, `azure/…`) se cobra con su precio, que
no siempre es el del proveedor directo. Los modelos a cero en LiteLLM no entran. Un
trabajo semanal de CI (`precios-litellm.yml`) la regenera con
`scripts/precios_litellm.py` y abre una PR con un informe de dónde LiteLLM dice otra cosa
que la tabla propia; la propia no se cambia nunca sola.

Un modelo sin tarifa **no** cuesta cero: se marca como desconocido y la interfaz avisa de
que el total está incompleto. Se le puede poner precio desde **Ajustes**, y entonces se
recalcula también lo ya guardado, no sólo lo que llegue después.

Para ponerle precio a un modelo que no está en la tabla —o a uno con tarifa negociada—
sin tocar el paquete instalado, escribe tus tarifas en un JSON propio con el mismo
formato y arranca Laplace con `LAPLACE_PRICES_EXTRA` apuntando a él. Sus entradas se
suman a la tabla y, si coinciden, mandan:

```json
{"models": {"mi-modelo": {"input": 1.0, "output": 4.0}}}
```

### La entrada no se cobra a una sola tarifa

Un agente repite su prompt de sistema en cada paso, así que la caché salta siempre.
Cobrar toda la entrada a tarifa completa infla la factura del usuario y, con ella, el
ahorro que le prometemos. El coste se calcula por tramos:

| Tramo | Cómo se cobra |
|-------|---------------|
| Entrada nueva | Tarifa base del modelo |
| Leída de caché | ~10 % de la base (2,5 % en algún modelo) |
| Escrita en caché | 1,25× la base (2× si es la caché de una hora) |
| Salida | Tarifa de salida |

Sobre eso se apilan los metros que la petición pida: lote (−50 %), modo rápido (tarifa
propia o 2×) y residencia de datos (+10 %). `input_tokens` es siempre el **total
facturable** de entrada, con los tokens de caché dentro; los proveedores no coinciden en
esto y la normalización la hace el SDK, no el usuario.

Cuando no se puede saber qué metro aplicó —el tramo de contexto largo existe pero
OpenAI no publica a partir de cuántos tokens entra— se cobra el estándar, el span queda
marcado como **tarifa asumida** y la interfaz lo dice en modo avanzado. Ante la duda se
elige siempre la interpretación que produce **menos** ahorro, nunca la que engorda
nuestro número.

## Empezar en un minuto

Sin cuenta, sin servidor y sin Docker. Laplace entero corre en un proceso de Python
contra un fichero SQLite en `~/.laplace`:

```bash
pip install "laplace-trace[ui]"
laplace ui
```

Se abre el navegador. Para ver qué detecta antes de instrumentar nada, pulsa **«Cargar
datos de ejemplo»** en la pantalla vacía, o desde la terminal:

```bash
laplace demo          # trazas simuladas, en un proyecto aparte
```

Las trazas se guardan para siempre salvo que se arranque con `LAPLACE_RETENTION_DAYS`, y
un proyecto entero —trazas, anotaciones, conjuntos, prompts y ajustes— se borra desde
Ajustes.

Y para que aparezcan las tuyas, una línea en tu agente:

```python
import laplace
laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")
```

Es **el mismo producto** que la versión de nube: la misma ingesta, las mismas tres
reglas de detección, el mismo panel de ahorro y la misma interfaz. Lo único que cambia
es dónde están las filas (D-015), y hay un test que compara los dos almacenes sobre los
mismos spans para que no puedan derivar.

Medido de verdad, en un entorno limpio: **55 segundos** desde no tener nada instalado
hasta ver la primera traza en pantalla (49 s de `pip install`, 4 s de arranque, 2 s de
ingesta).

## Arrancar la instalación completa

```bash
docker compose up
```

Levanta ClickHouse, Postgres, el backend y la web. Cuando termine:

- Interfaz: <http://localhost:3000>
- API y ficha OpenAPI: <http://localhost:8000/docs>
- Ingesta OTLP: `http://localhost:4318` (puerto estándar, alias del backend)

Para ver datos de verdad, lanza el agente de ejemplo:

```bash
python examples/agente_ejemplo.py
```

No necesita claves de API: el modelo es falso, pero los nombres de modelo y los
recuentos de tokens son reales, así que el coste que calcula el backend es el que
costaría de verdad. Genera a propósito las patologías que el producto tiene que saber
enseñar: un bucle de tool calls, un paso trivial resuelto con un modelo caro, un agente
iterativo de ~30 pasos que se atasca, y una traza que falla.

### Desarrollo sin Docker

```bash
docker compose up -d clickhouse postgres     # sólo las bases de datos

python -m venv .venv && .venv/Scripts/activate   # o source .venv/bin/activate
pip install -e packages/sdk-python -e apps/backend
uvicorn laplace_backend.main:app --port 8000

npm --prefix apps/web install
npm --prefix apps/web run dev
```

El backend aplica sus migraciones al arrancar (ClickHouse y Postgres), así que no hay
paso previo de esquema.

## Instrumentar un agente

```python
import laplace

laplace.init(project="mi-agente")

@laplace.observe(type="agent")
def responder(pregunta: str) -> str:
    plan = planificar(pregunta)
    return redactar(buscar(plan))

@laplace.observe(type="tool")
def buscar(query: str) -> list[str]:
    ...
```

A partir de `init()`, cada llamada a OpenAI o Anthropic se traza sola. Detalles y
configuración en [`packages/sdk-python/README.md`](packages/sdk-python/README.md).

## Estructura

```
laplace/
├── packages/
│   ├── sdk-python/     # el paquete laplace-trace (se publica solo a PyPI)
│   └── sdk-js/         # pendiente
├── apps/
│   ├── backend/        # ingesta OTLP + API de lectura (FastAPI)
│   └── web/            # interfaz (Next.js)
├── examples/           # agentes de ejemplo, evaluación y prompts gestionados
├── docs/               # contrato de traza
├── docker-compose.yml
└── DECISIONS.md        # por qué está hecho así
```

Es un monorepo porque las tres capas comparten el contrato de traza y cambian juntas:
un cambio de campo es un único commit consistente en vez de tres repos desincronizados.

## Cómo funciona

1. **Captura.** El SDK emite spans de OpenTelemetry con las convenciones semánticas
   GenAI. No inventa un formato propio, así que cualquier proceso ya instrumentado con
   OTel puede exportar a Laplace y viceversa.
2. **Ingesta.** El backend recibe OTLP/HTTP en `/v1/traces`, clasifica cada span en uno
   de los cinco tipos del contrato, **calcula el coste desglosado** contra una tabla de
   precios por modelo y guarda en ClickHouse. Postgres guarda metadatos y las tablas
   reservadas para diagnósticos y evaluación.
3. **Producto.** La web lee el árbol ya resuelto (jerarquía, coste acumulado por
   subárbol, repeticiones marcadas) y lo pinta.

El contrato completo está en [`docs/trace-contract.md`](docs/trace-contract.md), y el
porqué de cada decisión no obvia en [`DECISIONS.md`](DECISIONS.md).

## Pruebas

```bash
pytest apps/backend/tests
ruff check packages/sdk-python apps/backend examples
npm --prefix apps/web run typecheck
```

Hay cuatro niveles:

- **Sin dependencias.** `test_ingest.py` recorre el camino real —SDK → spans OTel →
  protobuf OTLP → contrato— sin base de datos ni claves de API. Si el SDK y la ingesta
  se desincronizan, falla.
- **Contra ClickHouse y Postgres.** El SQL de la nube se ejecuta de verdad, y las
  pruebas de paridad exigen que los dos almacenes digan **lo mismo** sobre los mismos
  spans. Se saltan solas si no hay nada escuchando (`docker compose up -d clickhouse
  postgres` para activarlas); conviene levantarlo, porque un alias mal puesto o un
  `any()` donde el otro almacén pone un `MAX()` sólo se ven ejecutando la consulta.
- **Contra los SDK reales de los proveedores.** `test_proveedores_reales.py` usa
  `openai.OpenAI` y `anthropic.Anthropic` de verdad —su parseo, sus modelos, su lector de
  SSE— con el transporte HTTP falseado. No necesita clave ni gasta dinero. Las que además
  comprueban que nuestros números cuadran con lo que factura el proveedor se encienden
  con `LAPLACE_LIVE_TESTS=1` y la clave en el entorno, y cuestan céntimos.
- **Contra un modelo que corre en tu máquina.** `test_modelo_local.py` le pasa al cliente
  real de OpenAI un `base_url` que apunta a Ollama y una clave ficticia, así que el SDK
  publicado habla por HTTP con un modelo de verdad: coste cero y **nada falseado**, ni
  siquiera el cuerpo de la respuesta. Se salta solo si no hay servidor local; cómo
  prepararlo, en [`docs/tests-con-modelo-local.md`](docs/tests-con-modelo-local.md).
  Lo que un modelo local no puede dar —tokens cacheados— vive aparte y marcado en
  `test_modelo_local_simulado.py`.

  **Ninguno de estos dos últimos niveles valida el modelo de coste contra facturación.**
  Un modelo local no factura, su caché sólo reporta lecturas y con reglas propias, y
  cuenta tokens con otro tokenizador. Que
  salgan en verde significa que el camino funciona, no que las cifras sean las del
  proveedor: eso sólo lo dicen las cuatro pruebas vivas que esperan una clave.

`test_alerts.py`, `test_panel.py`, `test_evals.py` y `test_prompts.py` corren siempre y no
tocan la red: el notificador de
Slack se sustituye por uno que apunta lo que le mandan, y la lógica del panel —el
veredicto, la detección de picos y la atribución— es pura y se prueba sin almacén. La
mayoría de los casos de alertas comprueban **silencios**, y los del panel comprueban que
**no se dice de más**: ni una variación contra un periodo vacío, ni una causa que no esté
en las trazas, ni un porcentaje de acierto sacado de cuatro casos. Los de evaluación
comprueban además que el veredicto de una persona y el de un modelo no puedan mezclarse
ni siquiera a propósito, y los de prompts, que una versión sólo se lleve el tráfico que
de verdad produjo —el de reserva aparte— y que un pico no se atribuya a un despliegue por
la hora a la que se hizo. `test_auth.py` es casi todo **intentos de hacer lo que no se
debe poder**: ingerir con la clave de otro proyecto, leer trazas ajenas, abrir una traza
por su id sin decir el proyecto, llegar sin credencial. Y uno que no es un intento sino
una red: recorre las rutas registradas y exige que ninguna nazca abierta.

## Licencia

Apache-2.0.
