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
from face.queues import Consumer, QueueNames, RabbitMQConsumer, RabbitMQInfrastructure
from face.spiders.runner import run_facebook_enrich_spider, run_google_search_spider

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
    await run_worker_loop(
        role="search",
        queue_name=QueueNames().search_request,
        env_var_name="FACE_SEARCH_JOB_JSON",
        consumer=consumer,
        poll_interval_seconds=poll_interval_seconds,
    )


async def run_enrich_worker_loop(
    consumer: Consumer | None = None,
    *,
    poll_interval_seconds: float = 5.0,
) -> None:
    await run_worker_loop(
        role="enrich",
        queue_name=QueueNames().enrich_request,
        env_var_name="FACE_ENRICH_JOB_JSON",
        consumer=consumer,
        poll_interval_seconds=poll_interval_seconds,
    )


async def run_worker_loop(
    *,
    role: str,
    queue_name: str,
    env_var_name: str,
    consumer: Consumer | None = None,
    poll_interval_seconds: float = 5.0,
) -> None:
    resolved_consumer = consumer or RabbitMQConsumer()
    idle_sleep_seconds = max(poll_interval_seconds, 0.1)

    while running:
        message = await resolved_consumer.get_json_message(
            queue_name,
            timeout_seconds=poll_interval_seconds,
        )
        if message is None:
            logger.info("worker runtime heartbeat", extra={"service": "face-search-spider"})
            await asyncio.sleep(idle_sleep_seconds)
            continue

        id_query = str(message.payload.get("id_query", "unknown"))
        child_env = os.environ.copy()
        child_env[env_var_name] = json.dumps(message.payload)
        try:
            logger.info(
                f"Dispatching {role} spider job from queue",
                extra={"service": f"face-{role}-spider", "id_query": id_query},
            )
            subprocess.run(
                [sys.executable, "-m", "face.worker_runtime", role],
                check=True,
                env=child_env,
            )
            await message.ack()
        except subprocess.CalledProcessError:
            logger.exception(
                f"{role.capitalize()} spider subprocess failed",
                extra={"service": f"face-{role}-spider", "id_query": id_query},
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
        try:
            run_google_search_spider(payload)
        except Exception:
            logger.exception(
                "Search spider one-shot execution failed",
                extra={"service": "face-search-spider"},
            )
            raise
        logger.info(
            "Search spider one-shot execution finished",
            extra={"service": "face-search-spider"},
        )
        return
    if role == "search":
        asyncio.run(RabbitMQInfrastructure().ensure_minimum_queues())
        logger.info("worker runtime started", extra={"service": "face-search-spider"})
        asyncio.run(run_search_worker_loop())
        logger.info("worker runtime stopped", extra={"service": "face-search-spider"})
        return

    if role == "enrich" and os.getenv("FACE_ENRICH_JOB_JSON"):
        payload = json.loads(os.environ["FACE_ENRICH_JOB_JSON"])
        logger.info(
            "Running enrich spider in one-shot mode",
            extra={"service": "face-enrich-spider"},
        )
        try:
            run_facebook_enrich_spider(payload)
        except Exception:
            logger.exception(
                "Enrich spider one-shot execution failed",
                extra={"service": "face-enrich-spider"},
            )
            raise
        logger.info(
            "Enrich spider one-shot execution finished",
            extra={"service": "face-enrich-spider"},
        )
        return
    if role == "enrich":
        asyncio.run(RabbitMQInfrastructure().ensure_minimum_queues())
        logger.info("worker runtime started", extra={"service": "face-enrich-spider"})
        asyncio.run(run_enrich_worker_loop())
        logger.info("worker runtime stopped", extra={"service": "face-enrich-spider"})
        return

    logger.info("worker runtime started", extra={"service": f"face-{role}-spider"})
    while running:
        time.sleep(5)
        logger.info("worker runtime heartbeat", extra={"service": f"face-{role}-spider"})
    logger.info("worker runtime stopped", extra={"service": f"face-{role}-spider"})


if __name__ == "__main__":
    main()
