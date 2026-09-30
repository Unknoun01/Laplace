# Empezar

## En local, en un minuto

Sin cuenta, sin servidor y sin Docker. Laplace entero corre en un proceso de Python
contra un fichero SQLite en `~/.laplace`, y nada sale de tu máquina:

```bash
pip install "laplace-trace[ui]"
laplace ui
```

Se abre el navegador. Para ver qué detecta antes de instrumentar nada, pulsa **«Cargar
datos de ejemplo»**, o desde la terminal:

```bash
laplace demo
```

Son un mes de datos inventados, en un proyecto aparte llamado `demo`.

Para ver tus trazas, una línea en tu agente:

```python
import laplace

laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")
```

Sigue en [Instrumentar tu agente](instrumentar.md).

## La instalación completa

Para un equipo: ClickHouse para las trazas, Postgres para lo demás, el backend y la
web.

```bash
docker compose up
```

- Interfaz: <http://localhost:3000>
- API: <http://localhost:8000/docs>
- Ingesta OTLP: `http://localhost:4318`

La primera vez, el log del backend lleva un código de un solo uso para crear la primera
cuenta. Las claves de cada proyecto se crean en la pantalla de la organización.

### Si ya tenías datos de una versión anterior

Las instalaciones creadas antes de octubre de 2026 guardan las trazas ordenadas de una
forma con la que cada pantalla lee todo el histórico. El backend lo avisa al arrancar, y
se arregla a mano (copia la tabla; la ingesta sigue funcionando mientras tanto):

```bash
docker compose exec backend python -m laplace_backend.storage.migrar_orden --hacerlo
```
