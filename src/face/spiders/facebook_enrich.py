from __future__ import annotations

import scrapy
from common.logging import get_logger
from scrapy_playwright.page import PageMethod

from face.items import FacebookRecordItem
from face.middlewares.stealth import apply_stealth_patch
from face.url_utils import normalize_url

logger = get_logger(__name__)


class FacebookEnrichSpider(scrapy.Spider):
    name = "facebook_enrich"

    def __init__(
        self,
        *,
        id_query: str,
        facebook_url: str,
        category: str,
        query_source: str = "api",
        record_id: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.id_query = id_query
        self.facebook_url = facebook_url
        self.category = category
        self.query_source = query_source
        self.record_id = record_id

    def start_requests(self):  # type: ignore[no-untyped-def]
        logger.info(
            "Starting Facebook enrich spider",
            extra={"service": "face-enrich-spider", "id_query": self.id_query},
        )
        yield scrapy.Request(
            self.facebook_url,
            callback=self.parse_facebook,
            meta={
                "playwright": True,
                "playwright_context": "authenticated",
                "playwright_page_init_callback": apply_stealth_patch,
                "playwright_include_page": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                ],
            },
        )

    async def parse_facebook(self, response):  # type: ignore[no-untyped-def]
        page = response.meta.get("playwright_page")
        normalized_url = normalize_url(
            response.css("link[rel='canonical']::attr(href)").get() or response.url
        ) or response.url
        title = response.css("title::text").get()
        description = response.css(
            "meta[property='og:description']::attr(content), "
            "meta[name='description']::attr(content)"
        ).get()

        try:
            yield FacebookRecordItem(
                item_type="facebook_record",
                id_query=self.id_query,
                url=response.url,
                url_normalized=normalized_url,
                category=self.category,
                query_source=self.query_source,
                record_id=self.record_id,
                status="enriched",
                payload={
                    "title": title,
                    "description": description,
                    "final_url": response.url,
                },
                last_error=None,
            )
        finally:
            if page is not None:
                await page.close()


if __name__ == "__main__":
    print("FacebookEnrichSpider ready")
