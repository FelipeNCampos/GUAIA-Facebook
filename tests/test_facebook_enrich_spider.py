from __future__ import annotations

import asyncio

from db.base import Base
from db.models import FaceRecord
from face.items import FacebookRecordItem
from face.middlewares.stealth import STEALTH_SCRIPT, apply_stealth_patch
from face.pipelines.persist import PersistPipeline
from face.repository import FaceRecordRepository
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
