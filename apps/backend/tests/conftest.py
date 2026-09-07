"""Configuración común de las pruebas.

El proveedor de OpenTelemetry sólo se puede fijar una vez por proceso, así que se
monta aquí y no en cada módulo.
"""

from __future__ import annotations

import pytest
from helpers import exporter
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


@pytest.fixture(scope="session", autouse=True)
def _provider() -> None:
    provider = TracerProvider(resource=Resource.create({"laplace.project.id": "test-project"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)


@pytest.fixture(autouse=True)
def _clear() -> None:
    exporter.clear()
