from __future__ import annotations

import json
import random
from typing import Any

import scrapy
from common.logging import get_logger
from scrapy_playwright.page import PageMethod

from face.config import get_settings
from face.items import FacebookURLItem
from face.middlewares.stealth import apply_stealth_patch
from face.url_classifier import classify_url
from face.url_utils import (
    build_bing_search_url,
    build_google_custom_search_url,
    build_google_search_url,
    extract_facebook_urls_from_text,
    normalize_url,
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
    handle_httpstatus_list = [403, 429, 500, 502, 503, 504]
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_MAX_DELAY": settings.google_search_autothrottle_max_delay,
        "AUTOTHROTTLE_START_DELAY": settings.google_search_autothrottle_start_delay,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": (
            settings.google_search_autothrottle_target_concurrency
        ),
        "COOKIES_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": (
            settings.google_search_concurrent_requests_per_domain
        ),
        "DOWNLOAD_DELAY": settings.google_search_download_delay,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
    }

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
        search_language: str | None = None,
        search_region: str | None = None,
        results_per_page: int | None = None,
        google_consent_cookie: str | None = None,
        google_search_provider: str | None = None,
        google_search_api_key: str | None = None,
        google_search_engine_id: str | None = None,
        max_block_retries: int | None = None,
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
        self.search_language = search_language or settings.google_search_language
        self.search_region = search_region or settings.google_search_region
        self.results_per_page = int(results_per_page or settings.google_search_results_per_page)
        self.google_consent_cookie = (
            google_consent_cookie or settings.google_search_consent_cookie
        )
        self.google_search_provider = (
            google_search_provider or settings.google_search_provider
        ).lower()
        self.google_search_api_key = google_search_api_key or settings.google_search_api_key
        self.google_search_engine_id = (
            google_search_engine_id or settings.google_search_engine_id
        )
        self.google_search_fallback_provider = (
            settings.google_search_fallback_provider.strip().lower()
        )
        self.browser_fallback_enabled = settings.google_search_browser_fallback_enabled
        self.browser_fallback_limit = settings.google_search_browser_fallback_limit
        self.max_block_retries = int(
            settings.google_search_block_retry_limit
            if max_block_retries is None
            else max_block_retries
        )
        self.page_count = 0
        self.seen_urls: set[str] = set()
        self.search_blocked_details: dict[str, Any] | None = None
        self.provider_fallback_attempted = False

    def start_requests(self):  # type: ignore[no-untyped-def]
        if self._should_use_custom_search_api():
            api_url = build_google_custom_search_url(
                api_key=self.google_search_api_key,
                search_engine_id=self.google_search_engine_id,
                subject=self.subject,
                start_date=self.start_date,
                end_date=self.end_date,
                language=self.search_language,
                region=self.search_region,
                results_per_page=self.results_per_page,
                start_index=1,
            )
            logger.info(
                "Starting Google search spider with official API",
                extra={"service": "face-search-spider", "id_query": self.id_query},
            )
            yield self._build_custom_search_request(api_url, page_number=1, start_index=1)
            return

        google_url = self.search_url_override or build_google_search_url(
            subject=self.subject,
            start_date=self.start_date,
            end_date=self.end_date,
            language=self.search_language,
            region=self.search_region,
            results_per_page=self.results_per_page,
        )
        logger.info(
            "Starting Google search spider",
            extra={"service": "face-search-spider", "id_query": self.id_query},
        )
        yield self._build_search_request(google_url, page_number=1)

    def parse_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        retry_count = int(response.meta.get("google_block_retry_count", 0))
        self.page_count = max(self.page_count, page_number)

        raw_urls = response.css("a::attr(href)").getall()
        blocked_details = self._detect_google_block(response, raw_urls)
        if blocked_details is not None:
            browser_fallback_request = self._build_browser_fallback_request(
                response=response,
                page_number=page_number,
                blocked_details=blocked_details,
            )
            if browser_fallback_request is not None:
                yield browser_fallback_request
                return

            retry_request = self._build_block_retry_request(
                response=response,
                page_number=page_number,
                retry_count=retry_count,
                blocked_details=blocked_details,
                referer="https://www.google.com/",
            )
            if retry_request is not None:
                yield retry_request
                return

            provider_fallback_request = self._build_provider_fallback_request(
                page_number=page_number
            )
            if provider_fallback_request is not None:
                yield provider_fallback_request
                return

            self._mark_search_blocked(blocked_details=blocked_details, retry_count=retry_count)
            return

        search_position = 0
        yielded_any = False
        for yielded_item in self._yield_discovered_items_from_response(
            response=response,
            raw_urls=raw_urls,
            page_number=page_number,
        ):
            yielded_any = True
            search_position += 1
            yield yielded_item

        if not yielded_any and getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_empty_page_count", 1)

        next_page = response.css("a#pnnext::attr(href)").get()
        if next_page and page_number < self.max_pages:
            yield self._build_search_request(
                response.urljoin(next_page),
                page_number=page_number + 1,
                referer=response.url,
            )

    def parse_browser_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        self.page_count = max(self.page_count, page_number)

        raw_urls = response.css("a::attr(href)").getall()
        blocked_details = self._detect_google_block(response, raw_urls)
        if blocked_details is not None:
            browser_attempt = int(response.meta.get("browser_fallback_attempt", 1))
            provider_fallback_request = self._build_provider_fallback_request(
                page_number=page_number
            )
            if provider_fallback_request is not None:
                yield provider_fallback_request
                return
            self._mark_search_blocked(
                blocked_details={
                    **blocked_details,
                    "browser_fallback_attempt": browser_attempt,
                    "provider": "google_browser_fallback",
                },
                retry_count=int(response.meta.get("google_block_retry_count", 0)),
            )
            return

        yielded_any = False
        search_position = 0
        for yielded_item in self._yield_discovered_items_from_response(
            response=response,
            raw_urls=raw_urls,
            page_number=page_number,
        ):
            yielded_any = True
            search_position += 1
            yield yielded_item

        if not yielded_any and getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_browser_empty_page_count", 1)

        next_page = response.css("a#pnnext::attr(href)").get()
        if next_page and page_number < self.max_pages:
            yield self._build_browser_search_request(
                response.urljoin(next_page),
                page_number=page_number + 1,
                browser_attempt=int(response.meta.get("browser_fallback_attempt", 1)),
                retry_count=int(response.meta.get("google_block_retry_count", 0)),
                referer=response.url,
            )

    def handle_browser_search_error(self, failure):  # type: ignore[no-untyped-def]
        request = getattr(failure, "request", None)
        error = getattr(failure, "value", failure)
        error_type = type(error).__name__
        error_message = (
            failure.getErrorMessage()
            if hasattr(failure, "getErrorMessage")
            else str(error)
        )

        marker_types = ["browser_fallback_failed", f"browser_{error_type.lower()}"]
        lowered_message = error_message.lower()
        if "missing x server" in lowered_message or "$display" in lowered_message:
            marker_types.append("missing_display")

        blocked_details = {
            "marker_types": sorted(set(marker_types)),
            "response_url": request.url if request is not None else None,
            "title": None,
            "page_number": int(request.meta.get("page_number", 1)) if request else 1,
            "anchor_count": 0,
            "provider": "google_browser_fallback",
            "browser_fallback_attempt": (
                int(request.meta.get("browser_fallback_attempt", 1)) if request else 1
            ),
            "error_type": error_type,
            "error_message": error_message,
        }
        provider_fallback_request = self._build_provider_fallback_request(
            page_number=int(request.meta.get("page_number", 1)) if request else 1
        )
        if provider_fallback_request is not None:
            return [provider_fallback_request]
        self._mark_search_blocked(
            blocked_details=blocked_details,
            retry_count=int(request.meta.get("google_block_retry_count", 0))
            if request
            else 0,
        )
        logger.warning(
            "Google browser fallback failed",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "response_url": blocked_details["response_url"],
                "marker_types": blocked_details["marker_types"],
                "error_type": error_type,
            },
        )
        return []

    def parse_custom_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        start_index = int(response.meta.get("start_index", 1))
        retry_count = int(response.meta.get("google_block_retry_count", 0))
        self.page_count = max(self.page_count, page_number)

        blocked_details = self._detect_custom_search_block(response)
        if blocked_details is not None:
            retry_request = self._build_api_retry_request(
                response=response,
                page_number=page_number,
                start_index=start_index,
                retry_count=retry_count,
                blocked_details=blocked_details,
            )
            if retry_request is not None:
                yield retry_request
                return

            provider_fallback_request = self._build_provider_fallback_request(
                page_number=page_number
            )
            if provider_fallback_request is not None:
                yield provider_fallback_request
                return

            self._mark_search_blocked(blocked_details=blocked_details, retry_count=retry_count)
            return

        payload = json.loads(response.text)
        search_position = 0
        for result in payload.get("items", []):
            raw_url = result.get("link")
            if not raw_url:
                continue
            yielded = list(
                self._yield_discovered_url(
                    raw_url=raw_url,
                    page_number=page_number,
                    search_position=search_position,
                )
            )
            if yielded:
                search_position += 1
                yield from yielded

        next_start_index = self._next_custom_search_start_index(payload)
        if next_start_index is not None and page_number < self.max_pages:
            next_url = build_google_custom_search_url(
                api_key=self.google_search_api_key,
                search_engine_id=self.google_search_engine_id,
                subject=self.subject,
                start_date=self.start_date,
                end_date=self.end_date,
                language=self.search_language,
                region=self.search_region,
                results_per_page=self.results_per_page,
                start_index=next_start_index,
            )
            yield self._build_custom_search_request(
                next_url,
                page_number=page_number + 1,
                start_index=next_start_index,
            )

    def parse_bing_search(self, response):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        self.page_count = max(self.page_count, page_number)

        raw_urls = response.css("a::attr(href)").getall()
        yielded_any = False
        for yielded_item in self._yield_discovered_items_from_response(
            response=response,
            raw_urls=raw_urls,
            page_number=page_number,
            discovered_via="bing_search",
        ):
            yielded_any = True
            yield yielded_item

        if not yielded_any and getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_bing_empty_page_count", 1)

        next_page = response.css("a.sb_pagN::attr(href)").get()
        if next_page and page_number < self.max_pages:
            yield self._build_bing_search_request(
                response.urljoin(next_page),
                page_number=page_number + 1,
                referer=response.url,
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
        category = classify_url(normalized)
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

    def _yield_discovered_items_from_response(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        raw_urls: list[str],
        page_number: int,
        discovered_via: str | None = None,
    ):  # type: ignore[no-untyped-def]
        search_position = 0

        for raw_url in raw_urls:
            yielded = list(
                self._yield_discovered_url(
                    raw_url=raw_url,
                    page_number=page_number,
                    search_position=search_position,
                    discovered_via=discovered_via,
                )
            )
            if yielded:
                search_position += 1
                yield from yielded

        if search_position > 0:
            return

        for extracted_url in extract_facebook_urls_from_text(response.text):
            yielded = list(
                self._yield_discovered_url(
                    raw_url=extracted_url,
                    page_number=page_number,
                    search_position=search_position,
                    discovered_via=discovered_via,
                )
            )
            if yielded:
                search_position += 1
                yield from yielded

    def _should_use_custom_search_api(self) -> bool:
        if self.search_url_override:
            return False
        if self.google_search_provider == "html":
            return False
        if self.google_search_provider == "api":
            return bool(self.google_search_api_key and self.google_search_engine_id)
        return bool(self.google_search_api_key and self.google_search_engine_id)

    def _discovery_source(self) -> str:
        if self._should_use_custom_search_api():
            return "google_custom_search_api"
        return "google_search"

    def _build_search_request(
        self,
        url: str,
        *,
        page_number: int,
        retry_count: int = 0,
        referer: str | None = None,
    ) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_search,
            headers=self._build_google_headers(page_number=page_number, referer=referer),
            cookies=self._build_google_cookies(),
            dont_filter=retry_count > 0,
            meta={
                "page_number": page_number,
                "google_block_retry_count": retry_count,
                "download_slot": "google_search_html",
            },
        )

    def _build_custom_search_request(
        self,
        url: str,
        *,
        page_number: int,
        start_index: int,
        retry_count: int = 0,
    ) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_custom_search,
            headers={
                "Accept": "application/json",
                "User-Agent": "GUIAI-Facebook/0.1 (+https://example.invalid)",
            },
            dont_filter=retry_count > 0,
            meta={
                "page_number": page_number,
                "start_index": start_index,
                "google_block_retry_count": retry_count,
                "download_slot": "google_custom_search_api",
            },
        )

    def _build_browser_search_request(
        self,
        url: str,
        *,
        page_number: int,
        browser_attempt: int,
        retry_count: int = 0,
        referer: str | None = None,
    ) -> scrapy.Request:
        headers = self._build_google_headers(page_number=page_number, referer=referer)
        return scrapy.Request(
            url,
            callback=self.parse_browser_search,
            errback=self.handle_browser_search_error,
            headers=headers,
            cookies=self._build_google_cookies(),
            dont_filter=True,
            meta={
                "page_number": page_number,
                "playwright": True,
                "browser_fallback_attempt": browser_attempt,
                "google_block_retry_count": retry_count,
                "playwright_page_init_callback": apply_stealth_patch,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                    PageMethod("wait_for_timeout", 1200),
                ],
                "download_slot": "google_search_browser",
            },
        )

    def _build_bing_search_request(
        self,
        url: str,
        *,
        page_number: int,
        referer: str | None = None,
    ) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_bing_search,
            headers=self._build_google_headers(page_number=page_number, referer=referer),
            dont_filter=True,
            meta={
                "page_number": page_number,
                "download_slot": "bing_search_html",
            },
        )

    def _build_browser_fallback_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        blocked_details: dict[str, Any],
    ) -> scrapy.Request | None:
        if not self.browser_fallback_enabled:
            return None
        if self._should_use_custom_search_api():
            return None
        if not self._should_use_browser_fallback(blocked_details):
            return None

        browser_attempt = int(response.meta.get("browser_fallback_attempt", 0))
        if browser_attempt >= self.browser_fallback_limit:
            return None

        next_attempt = browser_attempt + 1
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_browser_fallback_count", 1)
        logger.info(
            "Google search hit a JS-dependent challenge, switching to browser fallback",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "page_number": page_number,
                "browser_attempt": next_attempt,
                "marker_types": blocked_details["marker_types"],
            },
        )
        return self._build_browser_search_request(
            response.request.url,
            page_number=page_number,
            browser_attempt=next_attempt,
            retry_count=int(response.meta.get("google_block_retry_count", 0)),
            referer="https://www.google.com/",
        )

    def _build_provider_fallback_request(
        self,
        *,
        page_number: int,
    ) -> scrapy.Request | None:
        if self.provider_fallback_attempted:
            return None
        if self.google_search_fallback_provider != "bing":
            return None

        self.provider_fallback_attempted = True
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_provider_fallback_count", 1)
        logger.info(
            "Google search blocked, switching to Bing fallback",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "page_number": page_number,
                "fallback_provider": "bing",
            },
        )
        return self._build_bing_search_request(
            build_bing_search_url(
                subject=self.subject,
                start_date=self.start_date,
                end_date=self.end_date,
                language=self.search_language,
                region=self.search_region,
                results_per_page=self.results_per_page,
                first_result=1,
            ),
            page_number=page_number,
            referer="https://www.bing.com/",
        )

    def _build_block_retry_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        retry_count: int,
        blocked_details: dict[str, Any],
        referer: str | None,
    ) -> scrapy.Request | None:
        if retry_count >= self.max_block_retries:
            return None

        next_retry_count = retry_count + 1
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_block_retry_count", 1)
        logger.info(
            "Google search challenge detected, retrying conservatively",
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
            retry_count=next_retry_count,
            referer=referer,
        )

    def _build_api_retry_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        start_index: int,
        retry_count: int,
        blocked_details: dict[str, Any],
    ) -> scrapy.Request | None:
        if retry_count >= self.max_block_retries:
            return None

        next_retry_count = retry_count + 1
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_api_retry_count", 1)
        logger.info(
            "Google Custom Search API returned a retryable response",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "page_number": page_number,
                "retry_count": next_retry_count,
                "marker_types": blocked_details["marker_types"],
            },
        )
        return self._build_custom_search_request(
            response.request.url,
            page_number=page_number,
            start_index=start_index,
            retry_count=next_retry_count,
        )

    def _build_google_headers(
        self,
        *,
        page_number: int,
        referer: str | None,
    ) -> dict[str, str]:
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                "image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": f"{self.search_language},pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "DNT": "1",
            "Pragma": "no-cache",
            "Priority": "u=0, i",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none" if page_number == 1 else "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": random.choice(self.user_agents),
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _build_google_cookies(self) -> dict[str, str]:
        return {"CONSENT": self.google_consent_cookie}

    @staticmethod
    def _should_use_browser_fallback(blocked_details: dict[str, Any]) -> bool:
        marker_types = set(blocked_details.get("marker_types", []))
        return bool(
            {"enablejs_challenge", "consent_interstitial"} & marker_types
            or any(marker.startswith("http_status_403") for marker in marker_types)
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

        if response.status in self.handle_httpstatus_list:
            marker_types.append(f"http_status_{response.status}")

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

    def _detect_custom_search_block(self, response):  # type: ignore[no-untyped-def]
        if response.status < 400:
            return None

        marker_types = [f"api_http_status_{response.status}"]
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            payload = {}

        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        reason = None
        if isinstance(error, dict):
            errors = error.get("errors", [])
            if errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason")
            if reason:
                marker_types.append(f"api_{reason}")

        return {
            "marker_types": sorted(set(marker_types)),
            "response_url": response.url,
            "title": None,
            "page_number": int(response.meta.get("page_number", 1)),
            "anchor_count": 0,
            "provider": "google_custom_search_api",
            "status": response.status,
            "reason": reason,
        }

    @staticmethod
    def _next_custom_search_start_index(payload: dict[str, Any]) -> int | None:
        queries = payload.get("queries", {})
        next_page = queries.get("nextPage", []) if isinstance(queries, dict) else []
        if not next_page:
            return None
        next_start = next_page[0].get("startIndex")
        return int(next_start) if next_start is not None else None

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
            "Google search returned a blocked/challenge page",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "marker_types": blocked_details["marker_types"],
                "response_url": blocked_details["response_url"],
            },
        )


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
