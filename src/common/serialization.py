from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def json_default(value: Any) -> str | float:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Value of type {type(value)!r} is not JSON serializable")
