---
paths:
  - "apps/backend/laplace_backend/storage/**"
  - "scripts/carga.py"
---

# Los almacenes y la carga

- `clickhouse.py`, `sqlite.py` y `preagregados.py` son grandes: Grep y leer sólo el trozo.
- Toda consulta nueva, en los dos almacenes y con su prueba de paridad sobre datos
  empatados (D-099): nada de `any()` ni `argMax` sin desempate en ClickHouse.
- Las pruebas de la nube necesitan `docker compose up -d clickhouse postgres`; sin ellos,
  unas 90 se saltan.
- `scripts/carga.py`: **nunca a la vez que la suite**, y con poco volumen en este portátil
  (15 GB): 80 millones de spans tumbaron Docker y dejaron partes rotas en ClickHouse.
  Después, `--borrar`.
