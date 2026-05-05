from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

T = TypeVar("T")

logger = logging.getLogger(__name__)


def connect_with_retry(
    connect_fn: Callable[[], T],
    *,
    attempts: int = 10,
    delay_seconds: float = 3.0,
) -> T:
    last_error: OperationalError | OSError | None = None

    for attempt in range(1, attempts + 1):
        try:
            return connect_fn()
        except (OperationalError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            logger.warning(
                "Database connection failed during migration startup; retrying",
                extra={"attempt": attempt, "max_attempts": attempts},
            )
            time.sleep(delay_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("connect_with_retry exhausted without returning a connection or exception")
