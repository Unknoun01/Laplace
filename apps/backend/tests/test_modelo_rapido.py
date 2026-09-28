"""Qué modelo se propone como el rápido de tu propio tráfico (D-160).

Un modelo corre en varios pasos, cada uno con su mediana por llamada. Se juntaban con
la media simple de las medianas, así que un paso con cinco llamadas lentas pesaba lo
mismo que otro con cinco mil rápidas. Ahora pesan sus llamadas.
"""

from __future__ import annotations

from laplace_backend.insights.modelo_caro import _modelo_mas_rapido
from laplace_backend.storage.base import ModelUsage


def _uso(paso: str, modelo: str, llamadas: int, ms: float) -> ModelUsage:
    return ModelUsage(key=paso, name=paso, model=modelo, calls=llamadas, p50_duration_ms=ms)


def test_cada_paso_pesa_sus_llamadas():
    usos = [
        # «rapido»: 5.000 llamadas a 200 ms y un paso suelto de 5 llamadas a 3 s.
        _uso("a", "rapido", 5000, 200.0),
        _uso("b", "rapido", 5, 3000.0),
        # «medio»: 1 s en todas partes.
        _uso("c", "medio", 1000, 1000.0),
        _uso("d", "caro", 1000, 4000.0),
    ]
    modelo, ms = _modelo_mas_rapido(usos, excepto="caro")
    # Con la media simple, «rapido» saldría a 1.600 ms y ganaría «medio».
    assert modelo == "rapido"
    assert ms == (5000 * 200 + 5 * 3000) / 5005


def test_a_igualdad_decide_el_nombre_y_no_el_orden():
    usos = [_uso("a", "zeta", 10, 500.0), _uso("b", "alfa", 10, 500.0)]
    assert _modelo_mas_rapido(usos, excepto="x")[0] == "alfa"
    assert _modelo_mas_rapido(list(reversed(usos)), excepto="x")[0] == "alfa"


def test_sin_otro_modelo_no_hay_propuesta():
    assert _modelo_mas_rapido([_uso("a", "caro", 50, 900.0)], excepto="caro") is None
