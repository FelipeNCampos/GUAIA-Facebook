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
from scrapy.http import TextResponse
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


def make_json_response(
    url: str,
    payload: str,
    request: Request | None = None,
    status: int = 200,
) -> TextResponse:
    request = request or Request(url=url)
    return TextResponse(
        url=url,
        request=request,
        body=payload.encode("utf-8"),
        encoding="utf-8",
        status=status,
    )


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


def test_google_search_spider_builds_searxng_request() -> None:
    spider = GoogleSearchSpider(
        id_query="query-headers-1",
        subject="tema",
        query_source="api",
        user_agents=["test-agent"],
        search_language="pt-BR",
        search_region="br",
        enabled_engines="google,bing",
    )

    request = next(spider.start_requests())

    assert request.headers["User-Agent"].decode("utf-8") == "test-agent"
    assert request.headers["Accept-Language"].decode("utf-8").startswith("pt-BR")
    assert request.url.startswith("http://searxng:8080/search?")
    assert "format=json" in request.url
    assert "site%3Afacebook.com" in request.url
    assert "tema" in request.url
    assert "engines=google%2Cbing" in request.url
    assert request.meta["search_attempt"] == 1
    assert request.meta["search_retry_count"] == 0


def test_google_search_spider_uses_env_backed_default_max_pages_when_not_provided() -> None:
    spider = GoogleSearchSpider(
        id_query="query-default-pages-1",
        subject="tema",
        query_source="api",
        user_agents=["test-agent"],
    )

    assert spider.max_pages == 5


def test_google_search_spider_parses_urls_and_next_page() -> None:
    spider = GoogleSearchSpider(
        id_query="query-1",
        subject="tema",
        query_source="api",
        max_pages=2,
        user_agents=["test-agent"],
        results_per_page=2,
    )
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        """
        {
            "results": [
                {"url": "https://www.facebook.com/foo/posts/123?ref=watch"},
                {"url": "https://m.facebook.com/bar/videos/456/?__tn__=R"},
                {"url": "https://example.com/ignore"}
            ]
        }
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]
    requests = [result for result in results if isinstance(result, Request)]

    assert len(items) == 2
    assert items[0]["category"] == "post"
    assert items[0]["url_normalized"] == "https://www.facebook.com/foo/posts/123"
    assert items[0]["discovered_via"] == "searxng"
    assert items[1]["category"] == "video"
    assert items[1]["url_normalized"] == "https://www.facebook.com/bar/videos/456"
    assert len(requests) == 1
    assert requests[0].meta["page_number"] == 2
    assert requests[0].meta["search_attempt"] == 1
    assert "site%3Afacebook.com" in requests[0].url
    assert "pageno=2" in requests[0].url


def test_google_search_spider_classifies_tracked_reel_url_from_clean_path() -> None:
    spider = GoogleSearchSpider(
        id_query="query-reel-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        """
        {
            "results": [
                {
                    "url": "https://www.facebook.com/reel/3943180995975088?locale=pt_BR&set=a.1"
                }
            ]
        }
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]
    assert len(items) == 1
    assert items[0]["category"] == "reel"


