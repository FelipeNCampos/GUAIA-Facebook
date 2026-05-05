from __future__ import annotations

from db.base import Base
from db.models import FaceJob, FaceJobEvent
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
                "start_date": "2026-05-01",
                "end_date": "2026-05-05",
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
