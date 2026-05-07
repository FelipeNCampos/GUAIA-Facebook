from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from db.base import Base
from db.models import FaceJobEvent, FaceRecord
from face.items import FacebookRecordItem
from face.middlewares.stealth import STEALTH_SCRIPT, apply_stealth_patch
from face.pipelines.events import EventsPipeline
from face.pipelines.persist import PersistPipeline
from face.queues import QueueNames
from face.repository import FaceJobRepository, FaceRecordRepository
from face.spiders.facebook_enrich import FacebookEnrichSpider
from scrapy import Request
from scrapy.http import HtmlResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class FakePlaywrightPage:
    def __init__(self) -> None:
        self.closed = False
        self.init_scripts: list[str] = []

    async def close(self) -> None:
        self.closed = True

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def publish_json(self, queue_name: str, payload: dict[str, object]) -> None:
        self.messages.append((queue_name, payload))


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


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_html_response(url: str, html: str, request: Request) -> HtmlResponse:
    return HtmlResponse(url=url, request=request, body=html.encode("utf-8"), encoding="utf-8")


async def collect_async_items(async_iterable) -> list[object]:  # type: ignore[no-untyped-def]
    return [item async for item in async_iterable]


def test_facebook_enrich_spider_start_requests_uses_playwright_authenticated_context() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-1",
        facebook_url="https://www.facebook.com/foo/posts/123",
        category="post",
        query_source="api",
        record_id=7,
    )

    request = next(spider.start_requests())

    assert request.url == "https://www.facebook.com/foo/posts/123"
    assert request.meta["playwright"] is True
    assert request.meta["playwright_context"] == "authenticated"
    assert request.meta["playwright_page_init_callback"] is apply_stealth_patch
    assert request.meta["playwright_include_page"] is True
    assert request.meta["playwright_page_methods"][1].method == "wait_for_selector"
    assert request.callback == spider.parse_facebook


def test_apply_stealth_patch_injects_script_into_playwright_page() -> None:
    page = FakePlaywrightPage()

    asyncio.run(apply_stealth_patch(page, request=None))

    assert page.init_scripts == [STEALTH_SCRIPT]


def test_facebook_enrich_spider_parses_page_and_closes_playwright_page() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-1",
        facebook_url="https://www.facebook.com/foo/posts/123",
        category="post",
        query_source="api",
        record_id=7,
    )
    fake_page = FakePlaywrightPage()
    request = Request(
        url="https://www.facebook.com/foo/posts/123",
        meta={"playwright_page": fake_page},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head>
                <title>Post title</title>
                <meta property="og:description" content="Post description" />
                <link rel="canonical" href="https://www.facebook.com/foo/posts/123?ref=share" />
            </head>
            <body></body>
        </html>
        """,
        request=request,
    )

    results = asyncio.run(collect_async_items(spider.parse_facebook(response)))

    assert fake_page.closed is True
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, FacebookRecordItem)
    assert item["item_type"] == "facebook_record"
    assert item["status"] == "enriched"
    assert item["url_normalized"] == "https://www.facebook.com/foo/posts/123"
    assert item["payload"]["title"] == "Post title"
    assert item["payload"]["description"] == "Post description"


def test_facebook_enrich_spider_extracts_additional_video_metadata() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-2",
        facebook_url="https://www.facebook.com/GloboNews/videos/584418789465746",
        category="video",
        query_source="api",
        record_id=8,
    )
    fake_page = FakePlaywrightPage()
    request = Request(
        url="https://www.facebook.com/GloboNews/videos/584418789465746",
        meta={"playwright_page": fake_page},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head>
                <title>Bolsonaro</title>
                <meta property="og:description" content="Descricao do video" />
                <link
                    rel="canonical"
                    href="https://www.facebook.com/GloboNews/videos/584418789465746?locale=pt_BR"
                />
            </head>
            <body>
                <div data-video-id="584418789465746"></div>
                <div>
                    <div>340</div>
                    <div>217 comentarios</div>
                    <div>11 mil visualizacoes</div>
                </div>
                <div data-ad-rendering-role="profile_name">
                    <a href="/GloboNews?__tn__=-UC">
                        <span>GloboNews</span>
                    </a>
                    <svg title="Conta verificada"></svg>
                </div>
                <a
                    aria-label="7 de outubro de 2021"
                    href="https://www.facebook.com/GloboNews/videos/584418789465746/"
                >
                    7 de outubro de 2021
                </a>
            </body>
        </html>
        """,
        request=request,
    )

    results = asyncio.run(collect_async_items(spider.parse_facebook(response)))

    assert fake_page.closed is True
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, FacebookRecordItem)
    assert item["payload"]["profile_name"] == "GloboNews"
    assert item["payload"]["profile_url"] == "https://www.facebook.com/GloboNews"
    assert item["payload"]["is_verified"] is True
    assert item["payload"]["original_category"] == "video"
    assert item["payload"]["detected_category"] == "video"
    assert item["payload"]["published_at_text"] == "7 de outubro de 2021"
    assert item["payload"]["published_at_iso"] == "2021-10-07"
    assert item["payload"]["reaction_count"] == 340
    assert item["payload"]["comment_count"] == 217
    assert item["payload"]["view_count"] == 11000
    assert item["payload"]["video_id"] == "584418789465746"


