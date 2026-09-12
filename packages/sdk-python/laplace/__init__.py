"""Laplace — observabilidad y optimización de agentes de IA.

    import laplace
    laplace.init(project="mi-agente")

A partir de esa línea, cada llamada a OpenAI o Anthropic queda registrada. Para que
los pasos propios del agente aparezcan en el árbol, decóralos:

    @laplace.observe(type="agent")
    def responder(pregunta): ...

Y si los prompts los gestiona Laplace, `laplace.get_prompt("nombre")` los sirve y deja
escrito en cada traza con qué versión se ejecutó.
"""

from ._tracer import flush, get_config, init, is_enabled, shutdown
from .decorators import (
    get_current_trace_id,
    observe,
    set_context,
    span,
    update_current_span,
)
from .evals import Case, RunResult, fetch_dataset, run_dataset
from .manual import llm_span
from .prompts import PromptError, ServedPrompt, get_prompt
from .version import __version__

__all__ = [
    "Case",
    "PromptError",
    "RunResult",
    "ServedPrompt",
    "__version__",
    "fetch_dataset",
    "flush",
    "get_config",
    "get_current_trace_id",
    "get_prompt",
    "init",
    "is_enabled",
    "llm_span",
    "observe",
    "run_dataset",
    "set_context",
    "shutdown",
    "span",
    "update_current_span",
]
