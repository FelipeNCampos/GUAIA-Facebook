from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from common.logging import get_logger, set_log_context
from sqlalchemy.exc import IntegrityError

from face.models import CreateQueriesRequest, QueryCreateInput, QueryStatusResponse
from face.queues import Publisher, QueueNames
from face.repository import FaceJobRepository

logger = get_logger(__name__)


class QueryConflictError(Exception):
    """Raised when a query cannot be created due to a business conflict."""


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

            search_payload = {
                "id_query": id_query,
                "subject": query.subject,
                "query_source": query.query_source,
                "start_date": query.start_date.isoformat() if query.start_date else None,
                "end_date": query.end_date.isoformat() if query.end_date else None,
            }

            try:
                await self.publisher.publish_json(self.queue_names.search_request, search_payload)
                await self.publisher.publish_json(
                    self.queue_names.job_events,
                    {
                        "id_query": id_query,
                        "event_type": "search.requested",
                        "payload": search_payload,
                    },
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

            job = self.repository.update_job_status(
                id_query=id_query,
                status_current="search_requested",
            )
            self.repository.add_event(
                id_query=id_query,
                event_type="search.requested",
                payload=search_payload,
            )

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
