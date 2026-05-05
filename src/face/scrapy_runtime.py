from __future__ import annotations

import importlib.util
import platform


def resolve_asyncio_event_loop_path() -> str | None:
    if platform.system() == "Windows":
        return None
    if importlib.util.find_spec("uvloop") is None:
        return None
    return "uvloop.Loop"
