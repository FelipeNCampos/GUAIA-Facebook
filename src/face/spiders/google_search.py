from __future__ import annotations

import json
import random
from typing import Any

import scrapy
from common.logging import get_logger

from face.config import get_settings
from face.items import FacebookURLItem
from face.url_classifier import classify_url
from face.url_utils import (
    build_search_query,
    build_searxng_search_url,
    normalize_url,
    strip_tracking_params,
)

logger = get_logger(__name__)
settings = get_settings()
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
    handle_httpstatus_list = [400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504]
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_MAX_DELAY": settings.search_autothrottle_max_delay,
        "AUTOTHROTTLE_START_DELAY": settings.search_autothrottle_start_delay,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": settings.search_autothrottle_target_concurrency,
        "COOKIES_ENABLED": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": (
            settings.search_concurrent_requests_per_domain
        ),
        "DOWNLOAD_DELAY": settings.search_download_delay,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
    }

    def __init__(
        self,
        *,
        id_query: str,
        subject: str,
        query_source: str = "api",
        max_pages: int | None = None,
        search_url_override: str | None = None,
        user_agents: list[str] | None = None,
        search_language: str | None = None,
        search_region: str | None = None,
        search_category: str | None = None,
        enabled_engines: str | None = None,
        safe_search: int | None = None,
        results_per_page: int | None = None,
        max_block_retries: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.id_query = id_query
        self.subject = subject
        self.query_source = query_source
        self.max_pages = int(settings.search_max_pages if max_pages is None else max_pages)
        self.search_url_override = search_url_override
        self.user_agents = user_agents or DEFAULT_USER_AGENTS
        self.search_language = search_language or settings.searxng_search_language
        self.search_region = search_region or settings.searxng_search_region
        self.search_category = search_category or settings.searxng_search_category
        self.enabled_engines = enabled_engines or settings.searxng_enabled_engines
        self.safe_search = (
            settings.searxng_safe_search if safe_search is None else int(safe_search)
        )
        self.results_per_page = int(results_per_page or settings.searxng_results_per_page)
        self.searxng_internal_url = settings.searxng_internal_url
        self.max_block_retries = int(
            settings.search_block_retry_limit
            if max_block_retries is None
            else max_block_retries
        )
        self.page_count = 0
        self.seen_urls: set[str] = set()
        self.search_blocked_details: dict[str, Any] | None = None
        self.search_attempt = 1
        self.items_found = 0

    def start_requests(self):  # type: ignore[no-untyped-def]
        search_url = self._build_search_url(page_number=1, search_attempt=self.search_attempt)
        logger.info(
            "Starting SearXNG-backed search spider",
            extra={"service": "face-search-spider", "id_query": self.id_query},
        )
        yield self._build_search_request(
            search_url,
            page_number=1,
            search_attempt=self.search_attempt,
        )

    def parse_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        retry_count = int(response.meta.get("search_retry_count", 0))
        current_attempt = int(response.meta.get("search_attempt", self.search_attempt))
        self.page_count = max(self.page_count, page_number)
        blocked_details = self._detect_search_error(response)
        if blocked_details is not None:
            retry_request = self._build_retry_request(
                response=response,
                page_number=page_number,
                retry_count=retry_count,
                search_attempt=current_attempt,
                blocked_details=blocked_details,
            )
            if retry_request is not None:
                yield retry_request
                return
            self._mark_search_blocked(blocked_details=blocked_details, retry_count=retry_count)
            return

        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            self._mark_search_blocked(
                blocked_details={
                    "marker_types": ["invalid_json_response"],
                    "response_url": response.url,
                    "title": None,
                    "page_number": page_number,
                    "anchor_count": 0,
                },
                retry_count=retry_count,
            )
            return

        results = payload.get("results", []) if isinstance(payload, dict) else []
        yielded_any = False
        search_position = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            raw_url = result.get("url")
            if not raw_url:
                continue
            yielded = list(
                self._yield_discovered_url(
                    raw_url=raw_url,
                    page_number=page_number,
                    search_position=search_position,
                    discovered_via="searxng",
                )
            )
            if yielded:
                yielded_any = True
                search_position += 1
                yield from yielded

        if not yielded_any and getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_empty_page_count", 1)

        if page_number < self.max_pages and len(results) >= self.results_per_page:
            next_page_url = self._build_search_url(
                page_number=page_number + 1,
                search_attempt=current_attempt,
            )
            yield self._build_search_request(
                next_page_url,
                page_number=page_number + 1,
                search_attempt=current_attempt,
            )
            return

        if current_attempt == 1 and self.items_found == 0:
            self.search_attempt = 2
            if getattr(self, "crawler", None) is not None:
                self.crawler.stats.inc_value("face/search_bing_fallback_count", 1)
            logger.info(
                "No results in primary query, falling back to relaxed query",
                extra={"service": "face-search-spider", "id_query": self.id_query},
            )
            fallback_url = self._build_search_url(page_number=1, search_attempt=2)
            yield self._build_search_request(
                fallback_url,
                page_number=1,
                search_attempt=2,
            )

    def _yield_discovered_url(
        self,
        *,
        raw_url: str,
        page_number: int,
        search_position: int,
        discovered_via: str | None = None,
    ):  # type: ignore[no-untyped-def]
        normalized = normalize_url(raw_url)
        if normalized is None or normalized in self.seen_urls:
            return

        self.seen_urls.add(normalized)
        self.items_found += 1
        category_url = strip_tracking_params(normalized) or normalized
        category = classify_url(category_url)
        yield FacebookURLItem(
            item_type="facebook_url",
            id_query=self.id_query,
            url=raw_url,
            url_normalized=normalized,
            category=category,
            query_source=self.query_source,
            search_page=page_number,
            search_position=search_position + 1,
            source_query=self.subject,
            discovered_via=discovered_via or self._discovery_source(),
        )

    def _discovery_source(self) -> str:
        return "searxng"

    def _build_search_request(
        self,
        url: str,
        *,
        page_number: int,
        search_attempt: int,
        retry_count: int = 0,
    ) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_search,
            headers=self._build_search_headers(page_number=page_number),
            dont_filter=retry_count > 0,
            meta={
                "page_number": page_number,
                "search_attempt": search_attempt,
                "search_retry_count": retry_count,
                "download_slot": "searxng_search_json",
            },
        )

    def _build_retry_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        retry_count: int,
        search_attempt: int,
        blocked_details: dict[str, Any],
    ) -> scrapy.Request | None:
        if retry_count >= self.max_block_retries:
            return None

        next_retry_count = retry_count + 1
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_block_retry_count", 1)
        logger.info(
            "Search backend returned a retryable response",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "page_number": page_number,
                "retry_count": next_retry_count,
                "marker_types": blocked_details["marker_types"],
            },
        )
        return self._build_search_request(
            response.request.url,
            page_number=page_number,
            search_attempt=search_attempt,
            retry_count=next_retry_count,
        )

    def _build_query_string(self, *, search_attempt: int | None = None) -> str:
        attempt = self.search_attempt if search_attempt is None else search_attempt
        if attempt == 1:
            return build_search_query(self.subject)
        return f'facebook.com "{self.subject}"'

    def _build_search_url(
        self,
        *,
        page_number: int,
        search_attempt: int | None = None,
    ) -> str:
        attempt = self.search_attempt if search_attempt is None else search_attempt
        if attempt == 1 and self.search_url_override and page_number == 1:
            return self.search_url_override

        return build_searxng_search_url(
            base_url=self.searxng_internal_url,
            subject=self._build_query_string(search_attempt=attempt),
            language=self.search_language,
            region=self.search_region,
            category=self.search_category,
            enabled_engines=self.enabled_engines if attempt == 1 else "bing",
            safe_search=self.safe_search,
            results_per_page=self.results_per_page,
            page_number=page_number,
        )

    def _build_search_headers(
        self,
        *,
        page_number: int,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{self.search_language},pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "DNT": "1",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "X-Requested-With": "GUIAI-Facebook",
            "User-Agent": random.choice(self.user_agents),
        }
        return headers

    def _detect_search_error(
        self,
        response,  # type: ignore[no-untyped-def]
    ) -> dict[str, Any] | None:
        if response.status < 400:
            return None
        return {
            "marker_types": [f"http_status_{response.status}"],
            "response_url": response.url,
            "title": None,
            "page_number": int(response.meta.get("page_number", 1)),
            "anchor_count": 0,
        }

    def _mark_search_blocked(
        self,
        *,
        blocked_details: dict[str, Any],
        retry_count: int,
    ) -> None:
        self.search_blocked_details = blocked_details
        blocked_details["retry_count"] = retry_count
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.set_value("face/search_blocked", 1)
            self.crawler.stats.set_value(
                "face/search_blocked_markers",
                ",".join(blocked_details["marker_types"]),
            )
        logger.warning(
            "Search backend returned a blocked/error response",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "marker_types": blocked_details["marker_types"],
                "response_url": blocked_details["response_url"],
            },
        )


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
