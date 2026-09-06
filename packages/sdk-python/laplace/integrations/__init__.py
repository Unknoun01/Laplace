"""Integraciones con clientes de LLM.

Cada módulo expone `instrument()` / `uninstrument()` y no falla si la librería
correspondiente no está instalada.
"""

from . import anthropic, openai

__all__ = ["anthropic", "openai"]
