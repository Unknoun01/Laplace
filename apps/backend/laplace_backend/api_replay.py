"""Lanzar el replay (D-167) desde la interfaz local, con un botón (D-214).

Sólo en local: en la nube el proceso es de todos y la clave sería la del servidor. Las
claves del proveedor se leen del entorno del proceso hijo, como las lee la orden; no se
piden en la pantalla ni pasan por aquí. El replay lo hace `replay_proceso`, que llama al
mismo código que `laplace replay`.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .replay_proceso import lanzar_proceso
from .textos import t

router = APIRouter(prefix="/api/replay")

_VARIABLE = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
#: Una tirada por proyecto a la vez. En memoria: si el backend se reinicia a medias, se
#: pierde el estado (no lo gastado, que ya está en las trazas).
_candado = threading.Lock()


class ReplayIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=200)
    dataset: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    max_usd: float = Field(gt=0, le=1000)
    #: Lo peor que se enseñó al pedir permiso. Sin él, no se lanza.
    acepta_usd: float = Field(default=0, ge=0)


def _exigir_local() -> None:
    if get_settings().store != "sqlite":
        raise HTTPException(status_code=404, detail="Not Found")


def _tiradas(request: Request) -> dict[str, dict[str, Any]]:
    if not hasattr(request.app.state, "replays"):
        request.app.state.replays = {}
    return request.app.state.replays


def _lanzador(request: Request) -> Any:
    return getattr(request.app.state, "lanzador", None) or lanzar_proceso


def _spec(request: Request, body: ReplayIn, **extra: Any) -> dict[str, Any]:
    spec = body.model_dump()
    spec["endpoint"] = str(request.base_url).rstrip("/")
    return {**spec, **extra}


def _variable(model: str) -> str:
    from laplace.replay import provider_for

    return _VARIABLE.get(provider_for(model), "")


@router.get("/estado")
async def estado(request: Request, project_id: str = Query(min_length=1)) -> dict[str, Any]:
    """Si hay botón (sólo en local) y cómo va la última tirada del proyecto."""
    if get_settings().store != "sqlite":
        return {"disponible": False, "tirada": None}
    return {"disponible": True, "tirada": _tiradas(request).get(project_id)}


@router.post("/plan")
async def plan(request: Request, body: ReplayIn) -> dict[str, Any]:
    """Lo que haría el replay y lo peor que puede costar, sin reenviar ni gastar nada."""
    _exigir_local()
    spec = _spec(request, body, plan_only=True)
    try:
        resultado = await run_in_threadpool(_lanzador(request), spec, lambda n, total: None)
    except Exception as exc:  # noqa: BLE001 - el motivo (sin tarifa, sin llamadas…) se enseña
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan = resultado["plan"]
    variable = _VARIABLE.get(plan["provider"], "")
    return {
        "plan": plan,
        # Nunca el valor, sólo si está: es el entorno de `laplace ui`.
        "clave": bool(variable and os.environ.get(variable)),
        "variable": variable,
        "peor_usd": min(plan["worst_usd"], plan["max_usd"]),
    }


@router.post("", status_code=202)
async def lanzar(request: Request, body: ReplayIn) -> dict[str, Any]:
    """Lanza la tirada en segundo plano. El plan se recalcula dentro: si lo peor ya es
    mayor que `acepta_usd`, la tirada termina `cancelada` sin haber gastado nada."""
    _exigir_local()
    variable = _variable(body.model)
    if not (variable and os.environ.get(variable)):
        raise HTTPException(status_code=409, detail=t("error.replay_sin_clave"))
    tiradas = _tiradas(request)
    with _candado:
        if (tiradas.get(body.project_id) or {}).get("estado") == "corriendo":
            raise HTTPException(status_code=409, detail=t("error.replay_en_curso"))
        tirada: dict[str, Any] = {
            "estado": "corriendo",
            "dataset": body.dataset,
            "model": body.model,
            "hechas": 0,
            "total": 0,
        }
        tiradas[body.project_id] = tirada
    spec = _spec(request, body)
    lanzador = _lanzador(request)

    def avanzar(hechas: int, total: int) -> None:
        tirada.update(hechas=hechas, total=total)

    def correr() -> None:
        try:
            resultado = lanzador(spec, avanzar)
        except Exception as exc:  # noqa: BLE001 - el fallo se enseña en la pantalla
            tirada.update(estado="fallo", error=str(exc))
            return
        tirada.update(estado="cancelada" if resultado.get("cancelled") else "hecha")
        tirada["resultado"] = resultado

    threading.Thread(target=correr, daemon=True).start()
    return tirada
