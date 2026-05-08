from __future__ import annotations

import json
import random
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import scrapy
from common.logging import get_logger

from face.captcha import CaptchaChallenge, TwoCaptchaClient, detect_captcha_challenge
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
        resolved_settings = get_settings()
        self.id_query = id_query
        self.subject = subject
        self.query_source = query_source
        self.max_pages = int(
            resolved_settings.search_max_pages if max_pages is None else max_pages
        )
        self.search_url_override = search_url_override
        self.user_agents = user_agents or DEFAULT_USER_AGENTS
        self.search_language = search_language or resolved_settings.searxng_search_language
        self.search_region = search_region or resolved_settings.searxng_search_region
        self.search_category = search_category or resolved_settings.searxng_search_category
        self.enabled_engines = enabled_engines or resolved_settings.searxng_enabled_engines
        self.safe_search = (
            resolved_settings.searxng_safe_search if safe_search is None else int(safe_search)
        )
        self.results_per_page = int(
            results_per_page or resolved_settings.searxng_results_per_page
        )
        self.searxng_internal_url = resolved_settings.searxng_internal_url
        self.max_block_retries = int(
            resolved_settings.search_block_retry_limit
            if max_block_retries is None
            else max_block_retries
        )
        self.page_count = 0
        self.seen_urls: set[str] = set()
        self.search_blocked_details: dict[str, Any] | None = None
        self.search_attempt = 1
        self.items_found = 0
        self.twocaptcha = TwoCaptchaClient()

    def start_requests(self):  # type: ignore[no-untyped-def]
        self._set_twocaptcha_limit_stat()
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
        captcha_attempted = bool(response.meta.get("twocaptcha_solve_attempted", False))
        self.page_count = max(self.page_count, page_number)

        blocked_details = self._detect_search_error(response)
        captcha_challenge = self._detect_captcha_challenge(response)
        challenge_details = self._captcha_details_from_challenge(
            captcha_challenge=captcha_challenge,
            response=response,
        )
        effective_blocked_details = self._merge_blocked_details(
            blocked_details,
            challenge_details,
            page_number=page_number,
            response_url=response.url,
        )

        if captcha_challenge is not None:
            attempted_solves_before = self.twocaptcha.attempted_solves
            captcha_retry_request = self._maybe_solve_captcha_and_retry(
                response=response,
                page_number=page_number,
                retry_count=retry_count,
                search_attempt=current_attempt,
                blocked_details=effective_blocked_details,
                captcha_challenge=captcha_challenge,
            )
            if captcha_retry_request is not None:
                yield captcha_retry_request
                return
            captcha_attempted = (
                captcha_attempted or self.twocaptcha.attempted_solves > attempted_solves_before
            )

        if blocked_details is not None or captcha_challenge is not None:
            retry_request = self._build_retry_request(
                response=response,
                page_number=page_number,
                retry_count=retry_count,
                search_attempt=current_attempt,
                blocked_details=effective_blocked_details,
                captcha_solve_attempted=captcha_attempted,
            )
            if retry_request is not None:
                yield retry_request
                return
            self._mark_search_blocked(
                blocked_details=effective_blocked_details,
                retry_count=retry_count,
            )
            return

        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            invalid_json_details = {
                "marker_types": ["invalid_json_response"],
                "response_url": response.url,
                "title": None,
                "page_number": page_number,
                "anchor_count": 0,
            }
            self._mark_search_blocked(
                blocked_details=invalid_json_details,
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
        extra_headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        meta_extra: dict[str, Any] | None = None,
    ) -> scrapy.Request:
        meta: dict[str, Any] = {
            "page_number": page_number,
            "search_attempt": search_attempt,
            "search_retry_count": retry_count,
            "download_slot": "searxng_search_json",
        }
        if meta_extra:
            meta.update(meta_extra)
        return scrapy.Request(
            url,
            callback=self.parse_search,
            headers=self._build_search_headers(
                page_number=page_number,
                extra_headers=extra_headers,
            ),
            cookies=cookies,
            dont_filter=retry_count > 0,
            meta=meta,
        )

    def _build_retry_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        retry_count: int,
        search_attempt: int,
        blocked_details: dict[str, Any],
        captcha_solve_attempted: bool = False,
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
        meta_extra = {
            "twocaptcha_solve_attempted": captcha_solve_attempted,
        }
        return self._build_search_request(
            response.request.url,
            page_number=page_number,
            search_attempt=search_attempt,
            retry_count=next_retry_count,
            meta_extra=meta_extra,
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
        extra_headers: dict[str, str] | None = None,
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
        if extra_headers:
            headers.update(extra_headers)
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

    def _detect_captcha_challenge(
        self,
        response,  # type: ignore[no-untyped-def]
    ) -> CaptchaChallenge | None:
        return detect_captcha_challenge(
            response_url=response.url,
            status_code=response.status,
            response_text=response.text,
            headers=response.headers.to_unicode_dict(),
        )

    def _captcha_details_from_challenge(
        self,
        *,
        captcha_challenge: CaptchaChallenge | None,
        response,  # type: ignore[no-untyped-def]
    ) -> dict[str, Any] | None:
        if captcha_challenge is None:
            return None
        return {
            "marker_types": list(captcha_challenge.marker_types),
            "response_url": response.url,
            "title": None,
            "page_number": int(response.meta.get("page_number", 1)),
            "anchor_count": 0,
            "captcha_type": captcha_challenge.captcha_type,
        }

    def _merge_blocked_details(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
        *,
        page_number: int,
        response_url: str,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "marker_types": [],
            "response_url": response_url,
            "title": None,
            "page_number": page_number,
            "anchor_count": 0,
        }
        for candidate in (primary, secondary):
            if candidate is None:
                continue
            marker_types = candidate.get("marker_types", [])
            if isinstance(marker_types, list):
                merged["marker_types"].extend(marker_types)
            for key, value in candidate.items():
                if key == "marker_types":
                    continue
                if value is not None:
                    merged[key] = value
        merged["marker_types"] = list(dict.fromkeys(merged["marker_types"]))
        return merged

    def _maybe_solve_captcha_and_retry(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        retry_count: int,
        search_attempt: int,
        blocked_details: dict[str, Any],
        captcha_challenge: CaptchaChallenge,
    ) -> scrapy.Request | None:
        if bool(response.meta.get("twocaptcha_solve_attempted", False)):
            return None
        if not self.twocaptcha.can_solve():
            return None

        self._set_twocaptcha_limit_stat()
        solve_extra = {
            "service": "face-search-spider",
            "id_query": self.id_query,
            "page_number": page_number,
            "search_attempt": search_attempt,
            "twocaptcha_used": self.twocaptcha.used_solves,
            "twocaptcha_max": self.twocaptcha.max_solves,
            "captcha_type": captcha_challenge.captcha_type,
        }
        logger.info(
            "Detected captcha challenge and attempting 2Captcha solve",
            extra=solve_extra,
        )
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_twocaptcha_attempt_count", 1)

        token = self.twocaptcha.solve_challenge(
            captcha_challenge,
            logger_extra=solve_extra,
        )
        if token is None:
            if getattr(self, "crawler", None) is not None:
                self.crawler.stats.inc_value("face/search_twocaptcha_failure_count", 1)
            logger.warning(
                "2Captcha could not solve the detected challenge, falling back to normal retry",
                extra=solve_extra,
            )
            return None

        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_twocaptcha_solve_count", 1)
            self.crawler.stats.set_value(
                "face/search_twocaptcha_used",
                self.twocaptcha.used_solves,
            )

        next_retry_count = retry_count + 1
        logger.info(
            "Solved captcha via 2Captcha and retrying search request",
            extra={
                "service": "face-search-spider",
                "id_query": self.id_query,
                "page_number": page_number,
                "search_attempt": search_attempt,
                "retry_count": next_retry_count,
                "twocaptcha_used": self.twocaptcha.used_solves,
                "twocaptcha_max": self.twocaptcha.max_solves,
                "captcha_type": captcha_challenge.captcha_type,
            },
        )
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("face/search_block_retry_count", 1)

        return self._build_captcha_retry_request(
            response=response,
            page_number=page_number,
            retry_count=next_retry_count,
            search_attempt=search_attempt,
            blocked_details=blocked_details,
            captcha_challenge=captcha_challenge,
            token=token,
        )

    def _build_captcha_retry_request(
        self,
        *,
        response,  # type: ignore[no-untyped-def]
        page_number: int,
        retry_count: int,
        search_attempt: int,
        blocked_details: dict[str, Any],
        captcha_challenge: CaptchaChallenge,
        token: str,
    ) -> scrapy.Request:
        captcha_fields = captcha_challenge.build_submission_fields(token)
        captcha_headers = self._build_captcha_solution_headers(
            token=token,
            captcha_type=captcha_challenge.captcha_type,
        )
        captcha_cookies = self._build_captcha_solution_cookies(
            token=token,
            captcha_type=captcha_challenge.captcha_type,
        )
        meta_extra = {
            "twocaptcha_solve_attempted": True,
            "twocaptcha_captcha_type": captcha_challenge.captcha_type,
            "twocaptcha_marker_types": blocked_details["marker_types"],
        }

        if self._should_submit_captcha_form(captcha_challenge):
            return scrapy.FormRequest(
                url=captcha_challenge.submit_url,
                method=captcha_challenge.form_method,
                formdata=captcha_fields,
                callback=self._resume_search_after_captcha,
                headers=self._build_search_headers(
                    page_number=page_number,
                    extra_headers=captcha_headers,
                ),
                cookies=captcha_cookies,
                dont_filter=True,
                meta={
                    "page_number": page_number,
                    "search_attempt": search_attempt,
                    "search_retry_count": retry_count,
                    "download_slot": "searxng_search_json",
                    "captcha_original_request_url": response.request.url,
                    "captcha_retry_headers": captcha_headers,
                    "captcha_retry_cookies": captcha_cookies,
                    "captcha_solution_token": token,
                    **meta_extra,
                },
            )

        retry_url = self._append_query_params(response.request.url, captcha_fields)
        return self._build_search_request(
            retry_url,
            page_number=page_number,
            search_attempt=search_attempt,
            retry_count=retry_count,
            extra_headers=captcha_headers,
            cookies=captcha_cookies,
            meta_extra=meta_extra,
        )

    def _resume_search_after_captcha(
        self,
        response,  # type: ignore[no-untyped-def]
    ):  # type: ignore[no-untyped-def]
        page_number = int(response.meta.get("page_number", 1))
        search_attempt = int(response.meta.get("search_attempt", self.search_attempt))
        retry_count = int(response.meta.get("search_retry_count", 0))

        if (
            self._looks_like_json_response(response)
            and self._detect_captcha_challenge(response) is None
            and self._detect_search_error(response) is None
        ):
            yield from self.parse_search(response)
            return

        merged_cookies = {
            **response.meta.get("captcha_retry_cookies", {}),
            **self._extract_response_cookies(response),
        }
        retry_headers = dict(response.meta.get("captcha_retry_headers", {}))
        retry_url = self._append_query_params(
            str(response.meta["captcha_original_request_url"]),
            {
                self._captcha_token_field_from_meta(response): str(
                    response.meta.get("captcha_solution_token", "")
                ),
            },
        )
        yield self._build_search_request(
            retry_url,
            page_number=page_number,
            search_attempt=search_attempt,
            retry_count=retry_count,
            extra_headers=retry_headers,
            cookies=merged_cookies,
            meta_extra={
                "twocaptcha_solve_attempted": True,
                "twocaptcha_captcha_type": response.meta.get("twocaptcha_captcha_type"),
            },
        )

    def _mark_search_blocked(
        self,
        *,
        blocked_details: dict[str, Any],
        retry_count: int,
    ) -> None:
        self.search_blocked_details = blocked_details
        blocked_details["retry_count"] = retry_count
        if self.twocaptcha.attempted_solves > 0:
            blocked_details["twocaptcha_used"] = self.twocaptcha.used_solves
            blocked_details["twocaptcha_attempted"] = self.twocaptcha.attempted_solves
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
                "twocaptcha_used": blocked_details.get("twocaptcha_used"),
            },
        )

    def _set_twocaptcha_limit_stat(self) -> None:
        if getattr(self, "crawler", None) is None:
            return
        self.crawler.stats.set_value("face/search_twocaptcha_limit", self.twocaptcha.max_solves)

    @staticmethod
    def _should_submit_captcha_form(captcha_challenge: CaptchaChallenge) -> bool:
        return bool(captcha_challenge.form_inputs) or (
            captcha_challenge.submit_url != captcha_challenge.page_url
        )

    @staticmethod
    def _build_captcha_solution_headers(
        *,
        token: str,
        captcha_type: str,
    ) -> dict[str, str]:
        return {
            "X-Captcha-Token": token,
            "X-2Captcha-Token": token,
            "X-Captcha-Type": captcha_type,
        }

    @staticmethod
    def _build_captcha_solution_cookies(
        *,
        token: str,
        captcha_type: str,
    ) -> dict[str, str]:
        field_name = "g-recaptcha-response"
        if captcha_type == "hcaptcha":
            field_name = "h-captcha-response"
        elif captcha_type == "turnstile":
            field_name = "cf-turnstile-response"
        return {field_name: token}

    @staticmethod
    def _append_query_params(url: str, params: dict[str, str]) -> str:
        split_result = urlsplit(url)
        current_params = dict(parse_qsl(split_result.query, keep_blank_values=True))
        current_params.update(params)
        return urlunsplit(
            (
                split_result.scheme,
                split_result.netloc,
                split_result.path,
                urlencode(current_params),
                split_result.fragment,
            )
        )

    @staticmethod
    def _extract_response_cookies(response) -> dict[str, str]:  # type: ignore[no-untyped-def]
        cookies: dict[str, str] = {}
        for raw_cookie in response.headers.getlist("Set-Cookie"):
            parsed_cookie = SimpleCookie()
            parsed_cookie.load(raw_cookie.decode("utf-8", errors="ignore"))
            for name, morsel in parsed_cookie.items():
                cookies[name] = morsel.value
        return cookies

    @staticmethod
    def _captcha_token_field_from_meta(response) -> str:  # type: ignore[no-untyped-def]
        captcha_type = str(response.meta.get("twocaptcha_captcha_type", "recaptcha_v2"))
        if captcha_type == "hcaptcha":
            return "h-captcha-response"
        if captcha_type == "turnstile":
            return "cf-turnstile-response"
        return "g-recaptcha-response"

    @staticmethod
    def _looks_like_json_response(response) -> bool:  # type: ignore[no-untyped-def]
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore")
        stripped_body = response.text.lstrip()
        return "json" in content_type.lower() or stripped_body.startswith("{")


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
