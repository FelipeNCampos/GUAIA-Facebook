from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from db.models import FaceJob, FaceJobEvent, FaceRecord
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from face.config import Settings, get_settings


def create_engine_from_settings(settings: Settings | None = None) -> Engine:
    resolved = settings or get_settings()
    if not resolved.database_url:
        raise ValueError("DATABASE_URL must be configured via environment variable")
    return create_engine(resolved.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    engine = create_engine_from_settings(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


class QueryNotFoundError(Exception):
    """Raised when a query could not be found in the repository."""


class FaceJobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_job(
        self,
        *,
        id_query: str,
        subject: str,
        query_source: str,
        start_date: date | None,
        end_date: date | None,
    ) -> FaceJob:
        with self.session_factory() as session:
            job = FaceJob(
                id_query=id_query,
                subject=subject,
                query_source=query_source,
                start_date=start_date,
                end_date=end_date,
                status_current="pending",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def update_job_status(self, *, id_query: str, status_current: str) -> FaceJob:
        with self.session_factory() as session:
            job = session.query(FaceJob).filter(FaceJob.id_query == id_query).one_or_none()
            if job is None:
                raise QueryNotFoundError(f"Query '{id_query}' was not found")
            job.status_current = status_current
            session.commit()
            session.refresh(job)
            return job

    def add_event(
        self,
        *,
        id_query: str,
        event_type: str,
        payload: dict[str, object] | None,
    ) -> FaceJobEvent:
        with self.session_factory() as session:
            event = FaceJobEvent(
                id_query=id_query,
                event_type=event_type,
                payload=payload,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    def existing_job_ids(self, id_queries: Sequence[str]) -> set[str]:
        if not id_queries:
            return set()

        with self.session_factory() as session:
            rows = session.scalars(
                select(FaceJob.id_query).where(FaceJob.id_query.in_(list(id_queries)))
            ).all()
            return set(rows)

    def get_job_with_events(self, id_query: str) -> tuple[FaceJob, Sequence[FaceJobEvent]]:
        with self.session_factory() as session:
            job = session.query(FaceJob).filter(FaceJob.id_query == id_query).one_or_none()
            if job is None:
                raise QueryNotFoundError(f"Query '{id_query}' was not found")
            events = (
                session.query(FaceJobEvent)
                .filter(FaceJobEvent.id_query == id_query)
                .order_by(FaceJobEvent.created_at.asc(), FaceJobEvent.id.asc())
                .all()
            )
            session.expunge(job)
            for event in events:
                session.expunge(event)
            return job, events


class FaceRecordRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert_discovered_record(
        self,
        *,
        id_query: str,
        url: str,
        url_normalized: str,
        category: str,
        payload: dict[str, object] | None,
    ) -> FaceRecord:
        with self.session_factory() as session:
            existing = (
                session.query(FaceRecord)
                .filter(
                    FaceRecord.id_query == id_query,
                    FaceRecord.url_normalized == url_normalized,
                )
                .one_or_none()
            )

            if existing is None:
                record = FaceRecord(
                    id_query=id_query,
                    url=url,
                    url_normalized=url_normalized,
                    category=category,
                    status="discovered",
                    payload=payload,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                return record

            existing.url = url
            existing.url_normalized = url_normalized
            existing.category = category
            existing.status = "discovered"
            existing.payload = payload
            existing.last_error = None
            session.commit()
            session.refresh(existing)
            return existing
