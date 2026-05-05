from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

from common.logging import configure_logging, get_logger

from face.config import get_settings
from face.queues import Consumer, QueueNames, RabbitMQConsumer
from face.spiders.runner import run_google_search_spider

logger = get_logger(__name__)
running = True


def _handle_signal(_: int, __: object) -> None:
    global running
    running = False


async def run_search_worker_loop(
    consumer: Consumer | None = None,
    *,
    poll_interval_seconds: float = 5.0,
) -> None:
    resolved_consumer = consumer or RabbitMQConsumer()
    queue_name = QueueNames().search_request

    while running:
        message = await resolved_consumer.get_json_message(
            queue_name,
            timeout_seconds=poll_interval_seconds,
        )
        if message is None:
            logger.info("worker runtime heartbeat", extra={"service": "face-search-spider"})
            continue

        id_query = str(message.payload.get("id_query", "unknown"))
        child_env = os.environ.copy()
        child_env["FACE_SEARCH_JOB_JSON"] = json.dumps(message.payload)
        try:
            logger.info(
                "Dispatching search spider job from queue",
                extra={"service": "face-search-spider", "id_query": id_query},
            )
            subprocess.run(
                [sys.executable, "-m", "face.worker_runtime", "search"],
                check=True,
                env=child_env,
            )
            await message.ack()
        except subprocess.CalledProcessError:
            logger.exception(
                "Search spider subprocess failed",
                extra={"service": "face-search-spider", "id_query": id_query},
            )
            await message.reject(requeue=True)


def main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "worker"
    settings = get_settings()
    configure_logging(level=settings.app_log_level, json_logs=settings.app_log_json)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if role == "search" and os.getenv("FACE_SEARCH_JOB_JSON"):
        payload = json.loads(os.environ["FACE_SEARCH_JOB_JSON"])
        logger.info(
            "Running search spider in one-shot mode",
            extra={"service": "face-search-spider"},
        )
        run_google_search_spider(payload)
        logger.info(
            "Search spider one-shot execution finished",
            extra={"service": "face-search-spider"},
        )
        return
    if role == "search":
        logger.info("worker runtime started", extra={"service": "face-search-spider"})
        asyncio.run(run_search_worker_loop())
        logger.info("worker runtime stopped", extra={"service": "face-search-spider"})
        return

    logger.info("worker runtime started", extra={"service": f"face-{role}-spider"})
    while running:
        time.sleep(5)
        logger.info("worker runtime heartbeat", extra={"service": f"face-{role}-spider"})
    logger.info("worker runtime stopped", extra={"service": f"face-{role}-spider"})


if __name__ == "__main__":
    main()
