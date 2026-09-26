"""El Diagnóstico, recordado un minuto en la nube (D-142).

Con diez millones de spans al día, el Diagnóstico de un proyecto grande tarda segundos,
y se pide al abrir el inicio, la lista de trazas y cada traza. Durante un minuto se sirve
el mismo. Lo que llega por la ingesta en ese minuto no se ve hasta el siguiente, que es
el precio; lo que cambia por la API —marcar un hallazgo, una tarifa, la demo— borra la
caché en el momento, porque quien lo hace espera verlo ya.

En local no hay caché: el volumen no la necesita, y quien acaba de mandar trazas desde
su portátil espera verlas.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

#: Diagnósticos guardados como mucho. Cada uno es un proyecto y una ventana.
MAXIMO = 256


class CacheDiagnostico:
    def __init__(self) -> None:
        self._datos: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._cerrojo = threading.Lock()

    def leer(self, clave: tuple, segundos: int) -> Any | None:
        if segundos <= 0:
            return None
        with self._cerrojo:
            guardado = self._datos.get(clave)
            if guardado is None or time.monotonic() - guardado[0] > segundos:
                return None
            return guardado[1]

    def guardar(self, clave: tuple, valor: Any, segundos: int) -> None:
        if segundos <= 0:
            return
        with self._cerrojo:
            self._datos[clave] = (time.monotonic(), valor)
            self._datos.move_to_end(clave)
            while len(self._datos) > MAXIMO:
                self._datos.popitem(last=False)

    def olvidar(self) -> None:
        with self._cerrojo:
            self._datos.clear()
