"""Laplace — observabilidad y optimización de agentes de IA.

    import laplace
    laplace.init(project="mi-agente")

A partir de esa línea, cada llamada a OpenAI o Anthropic queda registrada. Para que
los pasos propios del agente aparezcan en el árbol, decóralos:

    @laplace.observe(type="agent")
    def responder(pregunta): ...
"""

from ._tracer import flush, get_config, init, is_enabled, shutdown
from .decorators import (
    get_current_trace_id,
    observe,
    set_context,
    span,
    update_current_span,
)
from .manual import llm_span
from .version import __version__

__all__ = [
    "__version__",
    "flush",
    "get_config",
    "get_current_trace_id",
    "init",
    "is_enabled",
    "llm_span",
    "observe",
    "set_context",
    "shutdown",
    "span",
    "update_current_span",
]
