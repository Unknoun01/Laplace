"""Cálculo de coste por span.

El coste se calcula aquí, en la ingesta, nunca en el SDK: los precios cambian y no
pueden quedar congelados en la versión que el usuario tenga instalada (D-005).

Se guarda **desglosado** (entrada / salida) porque el panel de ahorro necesita saber
qué parte del gasto es prompt y qué parte es generación para poder decir "este paso
saldría 10x más barato con otro modelo".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("laplace.pricing")

_PRICES_PATH = Path(__file__).with_name("model_prices.json")
_MILLION = 1_000_000.0


@dataclass(frozen=True)
class ModelPrice:
    """Precio en USD por 1M de tokens."""

    model: str
    input: float
    output: float
    cached_input: float | None = None


@dataclass(frozen=True)
class CostBreakdown:
    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    #: True cuando el modelo no está en la tabla. Nunca se presenta como exacto.
    estimated: bool = True


class PriceTable:
    """Tabla de precios por modelo, cargada del JSON del repo."""

    def __init__(self, models: dict[str, ModelPrice]) -> None:
        self._models = models
        # Prefijos ordenados de más largo a más corto: la primera coincidencia gana.
        self._prefixes = sorted(models, key=len, reverse=True)

    @classmethod
    def load(cls, path: Path | None = None) -> PriceTable:
        path = path or _PRICES_PATH
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo leer la tabla de precios en %s", path)
            return cls({})
        models = {
            name: ModelPrice(
                model=name,
                input=float(entry.get("input", 0.0)),
                output=float(entry.get("output", 0.0)),
                cached_input=(
                    float(entry["cached_input"]) if entry.get("cached_input") is not None else None
                ),
            )
            for name, entry in (raw.get("models") or {}).items()
        }
        logger.info("tabla de precios cargada: %d modelos", len(models))
        return cls(models)

    def lookup(self, model: str | None) -> ModelPrice | None:
        """Coincidencia exacta y, si no, prefijo más largo.

        Los proveedores versionan los modelos (`gpt-4o-mini-2024-07-18`); resolver por
        prefijo evita tener que añadir cada snapshot a mano.
        """
        if not model:
            return None
        normalized = model.strip().lower()
        direct = self._models.get(normalized)
        if direct is not None:
            return direct
        # Prefijos de proveedor tipo "openai/gpt-4o" o "anthropic.claude-3-5-sonnet".
        for sep in ("/", ":"):
            if sep in normalized:
                normalized = normalized.rsplit(sep, 1)[-1]
        for prefix in self._prefixes:
            if normalized.startswith(prefix):
                return self._models[prefix]
        return None

    def compute(
        self,
        model: str | None,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> CostBreakdown:
        price = self.lookup(model)
        if price is None:
            if model:
                logger.debug("modelo sin precio conocido: %s", model)
            return CostBreakdown(estimated=True)

        # Los tokens cacheados vienen incluidos en input_tokens: se descuentan del
        # precio normal y se cobran a su tarifa reducida.
        cached = max(0, min(cached_input_tokens, input_tokens))
        uncached = input_tokens - cached
        cached_rate = price.cached_input if price.cached_input is not None else price.input

        input_usd = (uncached * price.input + cached * cached_rate) / _MILLION
        output_usd = (output_tokens * price.output) / _MILLION
        return CostBreakdown(
            input_usd=input_usd,
            output_usd=output_usd,
            total_usd=input_usd + output_usd,
            estimated=False,
        )

    @property
    def models(self) -> dict[str, ModelPrice]:
        return dict(self._models)


_table: PriceTable | None = None


def get_price_table() -> PriceTable:
    global _table
    if _table is None:
        _table = PriceTable.load()
    return _table


def reload_price_table() -> PriceTable:
    """Recarga la tabla sin reiniciar el proceso."""
    global _table
    _table = PriceTable.load()
    return _table
