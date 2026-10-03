# Laplace

Observabilidad de agentes de IA: cuánto cuestan y si responden bien. Monorepo:
`packages/sdk-python` (SDK, `laplace.schema` es el contrato), `apps/backend` (FastAPI,
ingesta OTLP, reglas en `insights/`, almacenes en `storage/`), `apps/web` (Next.js),
`scripts/` (carga, precios, banco de integraciones JS), `docs/`.

## Antes de nada

- **`git fetch` y comparar con `origin/master`.** Hay sesiones en la nube que suben al
  mismo repositorio; trabajar sobre un `master` viejo duplica trabajo y choca en los
  números de decisión. Otra vez antes de hacer push.

## Qué leer y qué no

- **Para empezar basta este fichero.** Lo pendiente: `docs/HOJA_DE_RUTA.md` §3; las
  trampas conocidas: la sección «Qué hay que vigilar» de `STATUS.md`. No leas enteros
  `STATUS.md` (40 KB) ni `DECISIONS.md` (casi 300 KB).
- **Para una decisión:** `docs/decisiones-indice.md` la sitúa por tema; después, Grep de
  `### D-123` en `DECISIONS.md` y leer sólo esa entrada.
- Ficheros grandes (`storage/clickhouse.py`, `storage/sqlite.py`,
  `storage/preagregados.py`): Grep y leer sólo el trozo. Los estilos están en
  `apps/web/app/estilos/`, una hoja por pantalla.
- `graphify-out/` es un grafo del código (`graphify query "…"` para preguntas de
  arquitectura). Lo pone al día el hook de git con el código; no se reconstruye a mano.

## Cómo se trabaja

- Todo en español: código, comentarios, commits, textos. Los textos de la interfaz y del
  backend, en los cinco idiomas a la vez (`apps/web/lib/mensajes/*.ts`,
  `apps/backend/laplace_backend/textos/*.json`).
- Una rama por fase, un commit por bloque; en verde, `git merge --no-ff` a `master` y push.
- **Nada entra sin su prueba**: primero en rojo, después el arreglo, y comprobar que
  muerde rompiendo el código a propósito.
- Cada cambio con criterio lleva su `D-xxx` al final de `DECISIONS.md`, y después
  `python scripts/indice_decisiones.py` (una prueba exige el índice al día). La siguiente
  libre: **D-187** (compruébalo en `origin/master`).
- Los commits terminan con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Al cerrar una fase: la hoja de ruta y `STATUS.md` al día.

## Reglas del producto que no se tocan

Nunca afirmar lo que no se sabe; un «no lo sabemos» tiene que ser verdad. Un modelo sin
tarifa no cuesta cero, y una llamada sin recuento tampoco. Sin muestra suficiente no hay
porcentaje, y con márgenes solapados no hay ganador. No proyectar desde menos de un día.
No contar dos veces el mismo ahorro. Las tiradas de evaluación no son tráfico real.
Paridad entre SQLite y ClickHouse. El dinero se formatea sólo en `cifras.py` y
`lib/format.ts`; el color sólo sale de los tokens de los temas.

## Comandos (Windows)

- Backend: `.venv/Scripts/python.exe -m pytest apps/backend/tests -q` (unos 11 min con la
  nube y las pantallas). Mientras se trabaja, sólo los ficheros que toca el cambio, con `-x`.
- Lint: `.venv/Scripts/ruff.exe check apps/backend packages/sdk-python`.
- Nube: `docker compose up -d clickhouse postgres` (sin ellos, unas 90 pruebas se saltan).
- Web: `cd apps/web && npx tsc --noEmit`; tras tocarla, `.venv/Scripts/python.exe
  scripts/build_ui.py` (las pruebas de pantalla prueban `apps/web/out`).
- Si Ollama está abierto en `localhost:11434`, las pruebas de `test_modelo_local.py` generan
  de verdad y la suite tarda bastante más; no está colgada.
- Carga: `scripts/carga.py`. **Nunca a la vez que la suite** y con poco volumen en este
  portátil (15 GB): 80 millones de spans tumbaron Docker y dejaron partes rotas en
  ClickHouse. Después, `--borrar`.
- PowerShell: los here-strings `@'…'@` cierran en la columna 0, y el filtro de permisos
  confunde a veces `rm` o `--` dentro de un texto con un borrado: para eso, Bash o un
  script de Python.

## Para gastar menos

- Preferir Grep y lecturas acotadas a leer ficheros enteros.
- Lanzar en segundo plano lo largo (suite, carga) y no lanzar dos cosas pesadas a la vez
  sobre ClickHouse.
- Una sesión por fase: al cerrar una, empezar otra nueva en vez de alargar ésta.
