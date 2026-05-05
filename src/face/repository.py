from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from db.models import FaceExport, FaceJob, FaceJobEvent, FaceRecord
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
            job = self._get_job_or_raise(session, id_query)
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

    def get_job(self, id_query: str) -> FaceJob:
        with self.session_factory() as session:
            job = self._get_job_or_raise(session, id_query)
            session.expunge(job)
            return job

    def list_records(self, id_query: str) -> Sequence[FaceRecord]:
        with self.session_factory() as session:
            self._get_job_or_raise(session, id_query)
            records = (
                session.query(FaceRecord)
                .filter(FaceRecord.id_query == id_query)
                .order_by(FaceRecord.created_at.asc(), FaceRecord.id.asc())
                .all()
            )
            for record in records:
                session.expunge(record)
            return records

    def list_exports(self, id_query: str) -> Sequence[FaceExport]:
        with self.session_factory() as session:
            self._get_job_or_raise(session, id_query)
            exports = (
                session.query(FaceExport)
                .filter(FaceExport.id_query == id_query)
                .order_by(FaceExport.created_at.asc(), FaceExport.id.asc())
                .all()
            )
            for export in exports:
                session.expunge(export)
            return exports

    def create_export_request(self, *, id_query: str, export_format: str) -> FaceExport:
        with self.session_factory() as session:
            self._get_job_or_raise(session, id_query)
            export = FaceExport(
                id_query=id_query,
                export_format=export_format,
                status="pending",
            )
            session.add(export)
            session.commit()
            session.refresh(export)
            return export

    def update_export_status(
        self,
        *,
        export_id: int,
        status: str,
        storage_path: str | None = None,
        completed_at: datetime | None = None,
    ) -> FaceExport:
        with self.session_factory() as session:
            export = session.query(FaceExport).filter(FaceExport.id == export_id).one_or_none()
            if export is None:
                raise QueryNotFoundError(f"Export '{export_id}' was not found")
            export.status = status
            export.storage_path = storage_path
            export.completed_at = completed_at
            session.commit()
            session.refresh(export)
            return export

    @staticmethod
    def _get_job_or_raise(session: Session, id_query: str) -> FaceJob:
        job = session.query(FaceJob).filter(FaceJob.id_query == id_query).one_or_none()
        if job is None:
            raise QueryNotFoundError(f"Query '{id_query}' was not found")
        return job


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

    def upsert_enriched_record(
        self,
        *,
        id_query: str,
        url: str,
        url_normalized: str,
        category: str,
        payload: dict[str, object] | None,
        record_id: int | None = None,
        status: str = "enriched",
        last_error: str | None = None,
    ) -> FaceRecord:
        with self.session_factory() as session:
            existing = None
            if record_id is not None:
                existing = (
                    session.query(FaceRecord).filter(FaceRecord.id == record_id).one_or_none()
                )

            if existing is None:
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
                    status=status,
                    payload=payload,
                    last_error=last_error,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                return record

            existing.url = url
            existing.url_normalized = url_normalized
            existing.category = category
            existing.status = status
            existing.payload = payload
            existing.last_error = last_error
            session.commit()
            session.refresh(existing)
            return existing
