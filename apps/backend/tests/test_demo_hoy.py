"""La demo llega hasta ahora, no hasta anoche (D-146).

El mes de la demo se generaba de hace treinta días a ayer a medianoche: el día de hoy no
existía. El Diagnóstico aparta como «ya no ocurre» lo que no se ha visto en el último día
(D-135), así que cargada por la mañana todo seguía pendiente y cargada por la tarde los
siete problemas salían resueltos. Era la prueba de pantallas que fallaba «a veces»: fallaba
según la hora.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from laplace import demo


@pytest.fixture
def sin_red(monkeypatch):
    """El bucle de verdad, sin mandar nada: cada ejecución devuelve un id inventado."""
    ids = (f"{n:032x}" for n in itertools.count(1))
    monkeypatch.setattr(demo, "init", lambda **_: None)
    monkeypatch.setattr(demo, "flush", lambda: None)
    monkeypatch.setattr(demo, "set_context", lambda **_: None)
    monkeypatch.setattr(demo, "_una_ejecucion", lambda pregunta, esc: next(ids))


@pytest.mark.parametrize("hora", [0, 9, 16, 23])
def test_hay_trafico_de_las_ultimas_horas_a_cualquier_hora(sin_red, hora):
    fin = datetime(2026, 9, 26, hora, 30, tzinfo=timezone.utc)
    resultado = demo.generar_mes("http://nadie", ahora=fin)
    ultima = max(t.cuando for t in resultado.trazas)
    assert fin - ultima < timedelta(hours=3), f"lo último es de {ultima:%d %H:%M}"
    assert all(t.cuando < fin for t in resultado.trazas), "nada llega del futuro"
