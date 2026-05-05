from __future__ import annotations

import random
from typing import Any

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
GOOGLE_BLOCK_TEXT_MARKERS = (
    "our systems have detected unusual traffic",
    "detected unusual traffic",
    "before you continue to google search",
)
GOOGLE_BLOCK_HREF_PREFIXES = (
    "/httpservice/retry/enablejs",
    "https://consent.google.com",
    "https://www.google.com/sorry/",
)


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
        search_url_override: str | None = None,
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
        self.search_url_override = search_url_override
        self.user_agents = user_agents or DEFAULT_USER_AGENTS
        self.page_count = 0
        self.seen_urls: set[str] = set()
        self.search_blocked_details: dict[str, Any] | None = None

    def start_requests(self):  # type: ignore[no-untyped-def]
        google_url = self.search_url_override or build_google_search_url(
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
        blocked_details = self._detect_google_block(response, raw_urls)
        if blocked_details is not None:
            self.search_blocked_details = blocked_details
            if getattr(self, "crawler", None) is not None:
                self.crawler.stats.set_value("face/search_blocked", 1)
                self.crawler.stats.set_value(
                    "face/search_blocked_markers",
                    ",".join(blocked_details["marker_types"]),
                )
            logger.warning(
                "Google search returned a blocked/challenge page",
                extra={
                    "service": "face-search-spider",
                    "id_query": self.id_query,
                    "marker_types": blocked_details["marker_types"],
                    "response_url": blocked_details["response_url"],
                },
            )
            return

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

    def _detect_google_block(
        self,
        response,  # type: ignore[no-untyped-def]
        raw_urls: list[str],
    ) -> dict[str, Any] | None:
        marker_types: list[str] = []
        response_url = response.url.lower()
        response_text = response.text.lower()
        title = response.css("title::text").get()

        if "/sorry/" in response_url:
            marker_types.append("google_sorry")

        if any(marker in response_text for marker in GOOGLE_BLOCK_TEXT_MARKERS):
            if "detected unusual traffic" in response_text:
                marker_types.append("unusual_traffic")
            if "before you continue to google search" in response_text:
                marker_types.append("consent_interstitial")

        if any(
            raw_url.startswith(prefix)
            for raw_url in raw_urls
            for prefix in GOOGLE_BLOCK_HREF_PREFIXES
        ):
            if any(raw_url.startswith("/httpservice/retry/enablejs") for raw_url in raw_urls):
                marker_types.append("enablejs_challenge")
            if any(raw_url.startswith("https://consent.google.com") for raw_url in raw_urls):
                marker_types.append("consent_interstitial")
            if any(raw_url.startswith("https://www.google.com/sorry/") for raw_url in raw_urls):
                marker_types.append("google_sorry")

        if not marker_types:
            return None

        deduped_markers = sorted(set(marker_types))
        return {
            "marker_types": deduped_markers,
            "response_url": response.url,
            "title": title,
            "page_number": int(response.meta.get("page_number", 1)),
            "anchor_count": len(raw_urls),
        }


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
