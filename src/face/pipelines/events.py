from __future__ import annotations

import asyncio

from common.logging import get_logger

from face.queues import QueueNames, RabbitMQPublisher
from face.repository import FaceJobRepository, create_session_factory

logger = get_logger(__name__)


class EventsPipeline:
    def __init__(self) -> None:
        self.job_repository: FaceJobRepository | None = None
        self.publisher = None
        self.queue_names = QueueNames()
        self.discovered_count = 0

    @classmethod
    def from_crawler(cls, crawler):  # type: ignore[no-untyped-def]
        return cls()

    def open_spider(self, spider):  # type: ignore[no-untyped-def]
        self.job_repository = getattr(spider, "job_repository", None) or FaceJobRepository(
            create_session_factory()
        )
        self.publisher = getattr(spider, "publisher", None) or RabbitMQPublisher()
        self.discovered_count = 0

        if (
            getattr(spider, "name", None) == "google_search"
            and getattr(spider, "id_query", None)
            and self.job_repository is not None
        ):
            self.job_repository.update_job_status(
                id_query=spider.id_query,
                status_current="search_in_progress",
            )
            self.job_repository.add_event(
                id_query=spider.id_query,
                event_type="search.started",
                payload={
                    "subject": getattr(spider, "subject", None),
                },
            )

    def process_item(self, item, spider):  # type: ignore[no-untyped-def]
        if item.get("item_type") != "facebook_url":
            return item

        if self.job_repository is None or self.publisher is None:
            raise RuntimeError("EventsPipeline not initialized")

        self.discovered_count += 1
        payload = {
            "id_query": item["id_query"],
            "facebook_url": item["url_normalized"],
            "category": item["category"],
            "query_source": item.get("query_source", "api"),
            "record_id": item.get("record_id"),
        }
        self.job_repository.add_event(
            id_query=item["id_query"],
            event_type="search.url_discovered",
            payload={
                **payload,
                "url": item["url"],
                "url_normalized": item["url_normalized"],
            },
        )
        spider.crawler.stats.inc_value("face/search_url_discovered_count", 1)
        spider.crawler.stats.set_value("face/search_discovered_total", self.discovered_count)
        self._publish_discovered_url(payload)
        return item

    def close_spider(self, spider):  # type: ignore[no-untyped-def]
        if (
            getattr(spider, "name", None) == "google_search"
            and getattr(spider, "id_query", None)
            and self.job_repository is not None
        ):
            blocked_details = getattr(spider, "search_blocked_details", None)
            if blocked_details is not None:
                status = "search_blocked"
                event_type = "search.blocked"
                event_payload = {
                    **blocked_details,
                    "discovered_count": self.discovered_count,
                    "pages_visited": getattr(spider, "page_count", 0),
                }
            else:
                status = (
                    "search_completed"
                    if self.discovered_count > 0
                    else "search_completed_empty"
                )
                event_type = "search.completed"
                event_payload = {
                    "discovered_count": self.discovered_count,
                    "pages_visited": getattr(spider, "page_count", 0),
                }
            self.job_repository.update_job_status(
                id_query=spider.id_query,
                status_current=status,
            )
            self.job_repository.add_event(
                id_query=spider.id_query,
                event_type=event_type,
                payload=event_payload,
            )
            if blocked_details is not None:
                logger.warning(
                    "Search spider blocked by Google",
                    extra={"service": "face-search-spider", "id_query": spider.id_query},
                )
            else:
                logger.info(
                    "Search spider completed",
                    extra={"service": "face-search-spider", "id_query": spider.id_query},
                )

    def _publish_discovered_url(self, payload: dict[str, object]) -> None:
        if self.publisher is None:
            raise RuntimeError("Publisher not initialized")

        coroutine = self.publisher.publish_json(self.queue_names.enrich_request, payload)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coroutine)
            return

        task = loop.create_task(coroutine)
        task.add_done_callback(self._log_publish_error)

    @staticmethod
    def _log_publish_error(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("Failed to publish discovered URL to queue")
