"""Lo que pasa con un hallazgo después de verlo: arreglado, ignorado, o que vuelve.

Existe porque la ficha tenía un botón «Ya lo he arreglado, vuelve a medir» que sólo
recargaba la página. Las cifras cuentan la ventana entera, así que el arreglo no se veía
hasta que lo viejo salía del rango: el producto no podía contestar la única pregunta que
importa después de tocar el código, que es si ha servido (D-123).

Dos estados y una consecuencia:

* **Ignorado.** El usuario ha decidido que no es un problema para él. Sale de la lista y
  del evitable, y deja de alertar. Se puede deshacer.
* **Arreglado.** Se compara la ejecución media de antes con la de después de marcarlo.
  Si el problema sigue saliendo igual, vuelve a la lista como **reaparecido**: fiarse de
  lo que dice el usuario sin mirar sería esconder justo el caso que importa.

La comparación es **por ejecución**, nunca en totales: con la mitad de tráfico después,
el total baja solo y parecería un arreglo que no ha ocurrido.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from . import cifras
from .insights import Finding, FixCheck, detect
from .storage.base import Window
from .textos import t, tn

logger = logging.getLogger("laplace.seguimiento")

ESTADOS = ("arreglado", "ignorado")
PREFIJO = "finding:"
#: Por debajo de esto, después de marcarlo, no hay con qué decir nada. Es la misma cifra
#: que usa el panel para comparar periodos, por la misma razón (D-077).
MIN_EJECUCIONES = 5
#: Hasta dónde se mira hacia atrás para saber cómo era «antes».
ANTES = timedelta(days=7)
#: Por debajo de esta fracción de lo de antes, se considera que ha mejorado aunque
#: siga apareciendo.
MEJORA = 0.5


def clave(finding_id: str) -> str:
    return f"{PREFIJO}{finding_id}"


def leer_estados(metadata: Any, project_id: str) -> dict[str, dict[str, Any]]:
    """Los estados de un proyecto. Sin base de metadatos, ninguno: no es un error."""
    try:
        filas = metadata.list_settings(project_id, PREFIJO)
    except Exception:  # noqa: BLE001
        logger.warning("no se pueden leer los estados de %s", project_id, exc_info=True)
        return {}
    return {k[len(PREFIJO) :]: v for k, v in filas.items() if isinstance(v, dict)}


def _unidad(finding: Finding) -> str:
    if finding.costs_money:
        return "usd"
    if finding.window_waste_tokens:
        return "tokens"
    return "ms"


def _valor(finding: Finding, unidad: str) -> float:
    return {
        "usd": finding.window_waste_usd,
        "tokens": float(finding.window_waste_tokens),
        "ms": finding.window_waste_ms,
    }[unidad]


def _escribir(valor: float, unidad: str) -> str:
    if unidad == "usd":
        return cifras.dinero(valor)
    if unidad == "tokens":
        return t("unidad.tokens", n=cifras.miles(valor))
    if valor >= 1000:
        return f"{cifras.decimal(valor / 1000, 2)} s"
    return f"{cifras.miles(valor)} ms"


def _parse(valor: Any) -> datetime:
    fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def _por_ejecucion(
    store: Any, project_id: str, finding_id: str, unidad: str, ventana: Window
) -> tuple[int, float | None, bool]:
    resumen = store.summarize_window(project_id, ventana)
    if resumen.traces == 0:
        return 0, None, False
    hallado = next((f for f in detect(store, project_id, ventana) if f.id == finding_id), None)
    valor = _valor(hallado, unidad) if hallado else 0.0
    return resumen.traces, valor / resumen.traces, bool(hallado and hallado.cost_is_floor)


def comprobar(
    store: Any,
    project_id: str,
    finding: Finding,
    marcado: datetime,
    ahora: datetime | None = None,
) -> FixCheck:
    """Antes y después de `marcado`, por ejecución."""
    ahora = ahora or datetime.now(timezone.utc)
    unidad = _unidad(finding)
    dias_despues = max(1, math.ceil((ahora - marcado).total_seconds() / 86400))
    antes = Window(since=marcado - ANTES, until=marcado, days=ANTES.days)
    despues = Window(since=marcado, until=ahora, days=dias_despues)

    n_antes, por_antes, suelo_antes = _por_ejecucion(store, project_id, finding.id, unidad, antes)
    n_despues, por_despues, suelo_despues = _por_ejecucion(
        store, project_id, finding.id, unidad, despues
    )
    check = FixCheck(
        marked_at=marcado,
        unit=unidad,
        runs_before=n_antes,
        runs_after=n_despues,
        before_per_run=por_antes,
        after_per_run=por_despues,
        cost_is_floor=suelo_antes or suelo_despues,
    )

    if n_despues < MIN_EJECUCIONES:
        check.verdict = "pendiente"
        check.headline = tn(
            "seguimiento.pendiente",
            n_despues,
            n=cifras.miles(n_despues),
            minimo=MIN_EJECUCIONES,
        )
        return check

    ahora_txt = _escribir(por_despues or 0.0, unidad)
    if por_antes is None:
        # Sin antes no hay ahorro que calcular: sólo se puede decir si sigue saliendo.
        check.verdict = "arreglado" if not por_despues else "sigue"
        check.headline = (
            t("seguimiento.sin_antes.no_aparece", n=cifras.miles(n_despues))
            if not por_despues
            else t("seguimiento.sin_antes.sigue", n=cifras.miles(n_despues), ahora=ahora_txt)
        )
        return check

    antes_txt = _escribir(por_antes, unidad)
    if not por_despues:
        check.verdict = "arreglado"
        check.saved = por_antes * n_despues
        ahorrado = _escribir(check.saved, unidad)
        if check.cost_is_floor:
            ahorrado = t("seguimiento.al_menos", x=ahorrado)
        check.headline = t(
            "seguimiento.arreglado",
            n=cifras.miles(n_despues),
            antes=antes_txt,
            ahorrado=ahorrado,
        )
    elif por_despues <= por_antes * MEJORA:
        check.verdict = "mejor"
        check.saved = (por_antes - por_despues) * n_despues
        check.headline = t("seguimiento.mejor", antes=antes_txt, ahora=ahora_txt)
    else:
        check.verdict = "sigue"
        check.headline = t("seguimiento.sigue", antes=antes_txt, ahora=ahora_txt)
    return check


def aplicar_estados(
    store: Any,
    project_id: str,
    findings: list[Finding],
    estados: dict[str, dict[str, Any]],
    ahora: datetime | None = None,
) -> tuple[list[Finding], list[Finding]]:
    """Separa lo que sigue pendiente de lo que el usuario ya ha resuelto.

    Un arreglado que sigue saliendo igual vuelve a la lista como `reaparecido`.
    """
    activos: list[Finding] = []
    apartados: list[Finding] = []
    for finding in findings:
        estado = estados.get(finding.id)
        if not estado or estado.get("status") not in ESTADOS:
            activos.append(finding)
            continue
        finding.state = estado["status"]
        finding.state_note = str(estado.get("note") or "")
        try:
            finding.state_at = _parse(estado.get("at"))
        except (TypeError, ValueError):
            finding.state_at = None
        if finding.state == "ignorado" or finding.state_at is None:
            apartados.append(finding)
            continue
        finding.fix_check = comprobar(store, project_id, finding, finding.state_at, ahora)
        if finding.fix_check.verdict == "sigue":
            finding.state = "reaparecido"
            activos.append(finding)
        elif finding.fix_check.verdict == "mejor":
            # Ha bajado pero sigue ahí: apartarlo sería dar por cerrado algo abierto.
            activos.append(finding)
        else:
            apartados.append(finding)
    return activos, apartados
