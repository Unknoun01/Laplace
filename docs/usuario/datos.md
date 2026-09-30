# Tus datos

## Qué se guarda

Cada paso de cada traza: nombres, tiempos, modelo, tokens, coste y, salvo que lo
apagues (`capture_content=False`), los mensajes, hasta 1 MiB por paso. En local, en un
fichero SQLite en `~/.laplace` que no sale de tu máquina.

## Cuánto tiempo

- **La instalación** guarda para siempre, salvo que arranque con
  `LAPLACE_RETENTION_DAYS`.
- **Cada proyecto** puede guardar menos, desde **Ajustes → Datos**. Manda la más corta de
  las dos, y lo anterior se borra una vez al día.

## Borrar

Desde **Ajustes → Datos**, repitiendo el nombre para confirmar:

- **Lo de una persona** (`user_id`) **o un cliente** (`customer_id`): se borran las
  trazas enteras en las que aparece, no sólo los pasos que llevan su id.
- **Un proyecto entero**: trazas, anotaciones, conjuntos de casos, prompts y ajustes.

Por la API: `DELETE /api/subjects` y `DELETE /api/projects`. En la versión con cuentas,
cada borrado queda anotado en la auditoría de la organización.