def test_google_search_spider_marks_invalid_json_as_blocked() -> None:
    spider = GoogleSearchSpider(
        id_query="query-invalid-json-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        "{invalid-json",
        request=request,
    )

    results = list(spider.parse_search(response))

    assert results == []
    assert spider.search_blocked_details is not None
    assert spider.search_blocked_details["marker_types"] == ["invalid_json_response"]


def test_google_search_spider_falls_back_to_relaxed_bing_query_when_primary_finds_no_urls() -> None:
    spider = GoogleSearchSpider(
        id_query="query-fallback-1",
        subject="bolsonaro",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        """
        {
            "results": [
                {"url": "https://example.com/ignore"}
            ]
        }
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    assert len(results) == 1
    fallback_request = results[0]
    assert isinstance(fallback_request, Request)
    assert spider.items_found == 0
    assert spider.search_attempt == 2
    assert fallback_request.meta["page_number"] == 1
    assert fallback_request.meta["search_attempt"] == 2
    assert "format=json" in fallback_request.url
    assert "engines=bing" in fallback_request.url
    assert "facebook.com+%22bolsonaro%22" in fallback_request.url
    assert "site%3Afacebook.com" not in fallback_request.url
    assert spider.crawler.stats.values["face/search_empty_page_count"] == 1
    assert spider.crawler.stats.values["face/search_bing_fallback_count"] == 1


def test_google_search_spider_only_falls_back_after_primary_pagination_exhausts() -> None:
    spider = GoogleSearchSpider(
        id_query="query-fallback-pages-1",
        subject="bolsonaro",
        query_source="api",
        max_pages=2,
        user_agents=["test-agent"],
        results_per_page=2,
    )
    spider.crawler = build_crawler_stub()
    first_request = next(spider.start_requests())
    first_response = make_json_response(
        first_request.url,
        """
        {
            "results": [
                {"url": "https://example.com/ignore-1"},
                {"url": "https://example.com/ignore-2"}
            ]
        }
        """,
        request=first_request,
    )

    first_results = list(spider.parse_search(first_response))

    assert len(first_results) == 1
    second_primary_request = first_results[0]
    assert isinstance(second_primary_request, Request)
    assert second_primary_request.meta["page_number"] == 2
    assert second_primary_request.meta["search_attempt"] == 1
    assert "site%3Afacebook.com" in second_primary_request.url

    second_primary_response = make_json_response(
        second_primary_request.url,
        """
        {
            "results": [
                {"url": "https://example.com/ignore-3"}
            ]
        }
        """,
        request=second_primary_request,
    )

    second_results = list(spider.parse_search(second_primary_response))

    assert len(second_results) == 1
    fallback_request = second_results[0]
    assert isinstance(fallback_request, Request)
    assert fallback_request.meta["search_attempt"] == 2
    assert "engines=bing" in fallback_request.url
    assert "facebook.com+%22bolsonaro%22" in fallback_request.url
    assert spider.crawler.stats.values["face/search_empty_page_count"] == 2
    assert spider.crawler.stats.values["face/search_bing_fallback_count"] == 1


def test_google_search_spider_paginates_normally_during_fallback_attempt() -> None:
    spider = GoogleSearchSpider(
        id_query="query-fallback-pages-2",
        subject="bolsonaro",
        query_source="api",
        max_pages=2,
        user_agents=["test-agent"],
        results_per_page=1,
    )
    spider.search_attempt = 2
    spider.crawler = build_crawler_stub()
    fallback_request = spider._build_search_request(
        spider._build_search_url(page_number=1, search_attempt=2),
        page_number=1,
        search_attempt=2,
    )
    fallback_response = make_json_response(
        fallback_request.url,
        """
        {
            "results": [
                {"url": "https://www.facebook.com/foo/posts/123?ref=watch"}
            ]
        }
        """,
        request=fallback_request,
    )

    results = list(spider.parse_search(fallback_response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]
    requests = [result for result in results if isinstance(result, Request)]

    assert len(items) == 1
    assert spider.items_found == 1
    assert len(requests) == 1
    next_request = requests[0]
    assert next_request.meta["page_number"] == 2
    assert next_request.meta["search_attempt"] == 2
    assert "engines=bing" in next_request.url
    assert "facebook.com+%22bolsonaro%22" in next_request.url


def test_google_search_spider_retries_http_error_before_marking_blocked() -> None:
    spider = GoogleSearchSpider(
        id_query="query-retry-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
        max_block_retries=1,
    )
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        '{"error":"temporary"}',
        request=request,
        status=503,
    )

    results = list(spider.parse_search(response))

    assert len(results) == 1
    retry_request = results[0]
    assert isinstance(retry_request, Request)
    assert retry_request.dont_filter is True
    assert retry_request.meta["search_retry_count"] == 1
    assert spider.search_blocked_details is None
    assert spider.crawler.stats.values["face/search_block_retry_count"] == 1


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
        discovered_via="searxng",
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
    assert [message[0] for message in publisher.messages] == [
        QueueNames().job_events,
        QueueNames().url_discovered,
        QueueNames().enrich_request,
        QueueNames().job_events,
        QueueNames().job_events,
    ]
    assert publisher.messages[1][1]["facebook_url"] == "https://www.facebook.com/foo/posts/123"
    assert publisher.messages[2][1]["facebook_url"] == "https://www.facebook.com/foo/posts/123"
    assert publisher.messages[2][1]["query_source"] == "api"
    assert publisher.messages[3][1]["event_type"] == "search.url_discovered"
    assert publisher.messages[4][1]["event_type"] == "search.completed"


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
    job_repository.update_job_status(
        id_query="search-job-empty-1",
        status_current="search_requested",
    )

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
    assert [message[0] for message in spider.publisher.messages] == [
        QueueNames().job_events,
        QueueNames().job_events,
    ]
    assert spider.publisher.messages[1][1]["event_type"] == "search.completed"


def test_pipelines_mark_blocked_search_when_backend_fails(tmp_path) -> None:
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
        "marker_types": ["http_status_503"],
        "response_url": "http://searxng:8080/search?q=tema",
        "title": None,
        "page_number": 1,
        "anchor_count": 0,
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
    assert events[1].payload["marker_types"] == ["http_status_503"]
    assert events[1].payload["pages_visited"] == 1
    assert [message[0] for message in spider.publisher.messages] == [
        QueueNames().job_events,
        QueueNames().job_events,
    ]
    assert spider.publisher.messages[1][1]["event_type"] == "search.blocked"
