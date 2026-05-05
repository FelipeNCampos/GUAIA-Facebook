from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import aio_pika
from common.logging import get_logger
from common.serialization import json_default

from face.config import Settings, get_settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class QueueNames:
    search_request: str = "face.search.request"
    search_cache_lookup: str = "face.search.cache_lookup"
    url_discovered: str = "face.url.discovered"
    enrich_request: str = "face.enrich.request"
    enrich_cache_lookup: str = "face.enrich.cache_lookup"
    record_persisted: str = "face.record.persisted"
    export_request: str = "face.export.request"
    job_events: str = "face.job.events"
    dead_letter: str = "face.dead_letter"


class RabbitMQConnection:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def connect(self) -> aio_pika.RobustConnection:
        if not self.settings.rabbitmq_url:
            raise ValueError("RABBITMQ_URL must be configured via environment variable")
        logger.info("Connecting to RabbitMQ")
        return await aio_pika.connect_robust(self.settings.rabbitmq_url)


class Publisher(Protocol):
    async def publish_json(
        self,
        queue_name: str,
        payload: dict[str, object],
    ) -> None:
        """Publish a JSON payload to a queue."""


@dataclass
class ConsumedMessage:
    payload: dict[str, Any]
    ack: Callable[[], Awaitable[None]]
    reject: Callable[[bool], Awaitable[None]]


class Consumer(Protocol):
    async def get_json_message(
        self,
        queue_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ConsumedMessage | None:
        """Fetch one JSON message from a queue."""


class RabbitMQPublisher:
    def __init__(self, connection: RabbitMQConnection | None = None) -> None:
        self.connection = connection or RabbitMQConnection()

    async def publish_json(
        self,
        queue_name: str,
        payload: dict[str, object],
    ) -> None:
        connection = await self.connection.connect()
        channel = await connection.channel()
        await channel.declare_queue(queue_name, durable=True)
        message = aio_pika.Message(
            body=json.dumps(payload, default=json_default).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            await channel.default_exchange.publish(message, routing_key=queue_name)
        finally:
            await channel.close()
            await connection.close()


class RabbitMQConsumer:
    def __init__(self, connection: RabbitMQConnection | None = None) -> None:
        self.connection = connection or RabbitMQConnection()

    async def get_json_message(
        self,
        queue_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ConsumedMessage | None:
        connection = await self.connection.connect()
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, durable=True)
        try:
            message = await queue.get(timeout=timeout_seconds, fail=False)
            if message is None:
                return None

            payload = json.loads(message.body.decode("utf-8"))

            async def ack() -> None:
                await message.ack()
                await channel.close()
                await connection.close()

            async def reject(requeue: bool) -> None:
                await message.reject(requeue=requeue)
                await channel.close()
                await connection.close()

            return ConsumedMessage(payload=payload, ack=ack, reject=reject)
        except Exception:
            await channel.close()
            await connection.close()
            raise
