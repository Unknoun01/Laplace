"""API de la pestaña de Prompts (Fase 6).

Router propio por el mismo motivo que el de Evaluaciones: son cosas distintas y metidas
en `api.py` lo convertirían en un cajón.

Aquí hay una ruta que las demás pestañas no tienen: `/api/prompts/resolve`, que la
llama el **SDK del usuario en su camino caliente** para pedir el texto que va a mandar
al modelo. Eso cambia las prioridades de esa ruta y sólo de ésa: tiene que ser barata,
no puede exigir nada raro y su fallo tiene que ser legible, porque al otro lado hay un
agente en mitad de la petición de alguien. Lo que pasa cuando no responde lo decide el
SDK, no el backend (D-091).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from laplace.schema import Prompt, PromptVersion
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import identity_of
from .prompts import (
    Diff,
    PromptCard,
    PromptsView,
    build_card,
    diff_versions,
    observed_steps,
    verdicts_by_version,
)
from .storage.base import Window
from .storage.metadata import MetadataUnavailable, new_id
from .textos import t

logger = logging.getLogger("laplace.api.prompts")

router = APIRouter(prefix="/api/prompts")


def _store(request: Request) -> Any:
    return request.app.state.store


def _meta(request: Request) -> Any:
    return request.app.state.metadata


def _guard(fn, *args, **kwargs):
    """«No hay dónde guardar» es un 503 con su explicación, no un 200 que miente."""
    try:
        return fn(*args, **kwargs)
    except MetadataUnavailable as exc:
        # Lo que dice («postgres no responde: …») es para quien opera: al log.
        logger.warning("sin base de metadatos: %s", exc)
        raise HTTPException(status_code=503, detail=t("error.metadatos")) from exc


def _alcance(request: Request) -> str | None:
    """El proyecto al que hay que acotar un acceso que va por id opaco.

    `None` sólo para la clave de instalación, que ve todo. Para cualquier otra es su
    proyecto, y entonces un id ajeno simplemente no existe: 404, sin decir si existe en
    otro sitio. Es el patrón de lectura de D-097 aplicado también a la escritura, y al
    id opaco que el middleware no puede ver (D-121).
    """
    # Con cuentas, una identidad tiene varios proyectos y «el suyo» ya no es uno: el
    # proyecto viene en la petición, y el middleware ya ha comprobado que puede tocarlo
    # (D-127). Sin él, lo de siempre.
    return identity_of(request).scope(request.query_params.get("project_id"))


def _window(days: int) -> Window:
    until = datetime.now(timezone.utc)
    return Window(since=until - timedelta(days=days), until=until, days=days)


# ---------------------------------------------------------------------------------
# La ruta que usa el SDK
# ---------------------------------------------------------------------------------


@router.get("/resolve")
async def resolve_prompt(
    request: Request,
    project_id: str,
    name: str,
    version: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    """El texto que hay que mandar al modelo: el de producción, o el que se pida.

    **Se declara antes que `/{prompt_id}`**: FastAPI resuelve por orden de registro y
    una ruta con comodín declarada primero se comería ésta, que es justo la que está en
    el camino caliente de un agente ajeno.
    """
    meta = _meta(request)
    prompts = await run_in_threadpool(meta.list_prompts, project_id)
    prompt = next((p for p in prompts if p.name == name), None)
    if prompt is None:
        raise HTTPException(
            status_code=404,
            detail=t("error.prompt_no_hay", nombre=name, proyecto=project_id),
        )

    numero = version or prompt.production_version
    if not numero:
        raise HTTPException(
            status_code=409,
            detail=t("error.prompt_sin_produccion", nombre=name),
        )

    guardada = await run_in_threadpool(meta.get_prompt_version, prompt.id, numero)
    if guardada is None:
        raise HTTPException(
            status_code=404, detail=t("error.prompt_sin_version", nombre=name, version=numero)
        )
    return {
        "prompt_id": prompt.id,
        "name": prompt.name,
        "version": guardada.version,
        "text": guardada.text,
        "is_production": guardada.version == prompt.production_version,
    }


# ---------------------------------------------------------------------------------
# La pestaña
# ---------------------------------------------------------------------------------


async def _context(request: Request, project_id: str, window: Window):
    """Uso por versión y veredictos cruzados, que es de donde salen las métricas.

    Dos consultas al almacén de trazas y una a la de metadatos. El cruce va por las
    trazas **anotadas** y no por todo el tráfico de cada versión: las anotadas son unas
    decenas y el tráfico puede ser el proyecto entero.
    """
    # Sin las tiradas de evaluación, como las reglas (D-160): el coste y el acierto de
    # una versión son los de su tráfico real. Una comparación A/B lanzada con la v2 y el
    # modelo barato abarataba la v2 aquí y no en el Diagnóstico, y las dos pantallas
    # decían cifras distintas del mismo cambio. Las tiradas tienen su pestaña.
    uso = await run_in_threadpool(
        lambda: _store(request).prompt_usage(project_id, window, rules=True)
    )
    anotaciones = await run_in_threadpool(
        _meta(request).annotations_since, project_id, window.since
    )
    versiones = await run_in_threadpool(
        lambda: _store(request).prompt_versions_by_trace(
            project_id, [a.trace_id for a in anotaciones], sin_evaluaciones=True
        )
    )
    return uso, verdicts_by_version(anotaciones, versiones)


@router.get("", response_model=PromptsView)
async def list_prompts(
    request: Request, project_id: str, days: int = Query(7, ge=1, le=90)
) -> PromptsView:
    """La pestaña entera.

    Si no hay prompts gestionados **no se devuelve una pantalla vacía**: se devuelve lo
    que se puede inferir de las trazas, que es qué pasos han cambiado de instrucciones y
    cuándo. Es menos de lo que da adoptar la gestión, y se dice, pero es real y sale del
    tráfico de quien está mirando (D-093).
    """
    ventana = _window(days)
    meta = _meta(request)
    prompts = await run_in_threadpool(meta.list_prompts, project_id)
    vista = PromptsView(project_id=project_id, days=days, managed=bool(prompts))

    if prompts:
        uso, veredictos = await _context(request, project_id, ventana)
        for prompt in prompts:
            versiones = await run_in_threadpool(meta.list_prompt_versions, prompt.id)
            despliegues = await run_in_threadpool(meta.list_prompt_deploys, prompt.id)
            vista.prompts.append(
                build_card(prompt, versiones, uso, veredictos, despliegues)
            )

    observados = await run_in_threadpool(
        lambda: _store(request).observed_prompts(project_id, ventana, rules=True)
    )
    simultaneas = await run_in_threadpool(
        _store(request).co_occurring_step_keys, project_id, ventana
    )
    vista.observed = observed_steps(observados, simultaneous=simultaneas)
    if not observados:
        vista.observed_unavailable = (
            "Todavía no hay llamadas a modelos con instrucciones que mirar en este "
            "rango. En cuanto tu agente envíe una, aquí aparecerá con qué prompt corrió."
        )
    return vista


@router.get("/{prompt_id}", response_model=PromptCard)
async def get_prompt(
    request: Request,
    prompt_id: str,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> PromptCard:
    """La ficha de un prompt: versiones con su texto, historial y comparación."""
    meta = _meta(request)
    prompt = await run_in_threadpool(meta.get_prompt, prompt_id, _alcance(request))
    if prompt is None:
        raise HTTPException(status_code=404, detail=t("error.prompt_no_existe"))

    ventana = _window(days)
    uso, veredictos = await _context(request, project_id, ventana)
    versiones = await run_in_threadpool(meta.list_prompt_versions, prompt_id)
    despliegues = await run_in_threadpool(meta.list_prompt_deploys, prompt_id)
    return build_card(prompt, versiones, uso, veredictos, despliegues, with_text=True)


@router.get("/{prompt_id}/diff", response_model=Diff)
async def prompt_diff(
    request: Request, prompt_id: str, a: int = Query(..., ge=1), b: int = Query(..., ge=1)
) -> Diff:
    """Qué cambió entre dos versiones. `a` es la vieja y `b` la nueva."""
    meta = _meta(request)
    vieja = await run_in_threadpool(meta.get_prompt_version, prompt_id, a)
    nueva = await run_in_threadpool(meta.get_prompt_version, prompt_id, b)
    if vieja is None or nueva is None:
        raise HTTPException(status_code=404, detail=t("error.versiones_no_existen"))
    return diff_versions(vieja.text, nueva.text)


# ---------------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------------


class PromptIn(BaseModel):
    """Un prompt nuevo, con su primera versión. Las dos cosas a la vez, a propósito:
    un prompt sin texto no sirve para nada y obligaría a dos gestos para empezar."""

    project_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    #: Un prompt largo son decenas de miles de caracteres; esto es el tope de uno
    #: absurdo, que ocuparía la base y viajaría en cada `get_prompt()`.
    text: str = Field(min_length=1, max_length=200_000)
    notes: str = Field(default="", max_length=2000)
    author: str = Field(default="", max_length=120)


@router.post("", response_model=PromptCard)
async def create_prompt(request: Request, body: PromptIn) -> PromptCard:
    """Crea el prompt, guarda la versión 1 y **la pone en producción**.

    La primera sí se despliega sola. La alternativa —crear un prompt que
    `get_prompt()` no puede servir todavía— convierte el primer intento de cualquiera
    en un 409 sin explicación aparente. A partir de la segunda, guardar y desplegar son
    dos gestos distintos, que es lo que hace que exista el rollback.
    """
    meta = _meta(request)
    existentes = await run_in_threadpool(meta.list_prompts, body.project_id)
    if any(p.name == body.name for p in existentes):
        raise HTTPException(
            status_code=409,
            detail=t("error.prompt_ya_existe", nombre=body.name),
        )

    prompt = Prompt(
        id=new_id("pr"),
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        created_at=datetime.now(timezone.utc),
    )
    await run_in_threadpool(_guard, meta.create_prompt, prompt)
    await run_in_threadpool(
        _guard,
        meta.add_prompt_version,
        prompt.id,
        body.text,
        notes=body.notes or "primera versión",
        author=body.author or "yo",
    )
    await run_in_threadpool(
        _guard,
        meta.set_prompt_production,
        prompt.id,
        1,
        actor=body.author or "yo",
        note="primera versión",
    )
    return await get_prompt(request, prompt.id, body.project_id, 7)


class VersionIn(BaseModel):
    project_id: str
    text: str = Field(min_length=1, max_length=200_000)
    notes: str = Field(default="", max_length=2000)
    author: str = Field(default="", max_length=120)
    #: Ponerla en producción al guardarla. Por defecto **no**: guardar y desplegar son
    #: gestos distintos, y fundirlos haría imposible preparar una versión con calma.
    deploy: bool = False


@router.post("/{prompt_id}/versions", response_model=PromptVersion)
async def add_version(request: Request, prompt_id: str, body: VersionIn) -> PromptVersion:
    meta = _meta(request)
    if await run_in_threadpool(meta.get_prompt, prompt_id, _alcance(request)) is None:
        raise HTTPException(status_code=404, detail=t("error.prompt_no_existe"))
    version = await run_in_threadpool(
        _guard,
        meta.add_prompt_version,
        prompt_id,
        body.text,
        notes=body.notes,
        author=body.author or "yo",
    )
    if body.deploy:
        await run_in_threadpool(
            _guard,
            meta.set_prompt_production,
            prompt_id,
            version.version,
            actor=body.author or "yo",
            note=body.notes,
        )
    return version


class DeployIn(BaseModel):
    project_id: str
    version: int = Field(ge=1)
    actor: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)


@router.post("/{prompt_id}/production")
async def set_production(request: Request, prompt_id: str, body: DeployIn) -> dict[str, Any]:
    """Pone una versión en producción. Volver a una anterior es el rollback de un clic.

    A partir de aquí, `laplace.get_prompt()` sirve ésta. En un proceso ya arrancado
    tarda como mucho lo que dure su caché —un minuto—, y eso se dice en la respuesta:
    un rollback del que no se sabe cuándo hace efecto no tranquiliza a nadie.
    """
    meta = _meta(request)
    prompt = await run_in_threadpool(meta.get_prompt, prompt_id, _alcance(request))
    if prompt is None:
        raise HTTPException(status_code=404, detail=t("error.prompt_no_existe"))
    if await run_in_threadpool(meta.get_prompt_version, prompt_id, body.version) is None:
        raise HTTPException(
            status_code=404,
            detail=t("error.prompt_sin_version", nombre=prompt.name, version=body.version),
        )

    despliegue = await run_in_threadpool(
        _guard,
        meta.set_prompt_production,
        prompt_id,
        body.version,
        actor=body.actor or "yo",
        note=body.note,
    )
    return {
        "deploy": despliegue.model_dump(mode="json"),
        "detail": t("estado.prompt_desplegado", version=body.version),
    }


@router.delete("/{prompt_id}")
async def delete_prompt(request: Request, prompt_id: str) -> dict[str, bool]:
    """Borra el prompt y todo su histórico.

    Las trazas no se tocan: siguen diciendo con qué versión corrieron, aunque el texto
    ya no esté. Es información que ocurrió y no es nuestra para borrarla de ahí.
    """
    borrado = await run_in_threadpool(
        _guard, _meta(request).delete_prompt, prompt_id, _alcance(request)
    )
    if not borrado:
        raise HTTPException(status_code=404, detail=t("error.prompt_no_existe"))
    return {"deleted": True}
