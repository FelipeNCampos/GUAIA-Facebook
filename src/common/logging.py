from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import Any

_log_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "log_context",
    default={},
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("service", "id_query", "correlation_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        payload.update(_log_context.get())
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_log_context(**kwargs: Any) -> None:
    current = dict(_log_context.get())
    current.update({key: value for key, value in kwargs.items() if value is not None})
    _log_context.set(current)


def clear_log_context() -> None:
    _log_context.set({})
