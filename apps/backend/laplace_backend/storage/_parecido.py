"""El agrupado de la caché parecida (D-219), una sola vez para los dos almacenes.

Los almacenes devuelven una fila por pregunta distinta (la primera llamada de cada
`loop_hash` en un paso y modelo) y aquí se agrupan por la huella `sim_hash`. Así la
paridad es la de las filas y no la de dos agrupados escritos dos veces.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ._rows import utc
from .base import CacheableGroup

#: Bits de diferencia como mucho entre una pregunta y la cabeza de su grupo: una palabra
#: de diferencia en un texto de 100 palabras o más (medido en D-219).
UMBRAL_BITS = 4
#: Preguntas distintas por (paso, modelo) por encima de las cuales no se compara: con las
#: bandas de 16 bits harían falta ~n²/65.000 comparaciones. Callar no es afirmar.
MAX_PREGUNTAS_PASO = 20_000
#: Cinco bandas de 13 bits (la última de 12): dos huellas a `UMBRAL_BITS` (4) o menos
#: tienen al menos una banda entera igual (4 bits tocan como mucho 4 de las 5), así que
#: sólo se comparan las que comparten alguna. Con 8 bandas de 8 bits eran ~n/256
#: candidatas por pregunta, y 5.000 preguntas tardaban 7 s.
_BANDAS = ((0, 13), (13, 13), (26, 13), (39, 13), (52, 12))


def _bandas(huella: int) -> list[tuple[int, int]]:
    sin_signo = huella & 0xFFFF_FFFF_FFFF_FFFF
    return [(i, (sin_signo >> ini) & ((1 << ancho) - 1)) for i, (ini, ancho) in enumerate(_BANDAS)]


def _distancia(a: int, b: int) -> int:
    return ((a ^ b) & 0xFFFF_FFFF_FFFF_FFFF).bit_count()


def agrupar(
    filas: list[dict[str, Any]], *, min_mismas: int, limit: int = 20
) -> list[CacheableGroup]:
    """Los grupos de preguntas casi iguales por (paso, modelo).

    Cada pregunta se une a la primera cabeza a `UMBRAL_BITS` o menos, o es cabeza (sin
    encadenar: ninguna dista de su cabeza más que el umbral). Orden: (inicio, traza,
    span). Cuentan los miembros de otra ejecución que su cabeza (`repeated`) y, de ésos,
    los que dieron exactamente su salida (`same_answer`).
    """
    por_paso: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for f in filas:
        por_paso[(f["paso_clave"], f["request_model"])].append(f)

    grupos = []
    for (paso, modelo), preguntas in por_paso.items():
        preguntas.sort(key=lambda f: (str(f["start_time"]), f["trace_id"], f["span_id"]))
        cabezas: list[dict[str, Any]] = []
        indice: dict[tuple[int, int], list[int]] = defaultdict(list)
        miembros: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for p in preguntas:
            huella = int(p["sim_hash"])
            candidatas = sorted({c for b in _bandas(huella) for c in indice[b]})
            cabeza = next(
                (
                    c for c in candidatas
                    if _distancia(huella, int(cabezas[c]["sim_hash"])) <= UMBRAL_BITS
                ),
                None,
            )
            if cabeza is None:
                cabezas.append(p)
                for b in _bandas(huella):
                    indice[b].append(len(cabezas) - 1)
            elif p["trace_id"] != cabezas[cabeza]["trace_id"]:
                miembros[cabeza].append(p)

        parecidas = [m for c, ms in miembros.items() for m in ms]
        mismas = [
            m for c, ms in miembros.items() for m in ms
            if m["loop_out_hash"] == cabezas[c]["loop_out_hash"]
        ]
        if not parecidas or len(mismas) < min_mismas:
            continue
        grupos.append(
            CacheableGroup(
                name=max((p["etiqueta"] or "") for p in preguntas),
                model=modelo or "",
                step_key=paso or "",
                site=max((p["step_site"] or "") for p in preguntas),
                hint=max((p["step_hint"] or "") for p in preguntas),
                questions=len(miembros),
                repeated=len(parecidas),
                same_answer=len(mismas),
                same_answer_cost_usd=sum(float(m["cost_total_usd"] or 0.0) for m in mismas),
                same_answer_unknown_cost_spans=sum(
                    int(m["cost_unknown"] or 0) for m in mismas
                ),
                same_answer_assumed_rate_spans=sum(
                    int(m["cost_rate_assumed"] or 0) for m in mismas
                ),
                sample_trace_id=max((m["trace_id"] for m in mismas), default=""),
                last_seen=utc(max((p["start_time"] for p in preguntas), key=str)),
            )
        )
    grupos.sort(key=lambda g: (-g.same_answer_cost_usd, g.step_key, g.model))
    return grupos[:limit]
