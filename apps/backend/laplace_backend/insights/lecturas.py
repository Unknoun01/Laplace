"""Las lecturas del Diagnóstico, recordadas mientras dura una petición (D-142).

El Diagnóstico pedía el resumen de la ventana dos veces con los mismos argumentos, y
con diez millones de spans al día cada vez es más de medio segundo. Aquí cada lectura
se hace una vez y se sirve desde su resultado tantas veces como se pida.

Se probó también lanzar las cinco lecturas a la vez, y con ClickHouse no gana nada:
cada consulta ya usa todos los núcleos, y en paralelo sólo compiten entre sí (5,1 s
frente a 5,0 en serie sobre la prueba de carga). No se hace.
"""

from __future__ import annotations

from typing import Any


def _clave(nombre: str, args: tuple, kwargs: dict) -> str:
    # `repr` y no el hash: `Window` es un dataclass sin congelar y no sirve de clave.
    # Dos ventanas iguales dan el mismo `repr`, que es lo que hace falta.
    return f"{nombre}|{args!r}|{sorted(kwargs.items())!r}"


class Recordado:
    """El almacén, con cada lectura hecha una sola vez. El motor no cambia: pide como
    siempre y recibe lo mismo, errores incluidos."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._resultados: dict[str, tuple[bool, Any]] = {}

    def __getattr__(self, nombre: str) -> Any:
        valor = getattr(self._store, nombre)
        if not callable(valor):
            return valor

        def leer(*args: Any, **kwargs: Any) -> Any:
            clave = _clave(nombre, args, kwargs)
            if clave not in self._resultados:
                try:
                    self._resultados[clave] = (True, valor(*args, **kwargs))
                except Exception as exc:  # noqa: BLE001 - se relanza al que pregunta
                    self._resultados[clave] = (False, exc)
            bien, resultado = self._resultados[clave]
            if not bien:
                raise resultado
            return resultado

        return leer
