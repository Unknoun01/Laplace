"""Las reglas que se ponen en Laplace —tope por ejecución, de bucles y la parada—.

Quien opera el agente no siempre puede tocar su código, y menos a las tres de la mañana
con un agente gastando. Desde Ajustes se pone, por proyecto, un tope de gasto por
ejecución, uno de bucles o se para todo; el SDK lo pide aquí y lo aplica con la misma
maquinaria que `laplace.guard` (D-184). Lo de Laplace **se suma** a lo del código y
manda el más estricto: nada de lo que se ponga en la interfaz puede aflojar un límite
escrito en el código.

Tres decisiones, las mismas que con los prompts (`prompts.py`, D-091):

1. **En segundo plano.** Un hilo pregunta cada `INTERVALO_SEGUNDOS`; la llamada al
   modelo sólo lee la última copia, que es leer una variable. Laplace nunca está en el
   camino caliente del agente por esto.
2. **Con copia.** Si Laplace no responde, o responde algo que no se entiende, se sigue
   con la última copia buena. Un agente parado sigue parado aunque Laplace se caiga.
3. **Sin copia, sin reglas.** Si Laplace no ha respondido nunca, el agente sigue como si
   no hubiera nada puesto. Un agente no se cae porque Laplace esté caído.

La copia es de un proyecto y de un Laplace: si `laplace.init()` cambia cualquiera de
los dos, se tira.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

from ._http import LaplaceHTTPError, endpoint, quote, request

logger = logging.getLogger("laplace")

#: Cada cuánto se pregunta. Es lo que tarda, como mucho, en llegar una parada.
INTERVALO_SEGUNDOS = 30.0
#: Lo que se espera a Laplace en cada pregunta. Va en segundo plano, pero un hilo
#: colgado medio minuto retrasaría la siguiente.
TIMEOUT_SEGUNDOS = 5.0


@dataclass(frozen=True)
class Reglas:
    """Lo puesto en Laplace para el proyecto. Sin nada, todo a `None` y sin parar."""

    max_usd_per_run: float | None = None
    max_loop: int | None = None
    stopped: bool = False

    @property
    def limita(self) -> bool:
        return self.max_usd_per_run is not None or self.max_loop is not None


SIN_REGLAS = Reglas()

_lock = threading.Lock()
_actuales: Reglas = SIN_REGLAS
#: De qué `(proyecto, endpoint)` es la copia.
_de: tuple[str, str] | None = None
_avisado = False

_hilo: threading.Thread | None = None
_parar = threading.Event()
_destino: dict[str, str | None] = {"project": None, "endpoint": None}


def actuales() -> Reglas:
    """La última copia buena. Es lo que miran las llamadas: no espera a nadie."""
    return _actuales


def desde_json(datos: Any) -> Reglas:
    """Lo que dice la API, comprobado. Lanza `ValueError` si no se entiende.

    Con los mismos límites que `guard()`: un tope de cero o un bucle de una vuelta no
    se aplican, porque cortarían todo y se leerían como un fallo del agente.
    """
    if not isinstance(datos, dict):
        raise ValueError(f"se esperaba un objeto y llegó {type(datos).__name__}")
    tope = datos.get("max_usd_per_run")
    bucle = datos.get("max_loop")
    parado = datos.get("stopped", False)
    if tope is not None:
        if isinstance(tope, bool) or not isinstance(tope, (int, float)):
            raise ValueError(f"max_usd_per_run no es un número: {tope!r}")
        if not math.isfinite(tope) or tope <= 0:
            raise ValueError(f"max_usd_per_run tiene que ser mayor que cero: {tope!r}")
        tope = float(tope)
    if bucle is not None:
        if isinstance(bucle, bool) or not isinstance(bucle, int) or bucle < 2:
            raise ValueError(f"max_loop tiene que ser un entero de 2 o más: {bucle!r}")
    if not isinstance(parado, bool):
        raise ValueError(f"stopped no es verdadero ni falso: {parado!r}")
    return Reglas(max_usd_per_run=tope, max_loop=bucle, stopped=parado)


def refrescar(project: str | None = None, endpoint_url: str | None = None) -> bool:
    """Pregunta una vez. True si hay reglas nuevas; False si se sigue con la copia."""
    global _actuales, _de, _avisado
    from ._tracer import get_config

    config = get_config()
    proyecto = project or (getattr(config, "project", "") if config else "")
    try:
        base = endpoint(endpoint_url)
    except LaplaceHTTPError:
        return False
    if not proyecto:
        return False
    clave = (proyecto, base)
    with _lock:
        if _de != clave:
            # Otro proyecto u otro Laplace: la copia que hubiera no es de éste.
            _actuales, _de = SIN_REGLAS, clave
    try:
        datos = request(
            f"{base}/api/control?project_id={quote(proyecto)}", timeout=TIMEOUT_SEGUNDOS
        )
        reglas = desde_json(datos)
    except (LaplaceHTTPError, ValueError) as exc:
        # Una vez por el log, y no cada 30 s: con Laplace caído llenaría el del usuario.
        nivel = logging.DEBUG if _avisado else logging.WARNING
        _avisado = True
        logger.log(
            nivel,
            "laplace: no se han podido pedir las reglas del proyecto «%s» (%s); se sigue "
            "con la última copia, o sin reglas si no la hay",
            proyecto,
            exc,
        )
        return False
    with _lock:
        if _de == clave:
            _actuales = reglas
    _avisado = False
    return True


def _bucle() -> None:
    while not _parar.is_set():
        try:
            refrescar(_destino["project"], _destino["endpoint"])
        except Exception:  # noqa: BLE001 - el hilo no puede morir por una pregunta
            logger.debug("laplace: fallo inesperado al pedir las reglas", exc_info=True)
        _parar.wait(INTERVALO_SEGUNDOS)


def arrancar(project: str | None = None, endpoint_url: str | None = None) -> None:
    """Empieza a preguntar en segundo plano. Llamarla otra vez sólo cambia el destino."""
    global _hilo
    _destino["project"] = project
    _destino["endpoint"] = endpoint_url
    with _lock:
        if _hilo is not None and _hilo.is_alive():
            return
        _parar.clear()
        # Daemon: el proceso del usuario termina aunque este hilo esté esperando.
        _hilo = threading.Thread(target=_bucle, name="laplace-reglas", daemon=True)
        _hilo.start()


def detener() -> None:
    global _hilo
    _parar.set()
    hilo = _hilo
    if hilo is not None and hilo is not threading.current_thread():
        hilo.join(timeout=TIMEOUT_SEGUNDOS + 1)
    _hilo = None


def reiniciar() -> None:
    """Sin hilo y sin copia. Lo usan las pruebas."""
    global _actuales, _de, _avisado
    detener()
    with _lock:
        _actuales, _de, _avisado = SIN_REGLAS, None, False