def test_facebook_enrich_spider_extracts_relative_date_and_scoped_engagement() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-3",
        facebook_url="https://www.facebook.com/jovempannews/videos/836934785519611",
        category="video",
        query_source="api",
        record_id=9,
    )
    fake_page = FakePlaywrightPage()
    request = Request(
        url="https://www.facebook.com/jovempannews/videos/836934785519611",
        meta={"playwright_page": fake_page},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head>
                <title>Video real</title>
                <link
                    rel="canonical"
                    href="https://www.facebook.com/jovempannews/videos/836934785519611?locale=pt_BR"
                />
            </head>
            <body>
                <div class="related">
                    <span>999 comentarios</span>
                    <span>200 mil visualizacoes</span>
                </div>
                <div class="watch-permalink">
                    <div data-video-id="836934785519611"></div>
                    <div data-ad-rendering-role="profile_name">
                        <a href="https://www.facebook.com/jovempannews?__tn__=-UC">
                            <b><span>Jovem Pan News</span></b>
                        </a>
                        <svg title="Conta verificada"></svg>
                    </div>
                    <a
                        aria-label="5 d"
                        href="https://www.facebook.com/reel/836934785519611/?__tn__=%2CO"
                    >
                        5 d
                    </a>
                    <div aria-label="Veja quem reagiu a isso" role="toolbar">
                        <span>39</span>
                    </div>
                    <div>
                        <span>29 comentarios</span>
                        <span>3,4 mil visualizacoes</span>
                    </div>
                </div>
            </body>
        </html>
        """,
        request=request,
    )

    results = asyncio.run(collect_async_items(spider.parse_facebook(response)))

    assert fake_page.closed is True
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, FacebookRecordItem)
    assert item["payload"]["profile_name"] == "Jovem Pan News"
    assert item["payload"]["profile_url"] == "https://www.facebook.com/jovempannews"
    assert item["payload"]["is_verified"] is True
    assert item["payload"]["original_category"] == "video"
    assert item["payload"]["detected_category"] == "video"
    assert item["payload"]["published_at_text"] == "5 d"
    assert item["payload"]["published_at_iso"] == (
        datetime.now().astimezone() - timedelta(days=5)
    ).date().isoformat()
    assert item["payload"]["reaction_count"] == 39
    assert item["payload"]["comment_count"] == 29
    assert item["payload"]["view_count"] == 3400
    assert item["payload"]["video_id"] == "836934785519611"


def test_facebook_enrich_spider_extracts_reel_metadata() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-4",
        facebook_url="https://www.facebook.com/reel/3943180995975088",
        category="reel",
        query_source="api",
        record_id=10,
    )
    fake_page = FakePlaywrightPage()
    request = Request(
        url="https://www.facebook.com/reel/3943180995975088",
        meta={"playwright_page": fake_page},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head>
                <title>Reel real</title>
                <meta property="og:description" content="vamos com tudo" />
                <link rel="canonical" href="https://www.facebook.com/reel/3943180995975088" />
            </head>
            <body>
                <div data-pagelet="Reels">
                    <div data-video-id="3943180995975088"></div>
                </div>
                <div>
                    <a href="/profile.php?id=61588228981562&amp;sk=reels_tab">
                        Aliança pelo capitão
                    </a>
                    <div aria-label="Curtir" role="button"></div>
                    <div><span>29,5 mil</span></div>
                    <div aria-label="Comentar" role="button"></div>
                    <div><span>7,9 mil</span></div>
                    <div aria-label="Compartilhar" role="button"></div>
                    <div><span>3,2 mil</span></div>
                </div>
            </body>
        </html>
        """,
        request=request,
    )

    results = asyncio.run(collect_async_items(spider.parse_facebook(response)))

    assert fake_page.closed is True
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, FacebookRecordItem)
    assert item["payload"]["profile_name"] == "Aliança pelo capitão"
    assert item["payload"]["profile_url"] == "https://www.facebook.com/profile.php?id=61588228981562&sk=reels_tab"
    assert item["payload"]["original_category"] == "reel"
    assert item["payload"]["detected_category"] == "reel"
    assert item["payload"]["reaction_count"] == 29500
    assert item["payload"]["comment_count"] == 7900
    assert item["payload"]["view_count"] is None
    assert item["payload"]["video_id"] == "3943180995975088"


