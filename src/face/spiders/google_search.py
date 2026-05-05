from __future__ import annotations

import random

import scrapy
from common.logging import get_logger

from face.items import FacebookURLItem
from face.url_classifier import classify_url
from face.url_utils import build_google_search_url, normalize_url

logger = get_logger(__name__)
DEFAULT_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
]


class GoogleSearchSpider(scrapy.Spider):
    name = "google_search"

    def __init__(
        self,
        *,
        id_query: str,
        subject: str,
        query_source: str = "api",
        start_date: str | None = None,
        end_date: str | None = None,
        max_pages: int = 5,
        user_agents: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.id_query = id_query
        self.subject = subject
        self.query_source = query_source
        self.start_date = start_date
        self.end_date = end_date
        self.max_pages = int(max_pages)
        self.user_agents = user_agents or DEFAULT_USER_AGENTS
        self.page_count = 0
        self.seen_urls: set[str] = set()

    def start_requests(self):  # type: ignore[no-untyped-def]
        google_url = build_google_search_url(
            subject=self.subject,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        logger.info(
            "Starting Google search spider",
            extra={"service": "face-search-spider", "id_query": self.id_query},
        )
        yield scrapy.Request(
            google_url,
            callback=self.parse_search,
            headers={"User-Agent": random.choice(self.user_agents)},
            meta={"page_number": 1},
        )

    def parse_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        self.page_count = max(self.page_count, page_number)

        raw_urls = response.css("a::attr(href)").getall()
        search_position = 0
        for raw_url in raw_urls:
            normalized = normalize_url(raw_url)
            if normalized is None or normalized in self.seen_urls:
                continue

            self.seen_urls.add(normalized)
            search_position += 1
            category = classify_url(normalized)
            yield FacebookURLItem(
                item_type="facebook_url",
                id_query=self.id_query,
                url=raw_url,
                url_normalized=normalized,
                category=category,
                query_source=self.query_source,
                search_page=page_number,
                search_position=search_position,
                source_query=self.subject,
                discovered_via="google_search",
            )

        next_page = response.css("a#pnnext::attr(href)").get()
        if next_page and page_number < self.max_pages:
            yield response.follow(
                next_page,
                callback=self.parse_search,
                headers={"User-Agent": random.choice(self.user_agents)},
                meta={"page_number": page_number + 1},
            )


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
