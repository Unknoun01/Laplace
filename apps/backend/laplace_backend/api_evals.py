"""API de la pestaña de Evaluaciones (Fase 5).

Va en su propio router porque son tres cosas distintas —anotar, agrupar casos y
comparar tiradas— y metidas en `api.py` lo habrían convertido en un cajón. El router se
monta antes del comodín de la interfaz, igual que el otro.

Lo que **no** hay aquí es un endpoint que ejecute el agente de nadie. La tirada la corre
el SDK dentro del proceso del usuario y aquí se recibe el parte (D-086).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from laplace.schema import Annotation, Dataset, DatasetItem, EvalRun, EvalRunItem
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import identity_of
from .evals import Comparison, RunSummary, compare, summarize_run
from .judge import JudgeConfig, JudgeUnavailable, build_prompt, judge_trace
from .storage.base import TraceFilter
from .storage.metadata import MetadataUnavailable, new_id
from .tree import trace_io

logger = logging.getLogger("laplace.api.evals")

router = APIRouter(prefix="/api")


def _store(request: Request) -> Any:
    return request.app.state.store


def _meta(request: Request) -> Any:
    return request.app.state.metadata


def _alcance(request: Request) -> str | None:
    """El proyecto al que acotar un acceso que va por id opaco (D-121)."""
    # Con cuentas, una identidad tiene varios proyectos y «el suyo» ya no es uno: el
    # proyecto viene en la petición, y el middleware ya ha comprobado que puede tocarlo
    # (D-127). Sin él, lo de siempre.
    return identity_of(request).scope(request.query_params.get("project_id"))


def _guard(fn, *args, **kwargs):
    """Traduce «no hay dónde guardar» a un 503 con su explicación.

    Sin esto, en la nube con Postgres caído el usuario anotaría veinte trazas, vería un
    200 y las perdería todas sin enterarse.
    """
    try:
        return fn(*args, **kwargs)
    except MetadataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------------
# Anotaciones
# ---------------------------------------------------------------------------------


class AnnotationIn(BaseModel):
    """Lo que manda la interfaz al marcar una traza.

    No hay campo `source`: esta ruta **sólo** crea anotaciones humanas. El veredicto de
    máquina entra por la ruta del juez y por ninguna otra, de modo que ni un cliente
    equivocado ni un `curl` a mano pueden colar un veredicto de modelo disfrazado de
    persona (D-083).
    """

    project_id: str
    trace_id: str
    span_id: str | None = None
    verdict: str = Field(pattern="^(pass|fail|unknown)$")
    comment: str | None = None
    label: str | None = None
    author: str | None = None


@router.post("/annotations", response_model=Annotation)
async def create_annotation(request: Request, body: AnnotationIn) -> Annotation:
    """Marca una traza como buena o mala. Siempre con fuente humana."""
    anotacion = Annotation(
        id=new_id("an"),
        trace_id=body.trace_id,
        span_id=body.span_id,
        source="human",
        verdict=body.verdict,
        comment=body.comment,
        label=body.label,
        author=body.author or "yo",
        created_at=datetime.now(timezone.utc),
    )
    return await run_in_threadpool(
        _guard, _meta(request).save_annotation, body.project_id, anotacion
    )


@router.delete("/annotations/{annotation_id}")
async def delete_annotation(request: Request, annotation_id: str) -> dict[str, bool]:
    borrada = await run_in_threadpool(
        _guard, _meta(request).delete_annotation, annotation_id, _alcance(request)
    )
    if not borrada:
        raise HTTPException(status_code=404, detail="esa anotación ya no existe")
    return {"deleted": True}


@router.get("/annotations")
async def list_annotations(
    request: Request, trace_ids: str = Query(..., description="ids separados por coma")
) -> dict[str, Any]:
    """Anotaciones de varias trazas de una vez: lo usa el explorador para marcar filas."""
    ids = [t for t in trace_ids.split(",") if t]
    porciones = await run_in_threadpool(_meta(request).annotations_for, ids)
    return {
        "annotations": {
            k: [a.model_dump(mode="json") for a in v] for k, v in porciones.items()
        }
    }


# ---------------------------------------------------------------------------------
# El juez
# ---------------------------------------------------------------------------------


class JudgeIn(BaseModel):
    project_id: str
    #: Trazas sueltas, o todas las de una tirada.
    trace_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


@router.get("/judge")
async def judge_status(request: Request) -> dict[str, Any]:
    """Si el juez está configurado y con qué modelo. **La clave no sale nunca.**"""
    config: JudgeConfig = request.app.state.judge
    return {
        "enabled": config.enabled,
        "system": config.system,
        "model": config.model if config.enabled else "",
        "max_batch": request.app.state.settings.evals_judge_max_batch,
        "detail": (
            ""
            if config.enabled
            else "El juez está apagado. Enciéndelo con LAPLACE_EVALS_JUDGE_ENABLED=true "
            "y LAPLACE_EVALS_JUDGE_API_KEY. Anotar a mano funciona sin configurar nada."
        ),
    }


@router.post("/judge")
async def run_judge(request: Request, body: JudgeIn) -> dict[str, Any]:
    """Pasa el juez por unas trazas y guarda sus veredictos.

    Devuelve además **lo que ha costado juzgar**, porque es dinero que se acaba de
    gastar y el usuario tiene derecho a verlo en el mismo momento en que lo gasta.
    """
    config: JudgeConfig = request.app.state.judge
    if not config.enabled:
        raise HTTPException(status_code=503, detail=(await judge_status(request))["detail"])

    meta = _meta(request)
    ids = list(body.trace_ids)
    esperados: dict[str, Any] = {}
    if body.run_id:
        tirada = await run_in_threadpool(meta.get_run, body.run_id)
        if tirada is None:
            raise HTTPException(status_code=404, detail="esa tirada no existe")
        ids = [i.trace_id for i in tirada.items if not i.failed]
        casos = {
            c.id: c
            for c in await run_in_threadpool(meta.list_dataset_items, tirada.dataset_id)
        }
        esperados = {
            i.trace_id: casos[i.case_id].expected
            for i in tirada.items
            if i.case_id in casos
        }

    tope = request.app.state.settings.evals_judge_max_batch
    if len(ids) > tope:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(ids)} trazas pasan del tope de {tope}. Es un freno de mano a "
                f"propósito: juzgar cuesta dinero. Sube LAPLACE_EVALS_JUDGE_MAX_BATCH "
                f"si de verdad quieres gastar eso."
            ),
        )

    store = _store(request)
    hechas: list[Annotation] = []
    fallos: list[dict[str, str]] = []
    for trace_id in ids:
        spans = await run_in_threadpool(store.get_trace_spans, trace_id, body.project_id)
        try:
            anotacion = await run_in_threadpool(
                judge_trace, config, trace_id, spans, esperados.get(trace_id)
            )
        except JudgeUnavailable as exc:
            fallos.append({"trace_id": trace_id, "error": str(exc)})
            continue
        hechas.append(
            await run_in_threadpool(_guard, meta.save_annotation, body.project_id, anotacion)
        )

    coste = sum(a.judge.cost_usd for a in hechas if a.judge)
    incompleto = any(a.judge.cost_unknown for a in hechas if a.judge)
    return {
        "judged": len(hechas),
        "failed": fallos,
        "cost_usd": coste,
        "cost_unknown": incompleto,
        "model": config.model,
        "prompt_version": hechas[0].judge.prompt_version if hechas else "",
        "annotations": [a.model_dump(mode="json") for a in hechas],
    }


@router.get("/judge/prompt")
async def judge_prompt(request: Request, project_id: str, trace_id: str) -> dict[str, str]:
    """El texto exacto que se le mandaría al juez por esta traza.

    Modo avanzado. Un veredicto que no se puede auditar no se puede discutir, y el
    primer «este fail está mal» llega el día uno (D-067 aplicado al juez).
    """
    spans = await run_in_threadpool(_store(request).get_trace_spans, trace_id, project_id)
    if not spans:
        raise HTTPException(status_code=404, detail="traza no encontrada")
    from .judge import SYSTEM_PROMPT

    return {"system": SYSTEM_PROMPT, "user": build_prompt(spans)}


# ---------------------------------------------------------------------------------
# Conjuntos de casos
# ---------------------------------------------------------------------------------


class DatasetIn(BaseModel):
    """Un conjunto se crea desde un filtro del explorador, no a mano.

    Los casos salen de tráfico que ocurrió de verdad: un conjunto inventado no dice nada
    sobre el agente de nadie (D-085).
    """

    project_id: str
    name: str
    description: str = ""
    #: El mismo filtro que la lista de trazas. Se guarda tal cual para poder enseñarlo.
    filter: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=500)


@router.post("/datasets", response_model=Dataset)
async def create_dataset(request: Request, body: DatasetIn) -> Dataset:
    filtros = TraceFilter(
        project_id=body.project_id,
        limit=body.limit,
        since=_parse_dt(body.filter.get("since")),
        until=_parse_dt(body.filter.get("until")),
        status=body.filter.get("status") or None,
        session_id=body.filter.get("session_id") or None,
        search=body.filter.get("search") or None,
        step_key=body.filter.get("step_key") or None,
        span_type=body.filter.get("span_type") or None,
        model=body.filter.get("model") or None,
        min_cost_usd=_parse_float(body.filter.get("min_cost_usd")),
        sort=body.filter.get("sort") or "recent",
    )
    store = _store(request)
    pagina = await run_in_threadpool(store.list_traces, filtros)
    if not pagina.traces:
        raise HTTPException(
            status_code=400,
            detail="ese filtro no selecciona ninguna traza, así que el conjunto estaría vacío",
        )

    ahora = datetime.now(timezone.utc)
    dataset = Dataset(
        id=new_id("ds"),
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        created_at=ahora,
        source_filter={k: v for k, v in body.filter.items() if v not in (None, "")},
    )
    casos: list[DatasetItem] = []
    for resumen in pagina.traces:
        spans = await run_in_threadpool(
            store.get_trace_spans, resumen.trace_id, body.project_id
        )
        entrada, salida = trace_io(spans)
        casos.append(
            DatasetItem(
                id=new_id("case"),
                dataset_id=dataset.id,
                trace_id=resumen.trace_id,
                input=entrada,
                expected=salida,
                created_at=ahora,
            )
        )
    return await run_in_threadpool(_guard, _meta(request).create_dataset, dataset, casos)


def _parse_float(value: Any) -> float | None:
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("/datasets")
async def list_datasets(request: Request, project_id: str) -> dict[str, Any]:
    conjuntos = await run_in_threadpool(_meta(request).list_datasets, project_id)
    return {"datasets": [d.model_dump(mode="json") for d in conjuntos]}


@router.get("/datasets/{dataset_id}")
async def get_dataset(request: Request, dataset_id: str) -> dict[str, Any]:
    meta = _meta(request)
    conjunto = await run_in_threadpool(meta.get_dataset, dataset_id)
    if conjunto is None:
        raise HTTPException(status_code=404, detail="ese conjunto no existe")
    casos = await run_in_threadpool(meta.list_dataset_items, dataset_id)
    return {
        "dataset": conjunto.model_dump(mode="json"),
        "items": [c.model_dump(mode="json") for c in casos],
    }


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(request: Request, dataset_id: str) -> dict[str, bool]:
    borrado = await run_in_threadpool(
        _guard, _meta(request).delete_dataset, dataset_id, _alcance(request)
    )
    if not borrado:
        raise HTTPException(status_code=404, detail="ese conjunto no existe")
    return {"deleted": True}


# ---------------------------------------------------------------------------------
# Tiradas y comparación
# ---------------------------------------------------------------------------------


class RunIn(BaseModel):
    """El parte de una tirada que ya ha ocurrido en el proceso del usuario."""

    project_id: str
    dataset_id: str
    variant: str
    notes: str = ""
    items: list[EvalRunItem] = Field(default_factory=list)


@router.post("/runs", response_model=EvalRun)
async def create_run(request: Request, body: RunIn) -> EvalRun:
    meta = _meta(request)
    if await run_in_threadpool(meta.get_dataset, body.dataset_id) is None:
        raise HTTPException(status_code=404, detail="ese conjunto no existe")
    tirada = EvalRun(
        id=new_id("run"),
        project_id=body.project_id,
        dataset_id=body.dataset_id,
        variant=body.variant,
        notes=body.notes,
        created_at=datetime.now(timezone.utc),
        items=body.items,
    )
    return await run_in_threadpool(_guard, meta.create_run, tirada)


@router.get("/runs")
async def list_runs(
    request: Request, project_id: str, dataset_id: str | None = None
) -> dict[str, Any]:
    meta = _meta(request)
    tiradas = await run_in_threadpool(meta.list_runs, project_id, dataset_id)
    nombres = {
        d.id: d.name for d in await run_in_threadpool(meta.list_datasets, project_id)
    }
    resumenes: list[RunSummary] = []
    for tirada in tiradas:
        anotaciones, costes, prompts = await _context_for(request, project_id, tirada)
        resumenes.append(
            summarize_run(
                tirada,
                anotaciones,
                costes,
                nombres.get(tirada.dataset_id, ""),
                prompts,
            )
        )
    return {"runs": [r.model_dump(mode="json") for r in resumenes]}


async def _context_for(request: Request, project_id: str, *tiradas: EvalRun):
    """Anotaciones, costes y versiones de prompt de las trazas de unas tiradas.

    Tres consultas y no una por traza. Las versiones vienen de las mismas trazas que ya
    se están leyendo: desde la Fase 6, una comparación A vs B dice con qué prompt corrió
    cada lado, que es lo primero que se pregunta cuando el resultado sorprende (D-094).
    """
    ids = [i.trace_id for t in tiradas for i in t.items]
    anotaciones = await run_in_threadpool(_meta(request).annotations_for, ids)
    costes = await run_in_threadpool(_store(request).costs_for_traces, project_id, ids)
    prompts = await run_in_threadpool(
        _store(request).prompt_versions_by_trace, project_id, ids
    )
    return anotaciones, costes, prompts


@router.get("/experiments/compare", response_model=Comparison)
async def compare_runs(
    request: Request,
    project_id: str,
    a: str = Query(..., description="run_id de la versión de referencia"),
    b: str = Query(..., description="run_id de la versión nueva"),
) -> Comparison:
    """A vs B: acierto y coste a la vez. Es lo valioso de esta pestaña.

    Se comparan dos tiradas del **mismo** conjunto: comparar aciertos sobre casos
    distintos no querría decir nada, y es un error fácil de cometer con dos desplegables.
    """
    meta = _meta(request)
    run_a = await run_in_threadpool(meta.get_run, a)
    run_b = await run_in_threadpool(meta.get_run, b)
    if run_a is None or run_b is None:
        raise HTTPException(status_code=404, detail="alguna de las dos tiradas no existe")
    if run_a.dataset_id != run_b.dataset_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "las dos tiradas son de conjuntos distintos: comparar el acierto sobre "
                "casos diferentes no dice nada"
            ),
        )

    conjunto = await run_in_threadpool(meta.get_dataset, run_a.dataset_id)
    anotaciones, costes, prompts = await _context_for(request, project_id, run_a, run_b)
    return compare(
        run_a,
        run_b,
        anotaciones,
        costes,
        project_id=project_id,
        dataset_name=conjunto.name if conjunto else "",
        prompts=prompts,
    )
