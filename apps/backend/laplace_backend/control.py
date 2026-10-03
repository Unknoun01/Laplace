"""El plano de control: tope por ejecución, de bucles y la parada de un proyecto (D-187).

Se guarda como un ajuste más del proyecto y lo lee el SDK cada 30 s
(`laplace/_control.py`), que lo aplica con la maquinaria de `laplace.guard` y el más
estricto entre esto y lo del código. Aquí sólo se guarda y se devuelve: quien decide qué
pasa cuando Laplace no responde es el SDK, como con los prompts (D-091).

Los límites son los mismos que acepta `guard()` —un tope mayor que cero, bucles de 2 o
más— para que lo que se pone en la interfaz no pueda ser algo que el código rechazaría.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

CLAVE = "control"


class Control(BaseModel):
    """Lo puesto para un proyecto. Sin nada, todo a `None` y sin parar.

    `max_usd_per_run` no acaba en `_usd` a propósito: es un tope que pone el usuario,
    no una cifra que el producto afirme (el guardia de D-107 va por ésas).
    """

    project_id: str
    max_usd_per_run: float | None = None
    max_loop: int | None = None
    stopped: bool = False
    #: Cuándo se paró, en ISO-8601. Vacío si no está parado.
    stopped_at: str = ""
    updated_at: str = ""


class ControlIn(BaseModel):
    project_id: str
    max_usd_per_run: float | None = Field(default=None, gt=0)
    max_loop: int | None = Field(default=None, ge=2)
    stopped: bool = False


def leer(metadata: Any, project_id: str) -> Control:
    """Lo guardado, o nada puesto. Un valor que no se entiende cuenta como no puesto."""
    valor = metadata.get_setting(project_id, CLAVE) or {}
    try:
        entrada = ControlIn(
            project_id=project_id,
            max_usd_per_run=valor.get("max_usd_per_run"),
            max_loop=valor.get("max_loop"),
            stopped=bool(valor.get("stopped", False)),
        )
    except ValueError:  # la de pydantic también lo es
        return Control(project_id=project_id)
    return Control(
        **entrada.model_dump(),
        stopped_at=str(valor.get("stopped_at") or "") if entrada.stopped else "",
        updated_at=str(valor.get("updated_at") or ""),
    )


def guardar(metadata: Any, body: ControlIn, ahora: datetime | None = None) -> Control:
    ahora = ahora or datetime.now(timezone.utc)
    antes = leer(metadata, body.project_id)
    if not body.stopped:
        parado_desde = ""
    elif antes.stopped and antes.stopped_at:
        # Volver a guardar con la parada puesta no la mueve: se paró cuando se paró.
        parado_desde = antes.stopped_at
    else:
        parado_desde = ahora.isoformat()
    if body.max_usd_per_run is None and body.max_loop is None and not body.stopped:
        metadata.delete_setting(body.project_id, CLAVE)
        return Control(project_id=body.project_id, updated_at=ahora.isoformat())
    valor = {
        "max_usd_per_run": body.max_usd_per_run,
        "max_loop": body.max_loop,
        "stopped": body.stopped,
        "stopped_at": parado_desde,
        "updated_at": ahora.isoformat(),
    }
    metadata.set_setting(body.project_id, CLAVE, valor)
    return Control(**body.model_dump(), stopped_at=parado_desde, updated_at=ahora.isoformat())
