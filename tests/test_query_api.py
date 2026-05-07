from __future__ import annotations

from db.base import Base
from db.models import FaceExport, FaceJob, FaceJobEvent, FaceRecord
from face.api import app
from face.queues import QueueNames
from face.repository import FaceJobRepository
from face.services import QueryService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def publish_json(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))


class FailingPublisher(FakePublisher):
    async def publish_json(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))
        raise RuntimeError("rabbitmq unavailable")


def build_test_service(database_url: str) -> tuple[
    QueryService,
    FakePublisher,
    sessionmaker[Session],
]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    publisher = FakePublisher()
    service = QueryService(
        repository=FaceJobRepository(session_factory),
        publisher=publisher,
        queue_names=QueueNames(),
    )
    return service, publisher, session_factory


def build_test_service_with_publisher(
    database_url: str,
    publisher: FakePublisher,
) -> tuple[QueryService, sessionmaker[Session]]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    service = QueryService(
        repository=FaceJobRepository(session_factory),
        publisher=publisher,
        queue_names=QueueNames(),
    )
    return service, session_factory


def test_create_query_persists_and_publishes(tmp_path) -> None:
    db_path = tmp_path / "query_create.db"
    service, publisher, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    payload = {
        "queries": [
            {
                "subject": "candidato exemplo",
                "query_source": "manual",
            }
        ]
    }

    with TestClient(app) as client:
        response = client.post("/facebook/queries", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert len(body["queries"]) == 1
    id_query = body["queries"][0]["id_query"]
    assert body["queries"][0]["status_current"] == "search_requested"

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == id_query).one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == id_query)
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.subject == "candidato exemplo"
    assert job.query_source == "manual"
    assert job.status_current == "search_requested"
    assert [event.event_type for event in events] == ["query.created", "search.requested"]

    assert publisher.messages[0][0] == "face.search.request"
    assert publisher.messages[1][0] == "face.job.events"
    assert publisher.messages[0][1]["id_query"] == id_query

    del app.state.query_service


def test_get_query_status_returns_basic_status(tmp_path) -> None:
    db_path = tmp_path / "query_status.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-status-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )
        id_query = create_response.json()["queries"][0]["id_query"]
        response = client.get(f"/facebook/queries/{id_query}")

    assert response.status_code == 200
    body = response.json()
    assert body["id_query"] == "query-status-1"
    assert body["status_current"] == "search_requested"
    assert [event["event_type"] for event in body["recent_events"]] == [
        "query.created",
        "search.requested",
    ]

    del app.state.query_service


def test_create_query_conflict_returns_409(tmp_path) -> None:
    db_path = tmp_path / "query_conflict.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    payload = {"queries": [{"id_query": "dup-1", "subject": "tema", "query_source": "api"}]}

    with TestClient(app) as client:
        first = client.post("/facebook/queries", json=payload)
        second = client.post("/facebook/queries", json=payload)

    assert first.status_code == 202
    assert second.status_code == 409

    del app.state.query_service


def test_get_query_status_returns_404_for_unknown_query(tmp_path) -> None:
    db_path = tmp_path / "query_not_found.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        response = client.get("/facebook/queries/missing-query")

    assert response.status_code == 404

    del app.state.query_service


def test_duplicate_ids_in_same_batch_return_409_without_partial_persist(tmp_path) -> None:
    db_path = tmp_path / "query_duplicate_batch.db"
    service, _, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    payload = {
        "queries": [
            {"id_query": "dup-batch", "subject": "tema 1", "query_source": "api"},
            {"id_query": "dup-batch", "subject": "tema 2", "query_source": "api"},
        ]
    }

    with TestClient(app) as client:
        response = client.post("/facebook/queries", json=payload)

    assert response.status_code == 409

    with session_factory() as session:
        persisted = session.query(FaceJob).filter(FaceJob.id_query == "dup-batch").all()

    assert persisted == []

    del app.state.query_service


def test_publish_failure_marks_enqueue_failed_status(tmp_path) -> None:
    db_path = tmp_path / "query_publish_failure.db"
    publisher = FailingPublisher()
    service, session_factory = build_test_service_with_publisher(f"sqlite:///{db_path}", publisher)
    app.state.query_service = service

    payload = {
        "queries": [
            {"id_query": "publish-failure-1", "subject": "tema", "query_source": "api"}
        ]
    }

    with TestClient(app) as client:
        response = client.post("/facebook/queries", json=payload)

    assert response.status_code == 500

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == "publish-failure-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "publish-failure-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.status_current == "enqueue_failed"
    assert [event.event_type for event in events] == ["query.created", "search.enqueue_failed"]

    del app.state.query_service


