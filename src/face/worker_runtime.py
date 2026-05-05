from __future__ import annotations

import signal
import sys
import time

from common.logging import configure_logging, get_logger

from face.config import get_settings

logger = get_logger(__name__)
running = True


def _handle_signal(_: int, __: object) -> None:
    global running
    running = False


def main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "worker"
    settings = get_settings()
    configure_logging(level=settings.app_log_level, json_logs=settings.app_log_json)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("worker runtime started", extra={"service": f"face-{role}-spider"})
    while running:
        time.sleep(5)
        logger.info("worker runtime heartbeat", extra={"service": f"face-{role}-spider"})
    logger.info("worker runtime stopped", extra={"service": f"face-{role}-spider"})


if __name__ == "__main__":
    main()
