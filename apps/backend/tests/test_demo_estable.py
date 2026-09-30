"""La demo dice lo mismo la cargues a la hora que la cargues.

El problema de la versión del prompt salía entre un 53 % y un 70 % más caro según la
hora de la carga: el cambio de versión, el pico y el arreglo caían a «tantas horas desde
ahora», y cada versión se quedaba con una mezcla distinta de horas del día. Una demo que
cambia de cifra entre dos cargas desconcierta justo cuando se enseña.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from helpers import exporter, ingest
from laplace import demo

from laplace_backend import insights
from laplace_backend.storage.base import Window
from laplace_backend.storage.sqlite import SQLiteStore


@pytest.fixture
def sin_red(monkeypatch):
    monkeypatch.setattr(demo, "init", lambda **_: None)
    monkeypatch.setattr(demo, "flush", lambda: None)
    monkeypatch.setattr(demo, "set_context", lambda **_: None)


def _prompt_caro(tmp_path, hora: int) -> str:
    exporter.clear()
    fin = datetime(2026, 9, 26, hora, 30, tzinfo=timezone.utc)
    demo.generar_mes("http://nadie", project="demo", ahora=fin)
    store = SQLiteStore(tmp_path / f"d{hora}.db")
    store.migrate()
    spans = ingest()
    for span in spans:
        span.project_id = "demo"
    store.insert_spans(spans)
    ventana = Window(since=fin - timedelta(days=30), until=fin, days=30)
    (hallazgo,) = [f for f in insights.detect(store, "demo", ventana) if f.kind == "prompt_caro"]
    return hallazgo.title


def test_el_problema_del_prompt_dice_lo_mismo_a_cualquier_hora(sin_red, tmp_path):
    titulos = {hora: _prompt_caro(tmp_path, hora) for hora in (1, 9, 17, 23)}
    assert len(set(titulos.values())) == 1, titulos
    # Y lo que enseña sigue siendo un problema de verdad, no un 1 % que no importa.
    assert "más por ejecución" in titulos[9], titulos
