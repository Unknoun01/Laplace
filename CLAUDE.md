# Laplace

Observabilidad de agentes de IA: cuánto cuestan y si responden bien. Monorepo:
`packages/sdk-python` (SDK, `laplace.schema` es el contrato), `apps/backend` (FastAPI,
ingesta OTLP, reglas en `insights/`, almacenes en `storage/`), `apps/web` (Next.js),
`scripts/`, `docs/`. Lo que sólo vale para una parte (la web, los almacenes) está en
`.claude/rules/` y se carga al tocar esos ficheros.

## Antes de nada

- **`git fetch` y comparar con `origin/master`**, y otra vez antes de hacer push: hay
  sesiones en la nube que suben al mismo repositorio y chocan en los números de decisión.

## Qué leer y qué no

- Para empezar basta este fichero. Lo pendiente: `docs/HOJA_DE_RUTA.md` §3; las trampas:
  «Qué hay que vigilar» en `STATUS.md`. No leas enteros `STATUS.md` ni `DECISIONS.md`.
- Una decisión: `docs/decisiones-indice.md` la sitúa; después, Grep de `### D-123` en
  `DECISIONS.md` y leer sólo esa entrada.
- `graphify query "…"` para preguntas de arquitectura (`graphify-out/` lo pone al día el
  hook de git; no se reconstruye a mano).

## Cómo se trabaja

- Todo en español: código, comentarios, commits, textos. Los textos de la interfaz y del
  backend, en los cinco idiomas a la vez (`apps/web/lib/mensajes/*.ts`,
  `apps/backend/laplace_backend/textos/*.json`).
- Una rama por fase, un commit por bloque; en verde, `git merge --no-ff` a `master` y push.
- **Nada entra sin su prueba**: primero en rojo, después el arreglo, y comprobar que
  muerde rompiendo el código a propósito.
- Cada cambio con criterio lleva su `D-xxx` al final de `DECISIONS.md`, y después
  `python scripts/indice_decisiones.py`. La siguiente libre: **D-193** (compruébalo en
  `origin/master`).
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

- Suite: `.venv/Scripts/python.exe -m pytest apps/backend/tests -q` (unos 27 min con la
  nube y las pantallas): en segundo plano, y sólo al cerrar. Mientras se trabaja, los
  ficheros que toca el cambio, con `-x`.
- Lint: `.venv/Scripts/ruff.exe check apps/backend packages/sdk-python`.
- PowerShell: los here-strings `@'…'@` cierran en la columna 0, y el filtro de permisos
  confunde a veces `rm` o `--` dentro de un texto con un borrado: para eso, Bash o un
  script de Python.

## Para gastar menos

- Grep y lecturas acotadas antes que ficheros enteros.
- No lanzar dos cosas pesadas a la vez sobre ClickHouse.
- Una sesión por fase: al cerrar una, empezar otra nueva en vez de alargar ésta.
