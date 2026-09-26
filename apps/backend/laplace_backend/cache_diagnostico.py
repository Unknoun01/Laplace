"""El Diagnóstico, recordado un minuto en la nube y renovado antes de caducar (D-142, D-143).

Con diez millones de spans al día, el Diagnóstico de un proyecto grande tarda unos 4 s,
y se pide al abrir el inicio, la lista de trazas y cada traza. Durante un minuto se sirve
el mismo. Lo que llega por la ingesta en ese minuto no se ve hasta el siguiente, que es
el precio; lo que cambia por la API —marcar un hallazgo, una tarifa, la demo— borra la
caché en el momento, porque quien lo hace espera verlo ya.

Y para que ese minuto no termine en una espera de 4 s, un proceso de fondo recalcula
cada Diagnóstico cuando lleva tres cuartos de su vida guardado, **sólo si alguien lo ha
mirado en la última media hora**: lo que nadie abre no gasta ClickHouse. La primera vez
que se abre un proyecto sigue tardando lo que tarda.

En local no hay caché ni renovación: el volumen no lo necesita, y quien acaba de mandar
trazas desde su portátil espera verlas.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("laplace.diagnostico")

#: Diagnósticos guardados como mucho. Cada uno es un proyecto y una ventana.
MAXIMO = 256
#: Se renueva cuando ha pasado esta fracción de su vida en la caché.
RENOVAR_A = 0.75
#: Sólo se renueva lo que alguien ha leído en este tiempo.
MIRADO_HACE_S = 30 * 60
#: Cada cuánto mira el proceso de fondo si hay algo que renovar.
VUELTA_S = 5


@dataclass
class _Entrada:
    guardado: float
    valor: Any
    recalcular: Callable[[], Any] | None
    leido: float


class CacheDiagnostico:
    def __init__(self, reloj: Callable[[], float] = time.monotonic) -> None:
        self._datos: OrderedDict[Any, _Entrada] = OrderedDict()
        self._cerrojo = threading.Lock()
        self._reloj = reloj

    def leer(self, clave: Any, segundos: int) -> Any | None:
        if segundos <= 0:
            return None
        with self._cerrojo:
            entrada = self._datos.get(clave)
            ahora = self._reloj()
            if entrada is None or ahora - entrada.guardado > segundos:
                return None
            entrada.leido = ahora
            return entrada.valor

    def guardar(
        self,
        clave: Any,
        valor: Any,
        segundos: int,
        recalcular: Callable[[], Any] | None = None,
    ) -> None:
        if segundos <= 0:
            return
        with self._cerrojo:
            ahora = self._reloj()
            self._datos[clave] = _Entrada(ahora, valor, recalcular, ahora)
            self._datos.move_to_end(clave)
            while len(self._datos) > MAXIMO:
                self._datos.popitem(last=False)

    def olvidar(self) -> None:
        with self._cerrojo:
            self._datos.clear()

    # -- renovación ----------------------------------------------------------------

    def _pendientes(self, segundos: int) -> list[tuple[Any, _Entrada]]:
        ahora = self._reloj()
        with self._cerrojo:
            return [
                (clave, e)
                for clave, e in self._datos.items()
                if e.recalcular is not None
                and ahora - e.guardado >= segundos * RENOVAR_A
                and ahora - e.leido <= MIRADO_HACE_S
            ]

    def _aplicar(self, clave: Any, entrada: _Entrada, valor: Any) -> bool:
        with self._cerrojo:
            # Si mientras se recalculaba alguien borró la caché (un cambio por la API),
            # el valor nuevo se calculó con datos de antes: no se guarda.
            if self._datos.get(clave) is not entrada:
                return False
            entrada.valor = valor
            entrada.guardado = self._reloj()
            return True

    def renovar_pendientes(self, segundos: int) -> int:
        """Una vuelta en el hilo actual. Devuelve cuántos se renovaron."""
        hechos = 0
        for clave, entrada in self._pendientes(segundos):
            try:
                valor = entrada.recalcular()  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - se queda el de antes hasta que caduque
                logger.warning("no se pudo renovar un Diagnóstico", exc_info=True)
                continue
            hechos += self._aplicar(clave, entrada, valor)
        return hechos

    def _envejecer(self, segundos: float) -> None:
        """Para pruebas: como si todo llevara `segundos` más guardado."""
        with self._cerrojo:
            for entrada in self._datos.values():
                entrada.guardado -= segundos


async def una_vuelta(cache: CacheDiagnostico, segundos: int) -> int:
    """Renueva lo pendiente, cada cálculo en un hilo para no parar el servidor."""
    hechos = 0
    for clave, entrada in cache._pendientes(segundos):
        try:
            valor = await asyncio.to_thread(entrada.recalcular)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo renovar un Diagnóstico", exc_info=True)
            continue
        hechos += cache._aplicar(clave, entrada, valor)
    return hechos


async def renovar_siempre(cache: CacheDiagnostico, segundos: int) -> None:
    """El proceso de fondo: una vuelta cada pocos segundos, mientras viva el servidor."""
    while True:
        try:
            await una_vuelta(cache, segundos)
        except Exception:  # noqa: BLE001 - el proceso no se muere por una vuelta mala
            logger.exception("fallo en la renovación del Diagnóstico")
        await asyncio.sleep(VUELTA_S)