def test_get_query_records_returns_records_for_query(tmp_path) -> None:
    db_path = tmp_path / "query_records.db"
    service, _, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-records-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )

    assert create_response.status_code == 202

    with session_factory() as session:
        session.add_all(
            [
                FaceRecord(
                    id_query="query-records-1",
                    url="https://www.facebook.com/foo/posts/1",
                    url_normalized="https://www.facebook.com/foo/posts/1",
                    category="post",
                    status="discovered",
                    payload={"search_page": 1},
                ),
                FaceRecord(
                    id_query="query-records-1",
                    url="https://www.facebook.com/foo/videos/2",
                    url_normalized="https://www.facebook.com/foo/videos/2",
                    category="video",
                    status="enriched",
                    payload={"likes": 10},
                ),
            ]
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/facebook/queries/query-records-1/records")

    assert response.status_code == 200
    body = response.json()
    assert body["id_query"] == "query-records-1"
    assert len(body["records"]) == 2
    assert body["records"][0]["category"] == "post"
    assert body["records"][1]["status"] == "enriched"

    del app.state.query_service


def test_get_query_exports_and_create_export_request(tmp_path) -> None:
    db_path = tmp_path / "query_exports.db"
    service, publisher, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-exports-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )
        export_response = client.post(
            "/facebook/queries/query-exports-1/export",
            json={"export_format": "json"},
        )

    assert create_response.status_code == 202
    assert export_response.status_code == 202
    export_body = export_response.json()
    assert export_body["id_query"] == "query-exports-1"
    assert export_body["export_format"] == "json"
    assert export_body["status"] == "requested"

    with session_factory() as session:
        exports = (
            session.query(FaceExport)
            .filter(FaceExport.id_query == "query-exports-1")
            .order_by(FaceExport.id.asc())
            .all()
        )
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "query-exports-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert len(exports) == 1
    assert exports[0].status == "requested"
    assert publisher.messages[2][0] == "face.export.request"
    assert publisher.messages[2][1]["id_query"] == "query-exports-1"
    assert publisher.messages[3][0] == "face.job.events"
    assert [event.event_type for event in events][-1] == "export.requested"

    with TestClient(app) as client:
        list_response = client.get("/facebook/queries/query-exports-1/exports")

    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["id_query"] == "query-exports-1"
    assert len(list_body["exports"]) == 1
    assert list_body["exports"][0]["export_format"] == "json"
    assert list_body["exports"][0]["status"] == "requested"

    del app.state.query_service


def test_query_records_and_exports_return_404_for_unknown_query(tmp_path) -> None:
    db_path = tmp_path / "query_records_exports_not_found.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        records_response = client.get("/facebook/queries/missing-query/records")
        exports_response = client.get("/facebook/queries/missing-query/exports")
        create_export_response = client.post(
            "/facebook/queries/missing-query/export",
            json={"export_format": "xlsx"},
        )

    assert records_response.status_code == 404
    assert exports_response.status_code == 404
    assert create_export_response.status_code == 404

    del app.state.query_service


def test_retry_search_stage_reenqueues_existing_query(tmp_path) -> None:
    db_path = tmp_path / "query_retry_search.db"
    service, publisher, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-retry-search-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )
        retry_response = client.post(
            "/facebook/queries/query-retry-search-1/retry",
            json={"stage": "search"},
        )

    assert create_response.status_code == 202
    assert retry_response.status_code == 202
    body = retry_response.json()
    assert body["id_query"] == "query-retry-search-1"
    assert body["stage"] == "search"
    assert body["status_current"] == "search_requested"

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == "query-retry-search-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "query-retry-search-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.status_current == "search_requested"
    assert [event.event_type for event in events][-1] == "search.retry_requested"
    assert publisher.messages[2][0] == "face.search.request"
    assert publisher.messages[2][1]["id_query"] == "query-retry-search-1"
    assert publisher.messages[3][0] == "face.job.events"
    assert publisher.messages[3][1]["event_type"] == "search.retry_requested"

    del app.state.query_service


