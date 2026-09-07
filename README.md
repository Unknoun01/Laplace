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
| 1.7 | Modo local `laplace ui` con SQLite | pendiente |
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
| Contexto fijo | Un prompt con un suelo grande de tokens que se reenvía sin caché | Diferencia entre tarifa normal y de caché |

Cuatro cosas que **no** hace, a propósito: no inventa dinero donde no lo hay (un bucle de
herramientas no gasta tokens, así que enseña el tiempo perdido y lo dice), no cuenta dos
veces el mismo ahorro cuando dos reglas se solapan, no afirma que un modelo más barato
acertará igual (eso exige evaluaciones, que todavía no existen), y no presenta como
completo un total al que le faltan pasos cuyo modelo no tiene tarifa conocida.

## Los precios

`apps/backend/laplace_backend/pricing/model_prices.json` lleva versión, y cada modelo
declara de qué fuente oficial salen sus números y cuándo se verificó. **La tabla caduca**:
los proveedores cambian precios y retiran modelos, así que hay que reverificarla al menos
cada trimestre y subir `version`. Un test falla si las fuentes tienen más de 120 días.

Si un modelo aparece en tus trazas y no está en la tabla, su coste **no** se cuenta como
cero: se marca como desconocido y la interfaz avisa de que el total está incompleto. Otro
test falla si eso pasa con los datos que haya cargados.

## Arrancar en local

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
