from __future__ import annotations

from db.base import Base
from db.models import FaceJob, FaceJobEvent, FaceRecord
from face.items import FacebookURLItem
from face.pipelines.events import EventsPipeline
from face.pipelines.persist import PersistPipeline
from face.queues import QueueNames
from face.repository import FaceJobRepository, FaceRecordRepository
from face.spiders.google_search import GoogleSearchSpider
from scrapy import Request
from scrapy.http import HtmlResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def publish_json(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_html_response(url: str, html: str, request: Request | None = None) -> HtmlResponse:
    request = request or Request(url=url)
    return HtmlResponse(url=url, request=request, body=html.encode("utf-8"), encoding="utf-8")


def build_crawler_stub():
    return type(
        "CrawlerStub",
        (),
        {
            "stats": type(
                "StatsStub",
                (),
                {
                    "values": {},
                    "inc_value": lambda self, key, count=1: self.values.__setitem__(
                        key, self.values.get(key, 0) + count
                    ),
                    "set_value": lambda self, key, value: self.values.__setitem__(key, value),
                },
            )(),
        },
    )()


def test_google_search_spider_parses_urls_and_next_page() -> None:
    spider = GoogleSearchSpider(
        id_query="query-1",
        subject="tema",
        query_source="api",
        start_date="2026-05-01",
        end_date="2026-05-05",
        max_pages=2,
        user_agents=["test-agent"],
    )
    request = next(spider.start_requests())
    response = make_html_response(
        request.url,
        """
        <html>
            <body>
                <a
                    href="/url?q=https%3A%2F%2Fwww.facebook.com%2Ffoo%2Fposts%2F123%3Fref%3Dwatch"
                >
                    A
                </a>
                <a href="https://m.facebook.com/bar/videos/456/?__tn__=R">B</a>
                <a href="https://example.com/ignore">C</a>
                <a id="pnnext" href="/search?q=next-page">Next</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]
    requests = [result for result in results if isinstance(result, Request)]

    assert len(items) == 2
    assert items[0]["category"] == "post"
    assert items[0]["url_normalized"] == "https://www.facebook.com/foo/posts/123"
    assert items[1]["category"] == "video"
    assert len(requests) == 1
    assert requests[0].meta["page_number"] == 2


def test_google_search_spider_detects_google_block_page() -> None:
    spider = GoogleSearchSpider(
        id_query="query-blocked-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_html_response(
        "https://www.google.com/sorry/index?continue=teste",
        """
        <html>
            <head><title>Google Search</title></head>
            <body>
                <p>Our systems have detected unusual traffic from your computer network.</p>
                <a href="/httpservice/retry/enablejs?sei=abc">Enable JS</a>
                <a href="https://support.google.com/websearch">Ajuda</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    assert results == []
    assert spider.search_blocked_details is not None
    assert spider.search_blocked_details["response_url"] == response.url
    assert spider.search_blocked_details["anchor_count"] == 2
    assert spider.search_blocked_details["marker_types"] == [
        "enablejs_challenge",
        "google_sorry",
        "unusual_traffic",
    ]
    assert spider.crawler.stats.values["face/search_blocked"] == 1


def test_pipelines_persist_records_and_publish_discovered_urls(tmp_path) -> None:
    db_path = tmp_path / "search_pipeline.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    job_repository = FaceJobRepository(session_factory)
    record_repository = FaceRecordRepository(session_factory)
    publisher = FakePublisher()

    job_repository.create_job(
        id_query="search-job-1",
        subject="tema",
        query_source="api",
        start_date=None,
        end_date=None,
    )
    job_repository.update_job_status(id_query="search-job-1", status_current="search_requested")

    spider = GoogleSearchSpider(
        id_query="search-job-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.record_repository = record_repository
    spider.job_repository = job_repository
    spider.publisher = publisher
    spider.crawler = build_crawler_stub()
    spider.settings = type(
        "SettingsStub", (), {"getfloat": lambda self, key, default=0.0: default}
    )()

    persist_pipeline = PersistPipeline()
    events_pipeline = EventsPipeline()
    persist_pipeline.open_spider(spider)
    events_pipeline.open_spider(spider)

    item = FacebookURLItem(
        item_type="facebook_url",
        id_query="search-job-1",
        url="https://www.facebook.com/foo/posts/123?ref=watch",
        url_normalized="https://www.facebook.com/foo/posts/123",
        category="post",
        query_source="api",
        search_page=1,
        search_position=1,
        source_query="tema",
        discovered_via="google_search",
    )

    persisted_item = persist_pipeline.process_item(item, spider)
    events_pipeline.process_item(persisted_item, spider)
    spider.page_count = 1
    events_pipeline.close_spider(spider)

    with session_factory() as session:
        records = session.query(FaceRecord).filter(FaceRecord.id_query == "search-job-1").all()
        job = session.query(FaceJob).filter(FaceJob.id_query == "search-job-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "search-job-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert len(records) == 1
    assert records[0].category == "post"
    assert job.status_current == "search_completed"
    assert [event.event_type for event in events] == [
        "search.started",
        "search.url_discovered",
        "search.completed",
    ]
    assert publisher.messages[0][0] == QueueNames().enrich_request
    assert publisher.messages[0][1]["facebook_url"] == "https://www.facebook.com/foo/posts/123"
    assert publisher.messages[0][1]["query_source"] == "api"


def test_pipelines_mark_empty_search_without_google_block(tmp_path) -> None:
    db_path = tmp_path / "search_pipeline_empty.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    job_repository = FaceJobRepository(session_factory)

    job_repository.create_job(
        id_query="search-job-empty-1",
        subject="tema",
        query_source="api",
        start_date=None,
        end_date=None,
    )
    job_repository.update_job_status(id_query="search-job-empty-1", status_current="search_requested")

    spider = GoogleSearchSpider(
        id_query="search-job-empty-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.job_repository = job_repository
    spider.publisher = FakePublisher()
    spider.crawler = build_crawler_stub()
    spider.settings = type(
        "SettingsStub", (), {"getfloat": lambda self, key, default=0.0: default}
    )()
    spider.page_count = 1

    events_pipeline = EventsPipeline()
    events_pipeline.open_spider(spider)
    events_pipeline.close_spider(spider)

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == "search-job-empty-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "search-job-empty-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.status_current == "search_completed_empty"
    assert [event.event_type for event in events] == [
        "search.started",
        "search.completed",
    ]


def test_pipelines_mark_blocked_search_when_google_challenges(tmp_path) -> None:
    db_path = tmp_path / "search_pipeline_blocked.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    job_repository = FaceJobRepository(session_factory)

    job_repository.create_job(
        id_query="search-job-blocked-1",
        subject="tema",
        query_source="api",
        start_date=None,
        end_date=None,
    )
    job_repository.update_job_status(
        id_query="search-job-blocked-1", status_current="search_requested"
    )

    spider = GoogleSearchSpider(
        id_query="search-job-blocked-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.job_repository = job_repository
    spider.publisher = FakePublisher()
    spider.crawler = build_crawler_stub()
    spider.settings = type(
        "SettingsStub", (), {"getfloat": lambda self, key, default=0.0: default}
    )()
    spider.page_count = 1
    spider.search_blocked_details = {
        "marker_types": ["google_sorry", "unusual_traffic"],
        "response_url": "https://www.google.com/sorry/index",
        "title": "Google Search",
        "page_number": 1,
        "anchor_count": 3,
    }

    events_pipeline = EventsPipeline()
    events_pipeline.open_spider(spider)
    events_pipeline.close_spider(spider)

    with session_factory() as session:
        job = session.query(FaceJob).filter(FaceJob.id_query == "search-job-blocked-1").one()
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "search-job-blocked-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert job.status_current == "search_blocked"
    assert [event.event_type for event in events] == [
        "search.started",
        "search.blocked",
    ]
    assert events[1].payload["marker_types"] == ["google_sorry", "unusual_traffic"]
    assert events[1].payload["pages_visited"] == 1
