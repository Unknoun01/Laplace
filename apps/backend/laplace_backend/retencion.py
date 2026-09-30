"""Retención por proyecto y borrado de los datos de una persona o un cliente (D-173).

`LAPLACE_RETENTION_DAYS` vale para toda la instalación (D-123). Pero dos proyectos de la
misma instalación no tienen por qué guardar lo mismo: el de pruebas, una semana; el de
producción, lo que diga su contrato. Y el cliente de un cliente puede pedir que se borre
lo suyo, que en una traza es lo que lleva su `user_id` o su `customer_id`.

* **La retención de un proyecto** se guarda en sus ajustes y la aplica el bucle de fondo
  de las alertas, que ya tiene turno entre procesos, una vez al día, como la traída de
  Stripe (D-163). Si la instalación tiene la suya, manda la más corta: un proyecto no
  puede guardar más de lo que la instalación permite.
* **El borrado de una persona o un cliente** se lleva las trazas enteras en las que
  aparece, no sólo los spans que llevan su id: el id lo lleva la raíz, y dejar las
  llamadas al modelo de esa traza sería dejar justo su contenido.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("laplace.retencion")

CLAVE = "retention"
CADA = timedelta(days=1)


def leer(metadata: Any, project_id: str) -> dict[str, Any]:
    try:
        return dict(metadata.get_setting(project_id, CLAVE) or {})
    except Exception:  # noqa: BLE001 - sin metadatos no hay retención por proyecto
        return {}


def dias_efectivos(propios: int, instalacion: int) -> int:
    """Los días que se guardan: la más corta de las dos que existan; 0 es «sin límite»."""
    candidatos = [d for d in (propios, instalacion) if d and d > 0]
    return min(candidatos) if candidatos else 0


def aplicar_si_toca(
    store: Any,
    metadata: Any,
    project_id: str,
    instalacion: int = 0,
    ahora: datetime | None = None,
) -> bool:
    """Borra lo que el proyecto no guarda, si hace más de un día de la última vez."""
    ahora = ahora or datetime.now(timezone.utc)
    ajustes = leer(metadata, project_id)
    dias = int(ajustes.get("days") or 0)
    if dias <= 0:
        return False
    ultima = ajustes.get("last_run")
    if ultima and ahora - datetime.fromisoformat(ultima) < CADA:
        return False
    corte = ahora - timedelta(days=dias_efectivos(dias, instalacion))
    try:
        store.delete_project_before(project_id, corte)
    except Exception:  # noqa: BLE001 - el bucle de fondo no puede caerse por esto
        logger.exception("retención: no se ha podido recortar %s", project_id)
        return False
    ajustes["last_run"] = ahora.isoformat()
    metadata.set_setting(project_id, CLAVE, ajustes)
    logger.info("retención: %s guarda %d días; borrado lo anterior a %s", project_id, dias,
                corte.date())
    return True
