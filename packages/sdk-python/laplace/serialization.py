"""Serialización defensiva de payloads.

Un SDK de observabilidad nunca puede romper el programa que observa. Todo lo de aquí
degrada a `repr()` antes que lanzar una excepción.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from typing import Any
from uuid import UUID

_TRUNCATION_NOTE = "...[truncado por laplace: {omitted} bytes omitidos]"


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """Convierte cualquier objeto en algo serializable a JSON, sin lanzar nunca."""
    if _depth > 12:
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"

    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v, _depth + 1) for v in value]

    # Pydantic v2 / v1
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return to_jsonable(dump(), _depth + 1)
        except Exception:  # noqa: BLE001 - jamás romper al usuario
            pass
    legacy = getattr(value, "dict", None)
    if callable(legacy) and not isinstance(value, type):
        try:
            return to_jsonable(legacy(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return to_jsonable(dataclasses.asdict(value), _depth + 1)
        except Exception:  # noqa: BLE001
            pass

    if hasattr(value, "__dict__"):
        try:
            return {
                str(k): to_jsonable(v, _depth + 1)
                for k, v in vars(value).items()
                if not str(k).startswith("_")
            }
        except Exception:  # noqa: BLE001
            pass

    try:
        return repr(value)
    except Exception:  # noqa: BLE001
        return "<unserializable>"


def dumps(value: Any, max_bytes: int | None = None) -> str:
    """Serializa a JSON, truncando con marca explícita si excede `max_bytes`."""
    try:
        text = json.dumps(to_jsonable(value), ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = json.dumps({"_unserializable": repr(value)[:2000]}, ensure_ascii=False)
    return truncate(text, max_bytes)


def truncate(text: str, max_bytes: int | None) -> str:
    """Recorta a `max_bytes` dejando constancia de cuánto se omitió."""
    if max_bytes is None:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    omitted = len(encoded) - max_bytes
    head = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return head + _TRUNCATION_NOTE.format(omitted=omitted)
