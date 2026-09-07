"""Cálculo de coste por span.

El coste se calcula aquí, en la ingesta, nunca en el SDK: los precios cambian y no
pueden quedar congelados en la versión que el usuario tenga instalada (D-005).

Dos reglas que sostienen la credibilidad de todo el producto:

1. **Un modelo sin tarifa no cuesta cero, cuesta "no lo sabemos".** Un 0 silencioso se
   suma a los totales y los corrompe sin que nadie se entere. El span se marca como
   coste desconocido y la interfaz lo dice.
2. **Resolver por prefijo no puede saltar de versión.** `claude-opus-4-5` no puede
   heredar la tarifa de `claude-opus-4` (que es el triple). Sólo se acepta el prefijo
   cuando lo que sobra es un sufijo de snapshot o una palabra, nunca otro número de
   versión.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("laplace.pricing")

_PRICES_PATH = Path(__file__).with_name("model_prices.json")
_MILLION = 1_000_000.0

#: Lo que puede sobrar tras un prefijo para seguir siendo el mismo modelo: un snapshot
#: con fecha (`-20250929`, `-2024-07-18`) o una palabra (`-latest`, `-preview`). Un
#: `-5` o un `.7` son otra versión del modelo y NO heredan la tarifa.
_SNAPSHOT_SUFFIX = re.compile(r"^-(\d{8}|\d{4}-\d{2}-\d{2}|v\d+|[a-z][a-z0-9]*)$")


def _candidates(model: str) -> list[str]:
    """Formas del identificador con las que probar, de la más literal a la más pelada.

    Los gateways adornan el nombre: `openai/gpt-4o`, `anthropic.claude-sonnet-5`,
    `bedrock/anthropic.claude-sonnet-5-v1:0`. Se quitan esos adornos con cuidado de no
    partir un número de versión: en `gpt-5.1` el punto es parte del modelo, no un
    separador de proveedor, así que sólo se corta por `.` si lo que queda detrás no
    empieza por un dígito.
    """
    found = [model]
    if ":" in model:
        found.append(model.split(":", 1)[0])
    for value in list(found):
        if "/" in value:
            found.append(value.rsplit("/", 1)[-1])
    for value in list(found):
        if "." in value:
            tail = value.rsplit(".", 1)[-1]
            if tail and not tail[0].isdigit():
                found.append(tail)
    # Sin duplicados y conservando el orden.
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class ModelPrice:
    """Precio en USD por 1M de tokens."""

    model: str
    input: float
    output: float
    cached_input: float | None = None
    #: Modelo más barato del mismo proveedor que proponer para tareas cortas.
    alternative: str | None = None
    #: Clave del bloque `sources`: de dónde salen estos números.
    source: str = ""


@dataclass(frozen=True)
class Source:
    url: str
    verified_at: str


@dataclass(frozen=True)
class CostBreakdown:
    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    #: True cuando no hay tarifa para ese modelo. El coste NO es cero: es desconocido.
    unknown: bool = True
    #: Tarifa aplicada, para poder auditarla en modo avanzado.
    #: Formato: `<modelo de la tabla> @ <versión de la tabla>`.
    rate: str = ""


class PriceTable:
    """Tabla de precios por modelo, cargada del JSON del repo."""

    def __init__(
        self, models: dict[str, ModelPrice], version: str, sources: dict[str, Source]
    ) -> None:
        self._models = models
        self._version = version
        self._sources = sources
        # Prefijos de más largo a más corto: la coincidencia más específica gana.
        self._prefixes = sorted(models, key=len, reverse=True)

    @classmethod
    def load(cls, path: Path | None = None) -> PriceTable:
        path = path or _PRICES_PATH
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo leer la tabla de precios en %s", path)
            return cls({}, "desconocida", {})

        models = {
            name: ModelPrice(
                model=name,
                input=float(entry.get("input", 0.0)),
                output=float(entry.get("output", 0.0)),
                cached_input=(
                    float(entry["cached_input"]) if entry.get("cached_input") is not None else None
                ),
                alternative=entry.get("alternative") or None,
                source=entry.get("source", ""),
            )
            for name, entry in (raw.get("models") or {}).items()
        }
        sources = {
            key: Source(url=value.get("url", ""), verified_at=value.get("verified_at", ""))
            for key, value in (raw.get("sources") or {}).items()
        }
        version = str(raw.get("version", "desconocida"))
        logger.info("tabla de precios %s cargada: %d modelos", version, len(models))
        return cls(models, version, sources)

    @property
    def version(self) -> str:
        return self._version

    @property
    def models(self) -> dict[str, ModelPrice]:
        return dict(self._models)

    @property
    def sources(self) -> dict[str, Source]:
        return dict(self._sources)

    def lookup(self, model: str | None) -> ModelPrice | None:
        """Coincidencia exacta y, si no, el prefijo más largo que sea seguro.

        Los proveedores versionan los modelos con un snapshot (`gpt-4o-mini-2024-07-18`),
        y ese sí hereda la tarifa del modelo base. Lo que no puede heredarla es otra
        versión del modelo: `claude-opus-4-5` cuesta un tercio que `claude-opus-4`, y
        dejar que el prefijo colara habría cobrado el triple en silencio.
        """
        if not model:
            return None

        for candidate in _candidates(model.strip().lower()):
            direct = self._models.get(candidate)
            if direct is not None:
                return direct
            for prefix in self._prefixes:
                if candidate.startswith(prefix) and _SNAPSHOT_SUFFIX.match(
                    candidate[len(prefix) :]
                ):
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
                logger.info("modelo sin tarifa conocida: %s (coste desconocido)", model)
            return CostBreakdown(unknown=True)

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
            unknown=False,
            rate=f"{price.model} @ {self._version}",
        )


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
