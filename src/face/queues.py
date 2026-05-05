from __future__ import annotations

from dataclasses import dataclass

import aio_pika
from common.logging import get_logger

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
