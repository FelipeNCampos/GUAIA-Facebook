from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import aio_pika
import pytest
from db.base import Base
from db.models import FaceJob, FaceJobEvent, FaceRecord
from face.queues import QueueNames, RabbitMQConsumer, RabbitMQInfrastructure, RabbitMQPublisher
from face.repository import FaceJobRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


async def purge_queues(rabbitmq_url: str, queue_names: tuple[str, ...]) -> None:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    channel = await connection.channel()
    try:
        for queue_name in queue_names:
            queue = await channel.declare_queue(queue_name, durable=True)
            await queue.purge()
    finally:
        await channel.close()
        await connection.close()


async def consume_payload_and_ack(queue_name: str) -> dict[str, object] | None:
    consumed = await RabbitMQConsumer().get_json_message(queue_name, timeout_seconds=5.0)
    if consumed is None:
        return None
    payload = consumed.payload
    await consumed.ack()
    return payload


@contextmanager
def search_results_server(html: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/search":
                self.send_response(404)
                self.end_headers()
                return

            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/search"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.integration
def test_rabbitmq_round_trip_with_real_broker(
    integration_service_environment,
    integration_rabbitmq_url: str,
) -> None:
    queue_names = QueueNames()
    asyncio.run(RabbitMQInfrastructure().ensure_minimum_queues())
    asyncio.run(purge_queues(integration_rabbitmq_url, (queue_names.job_events,)))

    payload = {
        "id_query": uuid4().hex,
        "event_type": "integration.test",
        "payload": {"source": "pytest"},
    }

    asyncio.run(RabbitMQPublisher().publish_json(queue_names.job_events, payload))
    consumed = asyncio.run(consume_payload_and_ack(queue_names.job_events))

    assert consumed is not None
    assert consumed == payload


@pytest.mark.integration
def test_google_search_spider_persists_to_postgres_and_publishes_to_rabbitmq(
    integration_service_environment,
    integration_database_url: str,
    integration_rabbitmq_url: str,
) -> None:
    session_factory = build_session_factory(integration_database_url)
    job_repository = FaceJobRepository(session_factory)
    queue_names = QueueNames()
    id_query = f"integration-{uuid4().hex}"

    job_repository.create_job(
        id_query=id_query,
        subject="tema integracao",
        query_source="api",
        start_date=None,
        end_date=None,
    )
    job_repository.update_job_status(id_query=id_query, status_current="search_requested")

    asyncio.run(RabbitMQInfrastructure().ensure_minimum_queues())
    asyncio.run(
        purge_queues(
            integration_rabbitmq_url,
            (
                queue_names.url_discovered,
                queue_names.enrich_request,
                queue_names.job_events,
            ),
        )
    )

    html = """
    <html>
        <body>
            <a
                href="/url?q=https%3A%2F%2Fwww.facebook.com%2Ffoo%2Fposts%2F123%3Fref%3Dwatch"
            >
                Resultado
            </a>
            <a href="https://example.com/ignorar">Ignorar</a>
        </body>
    </html>
    """

    with search_results_server(html) as search_url:
        child_env = os.environ.copy()
        child_env["FACE_SEARCH_JOB_JSON"] = json.dumps(
            {
                "id_query": id_query,
                "subject": "tema integracao",
                "query_source": "api",
                "max_pages": 1,
                "search_url_override": search_url,
                "user_agents": ["integration-test-agent"],
            }
        )
        subprocess.run(
            [sys.executable, "-m", "face.worker_runtime", "search"],
            check=True,
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
        )

    with session_factory() as session:
        record = session.query(FaceRecord).filter(FaceRecord.id_query == id_query).one()
        job = session.query(FaceJob).filter(FaceJob.id_query == id_query).one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == id_query)
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert record.url_normalized == "https://www.facebook.com/foo/posts/123"
    assert record.category == "post"
    assert record.status == "discovered"
    assert job.status_current == "search_completed"
    assert [event.event_type for event in events] == [
        "search.started",
        "search.url_discovered",
        "search.completed",
    ]

    discovered_message = asyncio.run(consume_payload_and_ack(queue_names.url_discovered))
    consumed = asyncio.run(consume_payload_and_ack(queue_names.enrich_request))
    started_event = asyncio.run(consume_payload_and_ack(queue_names.job_events))
    discovered_event = asyncio.run(consume_payload_and_ack(queue_names.job_events))
    completed_event = asyncio.run(consume_payload_and_ack(queue_names.job_events))

    assert discovered_message is not None
    assert discovered_message["id_query"] == id_query
    assert discovered_message["facebook_url"] == "https://www.facebook.com/foo/posts/123"
    assert consumed is not None
    assert consumed["id_query"] == id_query
    assert consumed["facebook_url"] == "https://www.facebook.com/foo/posts/123"
    assert consumed["category"] == "post"
    assert consumed["query_source"] == "api"
    assert consumed["record_id"] == record.id
    assert started_event is not None
    assert started_event["event_type"] == "search.started"
    assert discovered_event is not None
    assert discovered_event["event_type"] == "search.url_discovered"
    assert completed_event is not None
    assert completed_event["event_type"] == "search.completed"
