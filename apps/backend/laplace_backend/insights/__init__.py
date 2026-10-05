"""Motor de detección de derroche (Fase 2, Norte B).

Reglas **deterministas**: nada de modelos, nada de heurísticas opacas. Cada hallazgo
sale de una consulta que el usuario puede ver, con una cifra que sale de sumar coste
realmente guardado por span.

Tres principios que condicionan todo lo de aquí:

1. **Nunca inventar dinero.** Si un bucle de herramientas no consume tokens, el ahorro
   es cero y se dice; lo que se ha perdido es tiempo, y eso se enseña en su lugar.
2. **Nunca prometer calidad.** Se puede afirmar cuánto costaría un paso con otro modelo,
   porque es aritmética sobre tokens reales. No se puede afirmar que acertaría igual:
   eso exige evaluaciones (Fase 4) y hoy no las hay.
3. **Toda cifra estimada lleva su cálculo detrás**, visible en modo avanzado.

El motor está partido por reglas (D-130): `modelos` (tipos, umbrales y redacción),
una por regla (`repeticion`, `bucle`, `modelo_caro`, `contexto_fijo`, `prompt_caro`) y
`motor`, que las
junta. Aquí se reexporta todo, así que `from .insights import X` sigue valiendo.
"""

from __future__ import annotations

from ..pricing import get_price_table  # noqa: F401
from .bucle import (  # noqa: F401
    _loop_detail,
    _loop_finding,
)
from .contexto_fijo import (  # noqa: F401
    MIN_PARTE_DEL_GASTO_EN_LECTURAS,
    MIN_PARTE_SIN_CACHEAR,
    _cache_arithmetic,
    _cheaper_model_if_recommended,
    _coste_de_escribir,
    _coste_de_leer_cache,
    _fixed_context_detail,
    _fixed_context_finding,
)
from .modelo_caro import (  # noqa: F401
    MAX_SALIDA_TRIVIAL_SIN_TARIFA,
    MIN_CALLS_MODELO_RAPIDO,
    MIN_VECES_MAS_LENTO,
    MotivoSinDinero,
    _expensive_model_detail,
    _expensive_model_finding,
    _modelo_caro_sin_tarifa,
    _modelo_lento_detail,
    _modelo_mas_rapido,
    _salida_tipica,
)
from .modelos import (  # noqa: F401
    _MILLION,
    CAUTION_SAVINGS_RATIO,
    DAYS_PER_MONTH,
    MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK,
    MAX_SALIDAS_BUCLE,
    MIN_CALLS_FOR_CONTEXT_RULE,
    MIN_CALLS_FOR_MODEL_RULE,
    MIN_DAYS_FOR_PROJECTION,
    MIN_FIXED_INPUT_TOKENS,
    MIN_REPEATS,
    MIN_VUELTAS_BUCLE,
    Difficulty,
    Finding,
    FindingDetail,
    FindingKind,
    FixCheck,
    FixStep,
    Overview,
    TechItem,
    _decimal,
    _floor_flags,
    _miles,
    _money,
    _projection_base,
    _projection_sentence,
    _scope_label,
    _seconds,
    _to_monthly,
    logger,
    observed_days,
    reparto,
    span_label,
    window_label,
)
from .motor import (  # noqa: F401
    _DETALLADORES,
    DETAILED_KINDS,
    _Contexto,
    _detalle_bucle,
    _detalle_modelo,
    _detalle_repeticion,
    _duplicate_tokens,
    _sin_envoltorios,
    _without_duplicates,
    detail,
    detect,
    overview,
)
from .prompt_caro import (  # noqa: F401
    _prompt_detail,
    _prompt_finding,
)
from .repeticion import (  # noqa: F401
    _repetition_detail,
    _repetition_finding,
)
from .salida_truncada import MIN_TRUNCADAS_REHECHAS  # noqa: F401

__all__ = [
    "CAUTION_SAVINGS_RATIO",
    "DAYS_PER_MONTH",
    "DETAILED_KINDS",
    "Difficulty",
    "Finding",
    "FindingDetail",
    "FindingKind",
    "FixCheck",
    "FixStep",
    "MAX_OUTPUT_TOKENS_FOR_CHEAP_TASK",
    "MAX_SALIDAS_BUCLE",
    "MAX_SALIDA_TRIVIAL_SIN_TARIFA",
    "MIN_CALLS_FOR_CONTEXT_RULE",
    "MIN_CALLS_FOR_MODEL_RULE",
    "MIN_CALLS_MODELO_RAPIDO",
    "MIN_DAYS_FOR_PROJECTION",
    "MIN_FIXED_INPUT_TOKENS",
    "MIN_PARTE_DEL_GASTO_EN_LECTURAS",
    "MIN_PARTE_SIN_CACHEAR",
    "MIN_REPEATS",
    "MIN_TRUNCADAS_REHECHAS",
    "MIN_VECES_MAS_LENTO",
    "MIN_VUELTAS_BUCLE",
    "MotivoSinDinero",
    "Overview",
    "TechItem",
    "detail",
    "detect",
    "logger",
    "observed_days",
    "overview",
    "span_label",
    "window_label",
]