def test_retry_enrich_stage_reenqueues_selected_records(tmp_path) -> None:
    db_path = tmp_path / "query_retry_enrich.db"
    service, publisher, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-retry-enrich-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )

    assert create_response.status_code == 202

    with session_factory() as session:
        session.add_all(
            [
                FaceRecord(
                    id_query="query-retry-enrich-1",
                    url="https://www.facebook.com/foo/posts/1",
                    url_normalized="https://www.facebook.com/foo/posts/1",
                    category="post",
                    status="discovered",
                    payload={"search_page": 1},
                ),
                FaceRecord(
                    id_query="query-retry-enrich-1",
                    url="https://www.facebook.com/foo/videos/2",
                    url_normalized="https://www.facebook.com/foo/videos/2",
                    category="video",
                    status="failed",
                    payload={"search_page": 1},
                ),
            ]
        )
        session.commit()
        records = (
            session.query(FaceRecord)
            .filter(FaceRecord.id_query == "query-retry-enrich-1")
            .order_by(FaceRecord.id.asc())
            .all()
        )
        retry_record_id = records[1].id

    with TestClient(app) as client:
        retry_response = client.post(
            "/facebook/queries/query-retry-enrich-1/retry",
            json={"stage": "enrich", "record_ids": [retry_record_id]},
        )

    assert retry_response.status_code == 202
    body = retry_response.json()
    assert body["stage"] == "enrich"
    assert body["status_current"] == "enrich_requested"
    assert body["retried_records"] == 1

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == "query-retry-enrich-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "query-retry-enrich-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.status_current == "enrich_requested"
    assert [event.event_type for event in events][-1] == "enrich.retry_requested"
    assert publisher.messages[2][0] == "face.enrich.request"
    assert publisher.messages[2][1]["record_id"] == retry_record_id
    assert publisher.messages[3][0] == "face.job.events"
    assert publisher.messages[3][1]["event_type"] == "enrich.retry_requested"

    del app.state.query_service


def test_retry_export_stage_reuses_latest_format_when_not_explicit(tmp_path) -> None:
    db_path = tmp_path / "query_retry_export.db"
    service, publisher, session_factory = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-retry-export-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )
        export_response = client.post(
            "/facebook/queries/query-retry-export-1/export",
            json={"export_format": "xlsx"},
        )

    assert create_response.status_code == 202
    assert export_response.status_code == 202

    with TestClient(app) as client:
        retry_response = client.post(
            "/facebook/queries/query-retry-export-1/retry",
            json={"stage": "export"},
        )

    assert retry_response.status_code == 202
    body = retry_response.json()
    assert body["stage"] == "export"
    assert body["status_current"] == "export_requested"
    assert body["export_format"] == "xlsx"
    assert body["export_id"] is not None

    with session_factory() as session:
        exports = (
            session.query(FaceExport)
            .filter(FaceExport.id_query == "query-retry-export-1")
            .order_by(FaceExport.id.asc())
            .all()
        )
        job = session.query(FaceJob).filter(FaceJob.id_query == "query-retry-export-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "query-retry-export-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert len(exports) == 2
    assert exports[-1].status == "requested"
    assert exports[-1].export_format == "xlsx"
    assert job.status_current == "export_requested"
    assert [event.event_type for event in events][-1] == "export.retry_requested"
    assert publisher.messages[4][0] == "face.export.request"
    assert publisher.messages[5][0] == "face.job.events"
    assert publisher.messages[5][1]["event_type"] == "export.retry_requested"

    del app.state.query_service


def test_retry_endpoint_returns_409_for_invalid_enrich_record_selection(tmp_path) -> None:
    db_path = tmp_path / "query_retry_invalid_enrich.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        create_response = client.post(
            "/facebook/queries",
            json={
                "queries": [
                    {
                        "id_query": "query-retry-invalid-enrich-1",
                        "subject": "tema",
                        "query_source": "api",
                    }
                ]
            },
        )
        retry_response = client.post(
            "/facebook/queries/query-retry-invalid-enrich-1/retry",
            json={"stage": "enrich", "record_ids": [999]},
        )

    assert create_response.status_code == 202
    assert retry_response.status_code == 409

    del app.state.query_service


def test_retry_endpoint_returns_404_for_unknown_query(tmp_path) -> None:
    db_path = tmp_path / "query_retry_not_found.db"
    service, _, _ = build_test_service(f"sqlite:///{db_path}")
    app.state.query_service = service

    with TestClient(app) as client:
        retry_response = client.post(
            "/facebook/queries/missing-query/retry",
            json={"stage": "search"},
        )

    assert retry_response.status_code == 404

    del app.state.query_service
