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
