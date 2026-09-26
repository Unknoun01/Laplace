"""Correr un conjunto de casos contra una versión del agente.

    import laplace

    laplace.init(project="mi-agente", endpoint="http://127.0.0.1:8100")
    laplace.run_dataset("regresiones-checkout", mi_agente, variant="prompt-v3")

**Laplace no ejecuta el agente de nadie.** La tirada corre aquí, en el proceso del
usuario, con sus claves, sus dependencias y su red; lo que llega al backend son las
trazas por la vía normal más un parte de qué caso produjo qué traza. Un backend que
ejecutase código ajeno necesitaría un sandbox, las credenciales del usuario y una copia
de su entorno, que son tres problemas que este producto no tiene por qué resolver para
responder a «¿la versión nueva acierta igual y cuesta menos?» (D-086).

El cliente HTTP es el compartido del SDK (`_http`): `urllib` y nada más.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import semconv
from ._http import LaplaceHTTPError, endpoint, quote, request
from ._tracer import flush, get_config
from .decorators import get_current_trace_id, span, update_current_span

logger = logging.getLogger("laplace")


@dataclass
class Case:
    """Un caso del conjunto, tal y como llega del backend."""

    id: str
    input: Any = None
    expected: Any = None
    trace_id: str = ""


@dataclass
class RunResult:
    """El resultado de una tirada, ya registrado en el backend."""

    run_id: str
    variant: str
    dataset_id: str
    cases: int = 0
    failed: int = 0
    trace_ids: list[str] = field(default_factory=list)

    @property
    def url_hint(self) -> str:
        return f"compara esta tirada con otra en la pestaña Evaluaciones (run: {self.run_id})"


class EvalError(RuntimeError):
    """Algo del ciclo de evaluación no ha podido completarse."""


def _endpoint(explicit: str | None) -> str:
    try:
        return endpoint(explicit)
    except LaplaceHTTPError as exc:
        raise EvalError(str(exc)) from exc


def _request(url: str, *, method: str = "GET", payload: Any = None) -> Any:
    try:
        return request(url, method=method, payload=payload)
    except LaplaceHTTPError as exc:
        raise EvalError(str(exc)) from exc


# ---------------------------------------------------------------------------------
# El conjunto de casos
# ---------------------------------------------------------------------------------


def fetch_dataset(
    name_or_id: str, *, project: str, endpoint: str | None = None
) -> tuple[str, list[Case]]:
    """El conjunto y sus casos. Acepta el identificador o el nombre.

    Por el nombre porque es lo que alguien escribe en un script sin abrir la interfaz, y
    lo que sigue funcionando cuando el conjunto se vuelve a crear en otra máquina.
    """
    base = _endpoint(endpoint)
    dataset_id = name_or_id
    if not name_or_id.startswith("ds_"):
        lista = _request(
            f"{base}/api/datasets?project_id={quote(project)}"
        )["datasets"]
        coincide = [d for d in lista if d["name"] == name_or_id]
        if not coincide:
            nombres = ", ".join(d["name"] for d in lista) or "(ninguno)"
            raise EvalError(
                f"no hay ningún conjunto llamado «{name_or_id}» en el proyecto "
                f"«{project}». Los que hay: {nombres}"
            )
        dataset_id = coincide[0]["id"]

    datos = _request(f"{base}/api/datasets/{quote(dataset_id)}")
    casos = [
        Case(
            id=c["id"],
            input=c.get("input"),
            expected=c.get("expected"),
            trace_id=c.get("trace_id", ""),
        )
        for c in datos["items"]
    ]
    return datos["dataset"]["id"], casos


# ---------------------------------------------------------------------------------
# La tirada
# ---------------------------------------------------------------------------------


def run_dataset(
    dataset: str,
    fn: Callable[[Any], Any],
    *,
    variant: str,
    project: str | None = None,
    endpoint: str | None = None,
    notes: str = "",
    on_case: Callable[[int, int, Case], None] | None = None,
) -> RunResult:
    """Pasa todos los casos del conjunto por `fn` y registra la tirada.

    `fn` recibe la entrada del caso y devuelve lo que devuelva; lo único que importa es
    que por dentro llame al modelo con el SDK inicializado, porque de ahí salen las
    trazas con las que se mide acierto y coste.

    Un caso que lanza una excepción **no interrumpe la tirada y no se descarta**: se
    registra como fallado. Descartarlo haría que una versión que revienta la mitad de
    las veces saliera con el mismo acierto que una que funciona, y ésa es exactamente la
    mentira que esta pantalla existe para no contar.
    """
    config = get_config()
    project_id = project or (getattr(config, "project", "") if config else "")
    if not project_id:
        raise EvalError("no sé de qué proyecto es esto: pasa project= o llama a laplace.init()")

    base = _endpoint(endpoint)
    dataset_id, casos = fetch_dataset(dataset, project=project_id, endpoint=base)
    if not casos:
        raise EvalError(f"el conjunto «{dataset}» no tiene casos")

    items: list[dict[str, Any]] = []
    for indice, caso in enumerate(casos, start=1):
        if on_case:
            on_case(indice, len(casos), caso)
        fallado, error, trace_id = False, "", ""
        with span(
            f"eval:{variant}",
            type=semconv.SPAN_TYPE_AGENT,
            input=caso.input,
            tags=[semconv.EVAL_TAG, f"variant:{variant}"],
            metadata={"eval.case_id": caso.id, "eval.variant": variant},
        ):
            trace_id = get_current_trace_id() or ""
            try:
                salida = fn(caso.input)
                update_current_span(output=salida)
            except Exception as exc:  # noqa: BLE001 - un caso roto es un fallo, no un corte
                fallado, error = True, f"{type(exc).__name__}: {exc}"
                logger.warning("el caso %s ha reventado: %s", caso.id, error)
        items.append(
            {"case_id": caso.id, "trace_id": trace_id, "failed": fallado, "error": error[:500]}
        )

    # Las trazas tienen que estar escritas antes de registrar la tirada: si el parte
    # llegara primero, la comparación buscaría costes de trazas que aún no existen y
    # daría cero, que se leería como «gratis».
    flush()

    respuesta = _request(
        f"{base}/api/runs",
        method="POST",
        payload={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "variant": variant,
            "notes": notes,
            "items": items,
        },
    )
    return RunResult(
        run_id=respuesta["id"],
        variant=variant,
        dataset_id=dataset_id,
        cases=len(items),
        failed=sum(1 for i in items if i["failed"]),
        trace_ids=[i["trace_id"] for i in items if i["trace_id"]],
    )
