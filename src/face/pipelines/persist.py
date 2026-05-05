from __future__ import annotations

from face.repository import FaceRecordRepository, create_session_factory


class PersistPipeline:
    def __init__(self) -> None:
        self.repository: FaceRecordRepository | None = None

    @classmethod
    def from_crawler(cls, crawler):  # type: ignore[no-untyped-def]
        return cls()

    def open_spider(self, spider):  # type: ignore[no-untyped-def]
        self.repository = getattr(spider, "record_repository", None) or FaceRecordRepository(
            create_session_factory()
        )

    def process_item(self, item, spider):  # type: ignore[no-untyped-def]
        if self.repository is None:
            raise RuntimeError("PersistPipeline repository not initialized")

        if item.get("item_type") == "facebook_url":
            payload = {
                "search_page": item["search_page"],
                "search_position": item["search_position"],
                "source_query": item["source_query"],
                "discovered_via": item["discovered_via"],
            }
            record = self.repository.upsert_discovered_record(
                id_query=item["id_query"],
                url=item["url"],
                url_normalized=item["url_normalized"],
                category=item["category"],
                payload=payload,
            )
            item["record_id"] = record.id
            return item

        if item.get("item_type") == "facebook_record":
            record = self.repository.upsert_enriched_record(
                id_query=item["id_query"],
                url=item["url"],
                url_normalized=item["url_normalized"],
                category=item["category"],
                payload=item.get("payload"),
                record_id=item.get("record_id"),
                status=item.get("status", "enriched"),
                last_error=item.get("last_error"),
            )
            item["record_id"] = record.id
        return item
