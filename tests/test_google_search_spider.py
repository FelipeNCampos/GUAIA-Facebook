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
from scrapy.http import HtmlResponse, TextResponse
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


class BrowserFailureStub:
    def __init__(self, request: Request, error: Exception) -> None:
        self.request = request
        self.value = error

    def getErrorMessage(self) -> str:
        return str(self.value)


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
    assert requests[0].headers["Referer"].decode("utf-8") == request.url


def test_google_search_spider_extracts_facebook_urls_from_rendered_text_when_anchors_fail() -> None:
    spider = GoogleSearchSpider(
        id_query="query-text-fallback-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_html_response(
        request.url,
        """
        <html>
            <body>
                <script>
                    window.__DATA__ = {
                        "link": "https:\\/\\/m.facebook.com\\/foo\\/posts\\/123\\/?ref=watch"
                    };
                </script>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]

    assert len(items) == 1
    assert items[0]["url_normalized"] == "https://www.facebook.com/foo/posts/123"


def test_google_search_spider_uses_browser_like_headers_and_consent_cookie() -> None:
    spider = GoogleSearchSpider(
        id_query="query-headers-1",
        subject="tema",
        query_source="api",
        user_agents=["test-agent"],
        search_language="pt-BR",
        google_consent_cookie="YES+test",
    )

    request = next(spider.start_requests())

    assert request.headers["User-Agent"].decode("utf-8") == "test-agent"
    assert request.headers["Accept-Language"].decode("utf-8").startswith("pt-BR")
    assert request.headers["Sec-Fetch-Site"].decode("utf-8") == "none"
    assert request.cookies["CONSENT"] == "YES+test"
    assert request.meta["google_block_retry_count"] == 0


def test_google_search_spider_prefers_custom_search_api_when_configured() -> None:
    spider = GoogleSearchSpider(
        id_query="query-api-1",
        subject="tema",
        query_source="api",
        google_search_provider="auto",
        google_search_api_key="api-key",
        google_search_engine_id="engine-id",
    )

    request = next(spider.start_requests())

    assert request.url.startswith("https://customsearch.googleapis.com/customsearch/v1?")
    assert "key=api-key" in request.url
    assert "cx=engine-id" in request.url
    assert request.callback == spider.parse_custom_search
    assert request.meta["download_slot"] == "google_custom_search_api"


def test_google_search_spider_parses_custom_search_json_results() -> None:
    spider = GoogleSearchSpider(
        id_query="query-api-results-1",
        subject="tema",
        query_source="api",
        google_search_provider="api",
        google_search_api_key="api-key",
        google_search_engine_id="engine-id",
        max_pages=2,
    )
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        """
        {
            "items": [
                {"link": "https://www.facebook.com/foo/posts/123?ref=watch"},
                {"link": "https://example.com/ignore"}
            ],
            "queries": {
                "nextPage": [{"startIndex": 11}]
            }
        }
        """,
        request=request,
    )

    results = list(spider.parse_custom_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]
    requests = [result for result in results if isinstance(result, Request)]

    assert len(items) == 1
    assert items[0]["url_normalized"] == "https://www.facebook.com/foo/posts/123"
    assert items[0]["discovered_via"] == "google_custom_search_api"
    assert len(requests) == 1
    assert requests[0].meta["start_index"] == 11


def test_google_search_spider_retries_custom_search_api_rate_limit() -> None:
    spider = GoogleSearchSpider(
        id_query="query-api-retry-1",
        subject="tema",
        query_source="api",
        google_search_provider="api",
        google_search_api_key="api-key",
        google_search_engine_id="engine-id",
        max_block_retries=1,
    )
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_json_response(
        request.url,
        """
        {
            "error": {
                "code": 429,
                "errors": [{"reason": "rateLimitExceeded"}]
            }
        }
        """,
        request=request,
        status=429,
    )

    results = list(spider.parse_custom_search(response))

    assert len(results) == 1
    retry_request = results[0]
    assert isinstance(retry_request, Request)
    assert retry_request.dont_filter is True
    assert retry_request.meta["google_block_retry_count"] == 1
    assert spider.crawler.stats.values["face/search_api_retry_count"] == 1


def test_google_search_spider_retries_blocked_page_before_marking_blocked() -> None:
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
    response = make_html_response(
        "https://www.google.com/sorry/index?continue=teste",
        """
        <html>
            <head><title>Google Search</title></head>
            <body>
                <p>Our systems have detected unusual traffic from your computer network.</p>
                <a href="/httpservice/retry/enablejs?sei=abc">Enable JS</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    assert len(results) == 1
    browser_request = results[0]
    assert isinstance(browser_request, Request)
    assert browser_request.dont_filter is True
    assert browser_request.callback == spider.parse_browser_search
    assert browser_request.errback == spider.handle_browser_search_error
    assert browser_request.meta["playwright"] is True
    assert browser_request.meta["browser_fallback_attempt"] == 1
    assert spider.search_blocked_details is None
    assert spider.crawler.stats.values["face/search_browser_fallback_count"] == 1


def test_google_search_spider_marks_blocked_after_browser_fallback_still_hits_challenge() -> None:
    spider = GoogleSearchSpider(
        id_query="query-browser-blocked-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
        max_block_retries=1,
    )
    spider.crawler = build_crawler_stub()
    request = Request(
        url="https://www.google.com/search?q=tema",
        meta={"page_number": 1, "browser_fallback_attempt": 1},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head><title>Google Search</title></head>
            <body>
                <p>Before you continue to Google Search</p>
                <a href="https://consent.google.com/somewhere">Consent</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_browser_search(response))

    assert len(results) == 1
    assert isinstance(results[0], Request)
    assert results[0].callback == spider.parse_bing_search
    assert spider.search_blocked_details is None
    assert spider.crawler.stats.values["face/search_provider_fallback_count"] == 1


def test_google_search_spider_marks_blocked_when_browser_fallback_launch_fails() -> None:
    spider = GoogleSearchSpider(
        id_query="query-browser-error-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    spider.crawler = build_crawler_stub()
    request = Request(
        url="https://www.google.com/search?q=tema",
        meta={
            "page_number": 1,
            "browser_fallback_attempt": 1,
            "google_block_retry_count": 2,
        },
    )

    results = spider.handle_browser_search_error(
        BrowserFailureStub(
            request,
            RuntimeError("Missing X server or $DISPLAY while launching Playwright"),
        )
    )

    assert len(results) == 1
    assert isinstance(results[0], Request)
    assert results[0].callback == spider.parse_bing_search
    assert spider.search_blocked_details is None
    assert spider.crawler.stats.values["face/search_provider_fallback_count"] == 1


def test_google_search_spider_detects_google_block_page() -> None:
    spider = GoogleSearchSpider(
        id_query="query-blocked-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
        google_search_provider="html",
        max_block_retries=0,
    )
    spider.browser_fallback_enabled = False
    spider.google_search_fallback_provider = ""
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
    assert spider.search_blocked_details["retry_count"] == 0
    assert spider.crawler.stats.values["face/search_blocked"] == 1


def test_google_search_spider_treats_empty_google_page_as_soft_block() -> None:
    spider = GoogleSearchSpider(
        id_query="query-empty-soft-block-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
        google_search_provider="html",
        max_block_retries=0,
    )
    spider.browser_fallback_enabled = False
    spider.google_search_fallback_provider = ""
    spider.crawler = build_crawler_stub()
    request = next(spider.start_requests())
    response = make_html_response(
        request.url,
        """
        <html>
            <head><title>Google Search</title></head>
            <body>
                <a href="/preferences">Preferences</a>
                <a href="/advanced_search">Advanced search</a>
                <a href="/history">History</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    assert results == []
    assert spider.search_blocked_details is not None
    assert spider.search_blocked_details["marker_types"] == ["empty_results_page"]


def test_google_search_spider_switches_to_bing_after_google_block() -> None:
    spider = GoogleSearchSpider(
        id_query="query-fallback-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
        google_search_provider="html",
        max_block_retries=0,
    )
    spider.browser_fallback_enabled = False
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
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_search(response))

    assert len(results) == 1
    assert isinstance(results[0], Request)
    assert results[0].callback == spider.parse_bing_search
    assert "bing.com/search" in results[0].url
    assert spider.search_blocked_details is None
    assert spider.crawler.stats.values["face/search_provider_fallback_count"] == 1


def test_google_search_spider_parses_bing_results() -> None:
    spider = GoogleSearchSpider(
        id_query="query-bing-1",
        subject="tema",
        query_source="api",
        max_pages=1,
        user_agents=["test-agent"],
    )
    request = Request(url="https://www.bing.com/search?q=tema", meta={"page_number": 1})
    response = make_html_response(
        request.url,
        """
        <html>
            <body>
                <a href="https://www.facebook.com/foo/posts/123?ref=watch">A</a>
                <a href="https://example.com/ignore">B</a>
            </body>
        </html>
        """,
        request=request,
    )

    results = list(spider.parse_bing_search(response))

    items = [result for result in results if isinstance(result, FacebookURLItem)]

    assert len(items) == 1
    assert items[0]["url_normalized"] == "https://www.facebook.com/foo/posts/123"
    assert items[0]["discovered_via"] == "bing_search"


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
    assert [message[0] for message in spider.publisher.messages] == [
        QueueNames().job_events,
        QueueNames().job_events,
    ]
    assert spider.publisher.messages[1][1]["event_type"] == "search.blocked"
