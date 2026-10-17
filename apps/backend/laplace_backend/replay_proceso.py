"""El replay que lanza el botón de la interfaz local (D-214), en un proceso aparte.

Hace lo mismo que `laplace replay`: llama a `laplace.replay.replay_dataset` y no
duplica nada de él. Aparte, porque `laplace.init()` es global y el backend no debe quedar
instrumentado; y con el entorno de `laplace ui`, de donde salen las claves del proveedor.

Protocolo: la especificación entra por stdin como JSON; por stdout salen líneas JSON,
`{"hechas": n, "total": m}` mientras avanza y, al final, `{"resultado": {...}}` o
`{"error": "..."}`.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def ejecutar(
    spec: dict[str, Any],
    *,
    client: Any = None,
    on_call: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Con `plan_only` sólo calcula el plan: no reenvía nada ni gasta. Con `acepta_usd`,
    no sigue si lo peor que puede costar es ahora mayor que lo que se enseñó."""
    from laplace.replay import replay_dataset

    def permiso(plan: Any) -> bool:
        if spec.get("plan_only"):
            return False
        return min(plan.worst_usd, plan.max_usd) <= float(spec["acepta_usd"]) + 1e-9

    resultado = replay_dataset(
        spec["dataset"],
        model=spec["model"],
        max_usd=float(spec["max_usd"]),
        project=spec["project_id"],
        endpoint=spec["endpoint"],
        confirm=permiso,
        client=client,
        proposal=spec.get("propuesta") or None,
        on_call=on_call,
    )
    return dataclasses.asdict(resultado)


def lanzar_proceso(
    spec: dict[str, Any], on_call: Callable[[int, int], None], *, python: str | None = None
) -> dict[str, Any]:
    """El lanzador de verdad: un proceso hijo con el entorno de este. Lanza `RuntimeError`
    con el motivo si el hijo dice `error` o muere sin resultado."""
    hijo = subprocess.Popen(
        [python or sys.executable, "-m", "laplace_backend.replay_proceso"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    assert hijo.stdin is not None and hijo.stdout is not None
    hijo.stdin.write(json.dumps(spec))
    hijo.stdin.close()
    final: dict[str, Any] = {}
    for linea in hijo.stdout:
        try:
            dato = json.loads(linea)
        except ValueError:
            continue
        if "hechas" in dato:
            on_call(dato["hechas"], dato["total"])
        else:
            final = dato
    hijo.wait()
    if "resultado" in final:
        return final["resultado"]
    raise RuntimeError(final.get("error") or f"el replay terminó con código {hijo.returncode}")


def _main() -> None:
    from laplace import init

    spec = json.load(sys.stdin)
    try:
        init(project=spec["project_id"], endpoint=spec["endpoint"])
        resultado = ejecutar(
            spec,
            on_call=lambda n, total: print(json.dumps({"hechas": n, "total": total}), flush=True),
        )
        print(json.dumps({"resultado": resultado}), flush=True)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo vuelve al botón como texto
        print(json.dumps({"error": str(exc) or type(exc).__name__}), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    _main()
