"""Piezas compartidas por las pruebas.

Módulo normal y no `conftest.py` porque los tests necesitan *importar* el exportador y
el ayudante de ingesta, no sólo recibirlos como fixture. Pytest añade este directorio a
`sys.path`, así que se importa por su nombre.
"""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from laplace_backend.ingest.otlp import parse_spans

exporter = InMemorySpanExporter()


def ingest():
    """Lo que el backend recibiría por el endpoint OTLP, ya traducido al contrato."""
    return parse_spans(encode_spans(exporter.get_finished_spans()))


def otlp_body() -> bytes:
    """Lo mismo, pero sin traducir: el sobre protobuf tal cual viaja por la red."""
    return encode_spans(exporter.get_finished_spans()).SerializeToString()


def span_llm():
    """El único span de LLM que ha salido por la ingesta, ya en el contrato.

    Aquí y no en cada fichero de pruebas porque tres tandas distintas lo necesitan
    —transporte falso, servidor local y API real— y la condición de «exactamente uno»
    es parte de lo que se comprueba: dos spans por una llamada es un fallo nuestro.
    """
    spans = [s for s in ingest() if s.type == "llm"]
    assert len(spans) == 1, f"se esperaba un span de LLM y hay {len(spans)}"
    return spans[0]


def hoja_de_estilos() -> str:
    """La hoja de estilos entera de la web, en el orden en que la importa `layout.tsx`.

    Vivía en un solo `globals.css`; desde D-174 está partida en `app/estilos/`. Las
    pruebas que la leen la leen entera, en el orden de la cascada.
    """
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[3] / "apps" / "web" / "app"
    layout = (app / "layout.tsx").read_text(encoding="utf-8")
    ficheros = re.findall(r'^import "\./(estilos/[^"]+\.css)";', layout, flags=re.M)
    assert ficheros, "layout.tsx no importa ninguna hoja de estilos"
    return "\n".join((app / f).read_text(encoding="utf-8") for f in ficheros)
