from __future__ import annotations

from typing import Any


def extract_facebook_metadata(_: Any, category: str | None = None) -> dict[str, Any]:
    return {"category": category, "metadata_status": "not_implemented"}
