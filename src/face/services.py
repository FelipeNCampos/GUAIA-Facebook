from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from common.logging import get_logger, set_log_context
from db.models import FaceExport, FaceRecord
from sqlalchemy.exc import IntegrityError

from face.models import (
    CreateExportRequest,
    CreateQueriesRequest,
    ExportAcceptedResponse,
    QueryCreateInput,
    QueryExportsResponse,
    QueryRecordsResponse,
    QueryStatusResponse,
    RetryAcceptedResponse,
    RetryQueryStageRequest,
)
from face.queues import Publisher, QueueNames
from face.repository import FaceJobRepository

logger = get_logger(__name__)


class QueryConflictError(Exception):
    """Raised when a query cannot be created due to a business conflict."""


class QueryRetryError(Exception):
    """Raised when a query stage cannot be retried with the provided payload."""


@dataclass(frozen=True)
class ResolvedQuery:
    id_query: str
    query: QueryCreateInput


@dataclass
class QueryService:
    repository: FaceJobRepository
    publisher: Publisher
    queue_names: QueueNames

    async def create_queries(self, request: CreateQueriesRequest) -> list[dict[str, object]]:
        accepted_queries: list[dict[str, object]] = []
        resolved_queries = self._resolve_queries(request)
        self._validate_batch_conflicts(resolved_queries)

        for resolved in resolved_queries:
            id_query = resolved.id_query
            query = resolved.query
            set_log_context(id_query=id_query)

            try:
                job = self.repository.create_job(
                    id_query=id_query,
                    subject=query.subject,
                    query_source=query.query_source,
                    start_date=query.start_date,
                    end_date=query.end_date,
                )
            except IntegrityError as exc:
                raise QueryConflictError(f"Query '{id_query}' already exists") from exc

            self.repository.add_event(
                id_query=id_query,
                event_type="query.created",
                payload={
                    "subject": query.subject,
                    "query_source": query.query_source,
                    "start_date": query.start_date.isoformat() if query.start_date else None,
                    "end_date": query.end_date.isoformat() if query.end_date else None,
                },
            )

            try:
                search_payload = self._build_search_payload_from_query(id_query, query)
                await self._publish_stage_request(
                    id_query=id_query,
                    stage="search",
                    queue_name=self.queue_names.search_request,
                    payload=search_payload,
                    status_current="search_requested",
                    event_type="search.requested",
                )
            except Exception as exc:
                self.repository.update_job_status(
                    id_query=id_query,
                    status_current="enqueue_failed",
                )
                self.repository.add_event(
                    id_query=id_query,
                    event_type="search.enqueue_failed",
                    payload={
                        "error": str(exc),
                    },
                )
                logger.exception(
                    "Failed to publish query for search",
                    extra={"service": "face-api", "id_query": id_query},
                )
                raise

            job = self.repository.get_job(id_query)

            logger.info(
                "Query accepted and published for search",
                extra={"service": "face-api", "id_query": id_query},
            )
            accepted_queries.append(
                {
                    "id_query": job.id_query,
                    "status_current": job.status_current,
                    "created_at": job.created_at,
                }
            )

        return accepted_queries

    def get_query_status(self, id_query: str) -> QueryStatusResponse:
        set_log_context(id_query=id_query)
        job, events = self.repository.get_job_with_events(id_query)
        return QueryStatusResponse(
            id_query=job.id_query,
            subject=job.subject,
            query_source=job.query_source,
            start_date=job.start_date,
            end_date=job.end_date,
            status_current=job.status_current,
            created_at=job.created_at,
            updated_at=job.updated_at,
            recent_events=[
                {
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at,
                }
                for event in events
            ],
        )

    def get_query_records(self, id_query: str) -> QueryRecordsResponse:
        set_log_context(id_query=id_query)
        records = self.repository.list_records(id_query)
        return QueryRecordsResponse(
            id_query=id_query,
            records=[
                {
                    "id": record.id,
                    "id_query": record.id_query,
                    "url": record.url,
                    "url_normalized": record.url_normalized,
                    "category": record.category,
                    "status": record.status,
                    "payload": record.payload,
                    "last_error": record.last_error,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                }
                for record in records
            ],
        )

    def get_query_exports(self, id_query: str) -> QueryExportsResponse:
        set_log_context(id_query=id_query)
        exports = self.repository.list_exports(id_query)
        return QueryExportsResponse(
            id_query=id_query,
            exports=[
                {
                    "id": export.id,
                    "id_query": export.id_query,
                    "export_format": export.export_format,
                    "storage_path": export.storage_path,
                    "status": export.status,
                    "created_at": export.created_at,
                    "completed_at": export.completed_at,
                }
                for export in exports
            ],
        )

    async def create_export_request(
        self,
        id_query: str,
        request: CreateExportRequest,
    ) -> ExportAcceptedResponse:
        set_log_context(id_query=id_query)
        export = self.repository.create_export_request(
            id_query=id_query,
            export_format=request.export_format,
        )
        export_payload = {
            "export_id": export.id,
            "id_query": id_query,
            "export_format": request.export_format,
        }

        try:
            await self.publisher.publish_json(self.queue_names.export_request, export_payload)
            await self.publisher.publish_json(
                self.queue_names.job_events,
                {
                    "id_query": id_query,
                    "event_type": "export.requested",
                    "payload": export_payload,
                },
            )
        except Exception as exc:
            self.repository.update_export_status(export_id=export.id, status="enqueue_failed")
            self.repository.add_event(
                id_query=id_query,
                event_type="export.enqueue_failed",
                payload={
                    "export_id": export.id,
                    "export_format": request.export_format,
                    "error": str(exc),
                },
            )
            logger.exception(
                "Failed to publish export request",
                extra={"service": "face-api", "id_query": id_query},
            )
            raise

        export = self.repository.update_export_status(export_id=export.id, status="requested")
        self.repository.add_event(
            id_query=id_query,
            event_type="export.requested",
            payload=export_payload,
        )
        logger.info(
            "Export request accepted and published",
            extra={"service": "face-api", "id_query": id_query},
        )
        return ExportAcceptedResponse(
            export_id=export.id,
            id_query=export.id_query,
            export_format=export.export_format,
            status=export.status,
            created_at=export.created_at,
        )

    async def retry_query_stage(
        self,
        id_query: str,
        request: RetryQueryStageRequest,
    ) -> RetryAcceptedResponse:
        set_log_context(id_query=id_query)
        job = self.repository.get_job(id_query)

        if request.stage == "search":
            search_payload = self._build_search_payload_from_job(job)
            try:
                await self._publish_stage_request(
                    id_query=id_query,
                    stage="search",
                    queue_name=self.queue_names.search_request,
                    payload=search_payload,
                    status_current="search_requested",
                    event_type="search.retry_requested",
                )
            except Exception as exc:
                await self._handle_retry_publish_failure(
                    id_query=id_query,
                    stage="search",
                    payload=search_payload,
                    error=exc,
                )
                raise
            return RetryAcceptedResponse(
                id_query=id_query,
                stage="search",
                status_current="search_requested",
            )

        if request.stage == "enrich":
            records = self._resolve_records_for_enrich_retry(
                id_query=id_query,
                record_ids=request.record_ids,
            )
            enrich_payloads = [
                self._build_enrich_payload(id_query=id_query, record=record, job=job)
                for record in records
            ]
            retry_count = 0
            try:
                for enrich_payload in enrich_payloads:
                    await self.publisher.publish_json(
                        self.queue_names.enrich_request,
                        enrich_payload,
                    )
                    retry_count += 1
                await self._publish_job_event(
                    id_query=id_query,
                    event_type="enrich.retry_requested",
                    payload={
                        "id_query": id_query,
                        "stage": "enrich",
                        "record_ids": [record.id for record in records],
                        "retried_records": retry_count,
                    },
                )
                self.repository.update_job_status(
                    id_query=id_query,
                    status_current="enrich_requested",
                )
                self.repository.add_event(
                    id_query=id_query,
                    event_type="enrich.retry_requested",
                    payload={
                        "stage": "enrich",
                        "record_ids": [record.id for record in records],
                        "retried_records": retry_count,
                    },
                )
            except Exception as exc:
                await self._handle_retry_publish_failure(
                    id_query=id_query,
                    stage="enrich",
                    payload={
                        "record_ids": [record.id for record in records],
                        "retried_records": retry_count,
                    },
                    error=exc,
                )
                raise
            return RetryAcceptedResponse(
                id_query=id_query,
                stage="enrich",
                status_current="enrich_requested",
                retried_records=retry_count,
            )

        export_format = self._resolve_export_format_for_retry(
            id_query=id_query,
            export_id=request.export_id,
            export_format=request.export_format,
        )
        export = self.repository.create_export_request(
            id_query=id_query,
            export_format=export_format,
        )
        export_payload = {
            "export_id": export.id,
            "id_query": id_query,
            "export_format": export.export_format,
            "retry_of_export_id": request.export_id,
        }
        try:
            await self._publish_stage_request(
                id_query=id_query,
                stage="export",
                queue_name=self.queue_names.export_request,
                payload=export_payload,
                status_current="export_requested",
                event_type="export.retry_requested",
            )
            export = self.repository.update_export_status(export_id=export.id, status="requested")
        except Exception as exc:
            self.repository.update_export_status(export_id=export.id, status="enqueue_failed")
            await self._handle_retry_publish_failure(
                id_query=id_query,
                stage="export",
                payload=export_payload,
                error=exc,
            )
            raise
        return RetryAcceptedResponse(
            id_query=id_query,
            stage="export",
            status_current="export_requested",
            export_id=export.id,
            export_format=export.export_format,
        )

    def _resolve_queries(self, request: CreateQueriesRequest) -> list[ResolvedQuery]:
        return [
            ResolvedQuery(id_query=query.id_query or uuid4().hex, query=query)
            for query in request.queries
        ]

    def _validate_batch_conflicts(self, resolved_queries: list[ResolvedQuery]) -> None:
        resolved_ids = [resolved.id_query for resolved in resolved_queries]

        duplicates = {
            id_query for id_query in resolved_ids if resolved_ids.count(id_query) > 1
        }
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise QueryConflictError(
                f"Duplicate id_query values in request batch: {duplicate_list}"
            )

        existing_ids = self.repository.existing_job_ids(resolved_ids)
        if existing_ids:
            duplicate_list = ", ".join(sorted(existing_ids))
            raise QueryConflictError(f"Query already exists: {duplicate_list}")

    @staticmethod
    def _build_search_payload_from_query(
        id_query: str,
        query: QueryCreateInput,
    ) -> dict[str, object]:
        return {
            "id_query": id_query,
            "subject": query.subject,
            "query_source": query.query_source,
            "start_date": query.start_date.isoformat() if query.start_date else None,
            "end_date": query.end_date.isoformat() if query.end_date else None,
        }

    @staticmethod
    def _build_search_payload_from_job(job) -> dict[str, object]:  # type: ignore[no-untyped-def]
        return {
            "id_query": job.id_query,
            "subject": job.subject,
            "query_source": job.query_source,
            "start_date": job.start_date.isoformat() if job.start_date else None,
            "end_date": job.end_date.isoformat() if job.end_date else None,
        }

    @staticmethod
    def _build_enrich_payload(
        *,
        id_query: str,
        record: FaceRecord,
        job,  # type: ignore[no-untyped-def]
    ) -> dict[str, object]:
        category = record.category
        if not category:
            raise QueryRetryError(
                f"Record '{record.id}' cannot be retried for enrich because category is missing"
            )
        facebook_url = record.url_normalized or record.url
        return {
            "id_query": id_query,
            "facebook_url": facebook_url,
            "category": category,
            "query_source": job.query_source,
            "record_id": record.id,
        }

    def _resolve_records_for_enrich_retry(
        self,
        *,
        id_query: str,
        record_ids: list[int] | None,
    ) -> list[FaceRecord]:
        records = list(self.repository.list_records(id_query))
        if not records:
            raise QueryRetryError(f"Query '{id_query}' has no records available for enrich retry")

        if record_ids is None:
            eligible_records = [record for record in records if record.url or record.url_normalized]
            if not eligible_records:
                raise QueryRetryError(
                    f"Query '{id_query}' has no eligible records available for enrich retry"
                )
            return eligible_records

        record_map = {record.id: record for record in records}
        missing_ids = [record_id for record_id in record_ids if record_id not in record_map]
        if missing_ids:
            missing_list = ", ".join(str(record_id) for record_id in missing_ids)
            raise QueryRetryError(
                f"Query '{id_query}' does not contain the requested record_ids: {missing_list}"
            )
        return [record_map[record_id] for record_id in record_ids]

    def _resolve_export_format_for_retry(
        self,
        *,
        id_query: str,
        export_id: int | None,
        export_format: str | None,
    ) -> str:
        if export_format is not None:
            return export_format

        exports = list(self.repository.list_exports(id_query))
        if not exports:
            raise QueryRetryError(
                f"Query '{id_query}' has no previous exports; provide export_format to retry export"
            )

        if export_id is not None:
            for export in exports:
                if export.id == export_id:
                    return export.export_format
            raise QueryRetryError(f"Query '{id_query}' does not contain export '{export_id}'")

        latest_export: FaceExport = exports[-1]
        return latest_export.export_format

    async def _publish_stage_request(
        self,
        *,
        id_query: str,
        stage: str,
        queue_name: str,
        payload: dict[str, object],
        status_current: str,
        event_type: str,
    ) -> None:
        await self.publisher.publish_json(queue_name, payload)
        await self._publish_job_event(
            id_query=id_query,
            event_type=event_type,
            payload=payload,
        )
        self.repository.update_job_status(
            id_query=id_query,
            status_current=status_current,
        )
        self.repository.add_event(
            id_query=id_query,
            event_type=event_type,
            payload={"stage": stage, **payload},
        )

    async def _publish_job_event(
        self,
        *,
        id_query: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        await self.publisher.publish_json(
            self.queue_names.job_events,
            {
                "id_query": id_query,
                "event_type": event_type,
                "payload": payload,
            },
        )

    async def _handle_retry_publish_failure(
        self,
        *,
        id_query: str,
        stage: str,
        payload: dict[str, object],
        error: Exception,
    ) -> None:
        self.repository.update_job_status(
            id_query=id_query,
            status_current=f"{stage}_retry_enqueue_failed",
        )
        self.repository.add_event(
            id_query=id_query,
            event_type=f"{stage}.retry_enqueue_failed",
            payload={**payload, "error": str(error)},
        )
        logger.exception(
            "Failed to publish retry request",
            extra={"service": "face-api", "id_query": id_query, "stage": stage},
        )
