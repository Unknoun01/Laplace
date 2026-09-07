# Laplace

Observabilidad y optimización de agentes de IA.

Cuando un agente falla, tarda o dispara la factura de tokens, los logs normales no
sirven: no capturan el árbol de decisiones. Laplace lo hace visible. Instalas una
librería con una línea y cada ejecución queda registrada como una traza jerárquica: qué
paso se dio, qué prompt se envió, qué respondió el modelo, qué herramienta se llamó,
cuántos tokens y cuánto costó cada nodo.

El ciclo completo es **observar → evaluar → optimizar**. Hoy está construida la primera
parte y los cimientos de las otras dos.

## Estado

| Fase | Qué es | Estado |
|------|--------|--------|
| 0 | Monorepo, `docker-compose`, contrato de traza, esquemas | ✅ |
| 1.4 | SDK de Python (`@observe` + auto-instrumentación) | ✅ |
| 1.5 | Ingesta OTLP + API de lectura | ✅ |
| 1.6 | Lista de trazas + vista de árbol | ✅ |
| 1.7 | Modo local `laplace ui` con SQLite | ✅ |
| 2 | Detección de derroche y **panel de ahorro** | ✅ |
| 2.b | Alertas (Slack) cuando se supera un umbral | pendiente |
| 3 | **Diagnóstico automático** con modelo | pendiente |
| 4+ | Evaluación, gestión de prompts, dashboards | pendiente |

Las fases 2 y 3 son el producto, no extras: son lo que separa a Laplace de un visor de
trazas.

## Las dos mitades de la interfaz

El mismo producto sirve a dos públicos con un botón. En **Diagnóstico** se habla en
cristiano y manda el dinero; en **Avanzado** aparece la capa técnica: la consulta que
disparó cada alerta, los atributos de cada span, el árbol completo y la exportación en
JSON. El modo es global y se recuerda.

- **Diagnóstico** (`/`) — cuánto te cuesta el agente, cuánto puedes dejar de pagar, y
  las cosas que arreglar ordenadas por dinero recuperable.
- **Problema** (`/problemas/…`) — qué pasa, por qué, cómo se ha detectado, cómo se
  arregla y cuánto te ahorras, con el cálculo detrás.
- **Trazas** (`/trazas`) — exploración libre: filtros, búsqueda y orden por coste.
- **Traza** (`/trazas/…`) — el árbol navegable, con coste por rama y repeticiones
  marcadas.

## Qué detecta hoy

Tres reglas deterministas, sin modelo de por medio
([`insights.py`](apps/backend/laplace_backend/insights.py)):

| Regla | Qué busca | Cómo calcula el ahorro |
|-------|-----------|------------------------|
| Repetición | El mismo paso, con la misma entrada, 3+ veces en una ejecución | Coste íntegro de las copias sobrantes |
| Modelo caro | Un paso con salida media corta que usa un modelo con alternativa más barata | Diferencia de tarifa sobre los tokens reales |
| Contexto fijo | Un prompt con un suelo grande de tokens que se reenvía sin caché | Diferencia entre tarifa normal y de caché, menos lo que cuesta escribirla |

Cuatro cosas que **no** hace, a propósito: no inventa dinero donde no lo hay (un bucle de
herramientas no gasta tokens, así que enseña el tiempo perdido y lo dice), no cuenta dos
veces el mismo ahorro cuando dos reglas se solapan, no afirma que un modelo más barato
acertará igual (eso exige evaluaciones, que todavía no existen), y no presenta como
completo un total al que le faltan pasos cuyo modelo no tiene tarifa conocida.

## Los precios

`apps/backend/laplace_backend/pricing/model_prices.json` lleva versión, y cada modelo
declara de qué fuente oficial salen sus números y cuándo se verificó.

**La tabla caduca, y rápido.** No es una formalidad: el 30 de julio de 2026 OpenAI
recortó un modelo un 20 % y otro un 80 % el mismo día, y hay tarifas promocionales con
fecha de vencimiento. Hay que reverificarla **cada 30 días** y subir `version`. Tres
tests fallan solos si no se hace: uno cuando una fuente pasa de 30 días, otro cuando una
tarifa promocional (campo `expires`) ha vencido, y otro cuando aparece en las trazas un
modelo que no está en la tabla.

Un modelo sin tarifa **no** cuesta cero: se marca como desconocido y la interfaz avisa de
que el total está incompleto.

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

Se abre el navegador. Para ver qué detecta antes de instrumentar nada:

```bash
laplace demo          # trazas simuladas, en un proyecto aparte
```

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
├── examples/           # agente de ejemplo instrumentado
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

Hay dos niveles:

- **Sin dependencias.** `test_ingest.py` recorre el camino real —SDK → spans OTel →
  protobuf OTLP → contrato— sin base de datos ni claves de API. Si el SDK y la ingesta
  se desincronizan, falla.
- **Contra ClickHouse.** `test_clickhouse_store.py` ejecuta el SQL de verdad y **se salta
  solo** si no hay ClickHouse escuchando (`docker compose up -d clickhouse` para
  activarlo). Cubre lo que las pruebas en memoria no pueden ver: filtros, agregaciones,
  paginación e idempotencia al reescribir un span.

## Licencia

Apache-2.0.