def test_facebook_enrich_spider_reclassifies_and_extracts_page_metadata() -> None:
    spider = FacebookEnrichSpider(
        id_query="query-5",
        facebook_url="https://www.facebook.com/jovempannews/videos/123?locale=pt_BR",
        category="video",
        query_source="api",
        record_id=11,
    )
    fake_page = FakePlaywrightPage()
    request = Request(
        url="https://www.facebook.com/jovempannews/videos/123?locale=pt_BR",
        meta={"playwright_page": fake_page},
    )
    response = make_html_response(
        request.url,
        """
        <html>
            <head>
                <title>Jovem Pan News</title>
                <link
                    rel="canonical"
                    href="https://www.facebook.com/jovempannews?locale=pt_BR&amp;__tn__=-UC"
                />
            </head>
            <body>
                <div role="banner">
                    <h1>Jovem Pan News</h1>
                </div>
                <div role="main">
                    <div role="tablist">
                        <button role="tab">Sobre</button>
                        <button role="tab">Vídeos</button>
                    </div>
                    <div>Empresa de mídia/notícias</div>
                    <div>3,4 mil seguidores</div>
                    <div>120 seguindo</div>
                    <div>O melhor resumo do dia em política e notícias do Brasil.</div>
                    <a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fjovempan.com.br%2Fnoticias&amp;h=abc">
                        Site oficial
                    </a>
                </div>
            </body>
        </html>
        """,
        request=request,
    )

    results = asyncio.run(collect_async_items(spider.parse_facebook(response)))

    assert fake_page.closed is True
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, FacebookRecordItem)
    assert item["category"] == "page"
    assert item["url_normalized"] == "https://www.facebook.com/jovempannews"
    assert item["payload"]["original_category"] == "video"
    assert item["payload"]["detected_category"] == "page"
    assert item["payload"]["profile_name"] == "Jovem Pan News"
    assert item["payload"]["profile_url"] == "https://www.facebook.com/jovempannews"
    assert item["payload"]["page_category"] == "Empresa de mídia/notícias"
    assert item["payload"]["bio"] == "O melhor resumo do dia em política e notícias do Brasil."
    assert item["payload"]["follower_count"] == 3400
    assert item["payload"]["following_count"] == 120
    assert item["payload"]["external_links"] == ["https://jovempan.com.br/noticias"]


def test_persist_pipeline_updates_record_with_enriched_payload(tmp_path) -> None:
    db_path = tmp_path / "enrich_pipeline.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    repository = FaceRecordRepository(session_factory)
    existing_record = repository.upsert_discovered_record(
        id_query="query-1",
        url="https://www.facebook.com/foo/posts/123",
        url_normalized="https://www.facebook.com/foo/posts/123",
        category="post",
        payload={"search_page": 1},
    )

    spider = FacebookEnrichSpider(
        id_query="query-1",
        facebook_url="https://www.facebook.com/foo/posts/123",
        category="post",
        query_source="api",
        record_id=existing_record.id,
    )
    spider.record_repository = repository

    pipeline = PersistPipeline()
    pipeline.open_spider(spider)

    item = FacebookRecordItem(
        item_type="facebook_record",
        id_query="query-1",
        url="https://www.facebook.com/foo/posts/123",
        url_normalized="https://www.facebook.com/foo/posts/123",
        category="post",
        query_source="api",
        record_id=existing_record.id,
        status="enriched",
        payload={"title": "Enriched title"},
        last_error=None,
    )

    persisted = pipeline.process_item(item, spider)

    with session_factory() as session:
        record = session.query(FaceRecord).filter(FaceRecord.id == existing_record.id).one()

    assert persisted["record_id"] == existing_record.id
    assert record.status == "enriched"
    assert record.payload == {"title": "Enriched title"}


def test_events_pipeline_publishes_enrich_completed_event(tmp_path) -> None:
    db_path = tmp_path / "enrich_events.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    job_repository = FaceJobRepository(session_factory)
    record_repository = FaceRecordRepository(session_factory)
    publisher = FakePublisher()

    job_repository.create_job(
        id_query="enrich-job-1",
        subject="tema",
        query_source="api",
        start_date=None,
        end_date=None,
    )

    spider = FacebookEnrichSpider(
        id_query="enrich-job-1",
        facebook_url="https://www.facebook.com/reel/3943180995975088",
        category="reel",
        query_source="api",
        record_id=None,
    )
    spider.record_repository = record_repository
    spider.job_repository = job_repository
    spider.publisher = publisher
    spider.crawler = build_crawler_stub()

    persist_pipeline = PersistPipeline()
    events_pipeline = EventsPipeline()
    persist_pipeline.open_spider(spider)
    events_pipeline.open_spider(spider)

    item = FacebookRecordItem(
        item_type="facebook_record",
        id_query="enrich-job-1",
        url="https://www.facebook.com/reel/3943180995975088",
        url_normalized="https://www.facebook.com/reel/3943180995975088",
        category="reel",
        query_source="api",
        record_id=None,
        status="enriched",
        payload={"title": "Reel real"},
        last_error=None,
    )

    persisted_item = persist_pipeline.process_item(item, spider)
    events_pipeline.process_item(persisted_item, spider)

    with session_factory() as session:
        events = (
            session.query(FaceJobEvent)
            .filter(FaceJobEvent.id_query == "enrich-job-1")
            .order_by(FaceJobEvent.id.asc())
            .all()
        )

    assert [event.event_type for event in events] == ["enrich.completed"]
    assert publisher.messages[0][0] == QueueNames().job_events
    assert publisher.messages[0][1]["event_type"] == "enrich.completed"
    assert publisher.messages[0][1]["payload"]["status"] == "enriched"
