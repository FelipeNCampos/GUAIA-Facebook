from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import scrapy
from common.logging import get_logger
from scrapy_playwright.page import PageMethod

from face.items import FacebookRecordItem
from face.middlewares.stealth import apply_stealth_patch
from face.url_classifier import classify_url
from face.url_utils import decode_facebook_redirect_url, normalize_url, strip_tracking_params

logger = get_logger(__name__)

PT_BR_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

COUNT_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?",
    re.IGNORECASE,
)
COMMENTS_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)\s+coment",
    re.IGNORECASE,
)
VIEWS_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)\s+visualiza",
    re.IGNORECASE,
)
FOLLOWERS_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)\s+(?:seguidores|followers)",
    re.IGNORECASE,
)
FOLLOWING_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)\s+(?:seguindo|following)",
    re.IGNORECASE,
)
PT_BR_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-z]+)\s+de\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
RELATIVE_TIME_RE = re.compile(
    r"(?:(?:ha|há)\s*)?(?P<value>\d+)\s*(?P<unit>m|min|mins|h|d|sem)(?:\s+ago)?",
    re.IGNORECASE,
)
REACTION_LABEL_RE = re.compile(
    r"(?:amei|curtir|like|reag)[^\d]*(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)",
    re.IGNORECASE,
)
PT_BR_DATE_NO_YEAR_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-z]+)",
    re.IGNORECASE,
)
REACTIONS_GENERIC_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:mil|mi|milhao|milhoes))?)\s+rea[cç]",
    re.IGNORECASE,
)
CONTENT_ID_RE = re.compile(r"/(?:videos|reel|reels)/(\d+)")
PAGE_CATEGORY_SKIP_TEXTS = {
    "sobre",
    "publicacoes",
    "posts",
    "videos",
    "reels",
    "fotos",
    "photos",
    "curtir",
    "comentarios",
    "mais relevantes",
}
WAIT_SELECTORS = {
    "page": "[role='main'] h1, [role='banner'], [role='tablist']",
    "video": "[data-video-id], [aria-label*='Reproduzir'], video",
    "reel": "[data-pagelet='Reels'], [aria-label='Curtir'], a[href*='sk=reels_tab']",
    "group": "[role='main'], h1",
    "post": "[role='main'], [data-ad-rendering-role='profile_name']",
}


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
                "playwright_page_methods": self._build_page_methods(self.category),
            },
        )

    async def parse_facebook(self, response):  # type: ignore[no-untyped-def]
        page = response.meta.get("playwright_page")
        normalized_url = normalize_url(
            response.css("link[rel='canonical']::attr(href)").get() or response.url
        ) or response.url
        detected_category = self._detect_category(normalized_url)
        title = response.css("title::text").get()
        description = response.css(
            "meta[property='og:description']::attr(content), "
            "meta[name='description']::attr(content)"
        ).get()
        payload = {
            "title": title,
            "description": description,
            "final_url": response.url,
            "canonical_url": normalized_url,
            "original_category": self.category,
            "detected_category": detected_category,
            **self._extract_metadata_by_category(response, detected_category),
        }

        try:
            yield FacebookRecordItem(
                item_type="facebook_record",
                id_query=self.id_query,
                url=response.url,
                url_normalized=normalized_url,
                category=detected_category,
                query_source=self.query_source,
                record_id=self.record_id,
                status="enriched",
                payload=payload,
                last_error=None,
            )
        finally:
            if page is not None:
                await page.close()

    def _build_page_methods(self, category: str) -> list[PageMethod]:
        selector = WAIT_SELECTORS.get(category, WAIT_SELECTORS["post"])
        return [
            PageMethod("wait_for_load_state", "domcontentloaded"),
            PageMethod("wait_for_selector", selector, timeout=15000),
            PageMethod("wait_for_timeout", 500),
        ]

    def _detect_category(self, normalized_url: str) -> str:
        return classify_url(strip_tracking_params(normalized_url) or normalized_url)

    def _extract_metadata_by_category(
        self,
        response,
        detected_category: str,
    ) -> dict[str, object | None]:  # type: ignore[no-untyped-def]
        extractor = {
            "page": self._extract_page_metadata,
            "video": self._extract_video_metadata,
            "reel": self._extract_reel_metadata,
        }.get(detected_category, self._extract_generic_metadata)
        return extractor(response)

    def _extract_generic_metadata(self, response) -> dict[str, object | None]:  # type: ignore[no-untyped-def]
        primary_scope = self._extract_primary_content_scope(response)
        engagement_text = self._extract_engagement_text(response, primary_scope)
        published_at_text = self._extract_published_at_text(response, primary_scope)
        author_url = self._extract_author_url(response)
        comment_match = COMMENTS_RE.search(engagement_text or "")

        return {
            "profile_name": self._extract_author_name(response),
            "profile_url": normalize_url(response.urljoin(author_url)) if author_url else None,
            "is_verified": self._extract_author_verified(response),
            "published_at_text": published_at_text,
            "published_at_iso": self._parse_published_at(published_at_text),
            "reaction_count": self._extract_reaction_count(
                response,
                primary_scope,
                engagement_text,
            ),
            "comment_count": self._extract_comment_count(
                response,
                primary_scope,
                comment_match,
            ),
            "view_count": self._extract_view_count(
                response,
                primary_scope,
                engagement_text,
            ),
        }

    def _extract_video_metadata(self, response) -> dict[str, object | None]:  # type: ignore[no-untyped-def]
        metadata = self._extract_generic_metadata(response)
        metadata["video_id"] = self._extract_video_id(response)
        return metadata

    def _extract_reel_metadata(self, response) -> dict[str, object | None]:  # type: ignore[no-untyped-def]
        metadata = self._extract_generic_metadata(response)
        metadata["video_id"] = self._extract_video_id(response)
        if metadata.get("reaction_count") is None:
            metadata["reaction_count"] = self._extract_reel_reaction_count(response)
        return metadata

    def _extract_page_metadata(self, response) -> dict[str, object | None]:  # type: ignore[no-untyped-def]
        main_scope = response.xpath("(//*[@role='main'])[1]")
        banner_scope = response.xpath("(//*[@role='banner'])[1]")
        combined_text = self._clean_text(
            " ".join(
                main_scope.xpath(".//text()").getall() + banner_scope.xpath(".//text()").getall()
            )
        )
        profile_name = self._first_clean_text(
            [
                response.xpath("(//*[@role='main']//h1//text()[normalize-space()])[1]").get(),
                response.xpath("(//*[@role='banner']//h1//text()[normalize-space()])[1]").get(),
                self._extract_author_name(response),
            ]
        )
        profile_url = normalize_url(
            response.css("link[rel='canonical']::attr(href)").get() or response.url
        )
        external_links = self._extract_external_links(response)
        return {
            "profile_name": profile_name,
            "profile_url": profile_url,
            "is_verified": self._extract_author_verified(response),
            "published_at_text": None,
            "published_at_iso": None,
            "reaction_count": None,
            "comment_count": None,
            "view_count": None,
            "follower_count": self._extract_count_from_match(
                FOLLOWERS_RE.search(combined_text or "")
            ),
            "following_count": self._extract_count_from_match(
                FOLLOWING_RE.search(combined_text or "")
            ),
            "external_links": external_links,
            "bio": self._extract_page_bio(response, profile_name),
            "page_category": self._extract_page_category(response, profile_name),
        }

    @staticmethod
    def _clean_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
        return cleaned or None

    @staticmethod
    def _strip_accents(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        return normalized.encode("ascii", "ignore").decode("ascii")

    @classmethod
    def _first_clean_text(cls, values: Iterable[str | None]) -> str | None:
        for value in values:
            cleaned = cls._clean_text(value)
            if cleaned:
                return cleaned
        return None

    def _extract_author_name(self, response) -> str | None:  # type: ignore[no-untyped-def]
        return self._first_clean_text(
            [
                response.css("[data-ad-rendering-role='profile_name'] a span::text").get(),
                response.css("[data-ad-rendering-role='profile_name'] a b span::text").get(),
                response.xpath(
                    "//*[@data-ad-rendering-role='profile_name']//a[1]//text()[normalize-space()]"
                ).get(),
                response.xpath(
                    "(//a[contains(@href, 'sk=reels_tab')]//text()[normalize-space()])[1]"
                ).get(),
                response.xpath(
                    "(//a[contains(@aria-label, 'Ver perfil do dono')]"
                    "//text()[normalize-space()])[1]"
                ).get(),
            ]
        )

    def _extract_author_url(self, response) -> str | None:  # type: ignore[no-untyped-def]
        return self._first_clean_text(
            [
                response.css("[data-ad-rendering-role='profile_name'] a::attr(href)").get(),
                response.xpath("(//a[contains(@href, 'sk=reels_tab')]/@href)[1]").get(),
                response.xpath("(//a[contains(@aria-label, 'Ver perfil do dono')]/@href)[1]").get(),
            ]
        )

    def _extract_author_verified(self, response) -> bool:  # type: ignore[no-untyped-def]
        verified_title = response.css(
            "[data-ad-rendering-role='profile_name'] svg::attr(title)"
        ).re_first(r"(?i)verific")
        if verified_title:
            return True
        return bool(
            response.xpath(
                "//*[contains(., 'Conta verificada') or contains(., 'Verified')]"
                "[ancestor::*[@data-ad-rendering-role='profile_name'] "
                "or ancestor::a[contains(@href, 'sk=reels_tab')]]"
            )
        )

    def _extract_primary_content_scope(self, response):  # type: ignore[no-untyped-def]
        content_id = self._extract_content_id(response.url)
        if content_id:
            nodes = response.xpath(f"//*[@data-video-id='{content_id}']")
            if nodes:
                scope = self._find_scope_with_signals(nodes[0], expect_actions=True)
                if scope is not None:
                    return scope

        reel_anchor = response.xpath(
            "(//*[contains(@aria-label, 'Amei') "
            "or contains(@aria-label, 'Curtir') "
            "or contains(@aria-label, 'reaç') "
            "or contains(@aria-label, 'reac')])[1]"
        )
        if reel_anchor:
            scope = self._find_scope_with_signals(reel_anchor[0], expect_actions=True)
            if scope is not None:
                return scope

        profile_blocks = response.xpath("//*[@data-ad-rendering-role='profile_name']")
        if profile_blocks:
            scope = self._find_scope_with_signals(profile_blocks[0], expect_actions=False)
            if scope is not None:
                return scope

        main = response.xpath("(//*[@role='main'])[1]")
        return main or None
        

    def _find_scope_with_signals(self, node, *, expect_actions: bool):  # type: ignore[no-untyped-def]
        ancestors = list(node.xpath("ancestor-or-self::*"))
        for candidate in reversed(ancestors):
            text = self._clean_text(" ".join(candidate.xpath(".//text()").getall()))
            aria_labels = " ".join(
                candidate.xpath(".//*[@aria-label]/@aria-label").getall()
            ).lower()

            if not text and not aria_labels:
                continue

            if COMMENTS_RE.search(text or "") and VIEWS_RE.search(text or ""):
                return candidate

            if COMMENTS_RE.search(aria_labels or "") and VIEWS_RE.search(aria_labels or ""):
                return candidate

            if expect_actions and any(
                token in aria_labels
                for token in (
                    "curtir",
                    "comentar",
                    "compartilhar",
                    "amei",
                    "reaç",
                    "reac",
                )
            ):
                return candidate

            if not expect_actions and (
                "veja quem reagiu" in (text or "").lower()
                or "veja quem reagiu" in aria_labels
            ):
                return candidate

        return node

    @staticmethod
    def _extract_content_id(url: str) -> str | None:
        match = CONTENT_ID_RE.search(url)
        if match is None:
            return None
        return match.group(1)

    def _extract_video_id(self, response) -> str | None:  # type: ignore[no-untyped-def]
        video_id = response.xpath("(//*[@data-video-id]/@data-video-id)[1]").get()
        if video_id:
            return video_id
        return self._extract_content_id(response.url)

    def _extract_published_at_text(self, response, primary_scope=None) -> str | None:  # type: ignore[no-untyped-def]
        meta_published = self._first_clean_text(
            [
                response.css("meta[property='article:published_time']::attr(content)").get(),
                response.css("meta[property='og:updated_time']::attr(content)").get(),
            ]
        )
        if meta_published:
            return meta_published

        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            labels = root.xpath(".//*[@aria-label]/@aria-label").getall()
            for label in labels:
                cleaned = self._clean_text(label)
                if not cleaned:
                    continue
                if (
                    PT_BR_DATE_RE.search(cleaned)
                    or PT_BR_DATE_NO_YEAR_RE.search(cleaned)
                    or RELATIVE_TIME_RE.search(cleaned)
                ):
                    return cleaned

            texts = root.xpath(
                ".//a//text()[normalize-space()] | "
                ".//span//text()[normalize-space()] | "
                ".//div//text()[normalize-space()]"
            ).getall()
            for text in texts:
                cleaned = self._clean_text(text)
                if not cleaned:
                    continue
                if (
                    PT_BR_DATE_RE.search(cleaned)
                    or PT_BR_DATE_NO_YEAR_RE.search(cleaned)
                    or RELATIVE_TIME_RE.search(cleaned)
                ):
                    return cleaned

        return None

    def _extract_engagement_text(self, response, primary_scope=None) -> str | None:  # type: ignore[no-untyped-def]
        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            candidates = root.xpath(
                ".//*[contains(translate(., "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
                "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), 'coment') "
                "or contains(translate(., "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
                "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), 'visualiza') "
                "or contains(translate(@aria-label, "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
                "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), 'coment') "
                "or contains(translate(@aria-label, "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
                "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), 'visualiza')]"
            )
            for candidate in candidates:
                text = self._clean_text(
                    " ".join(candidate.xpath(".//text() | .//@aria-label").getall())
                )
                if text and COMMENTS_RE.search(text) and VIEWS_RE.search(text):
                    return text

        for root in search_roots:
            text = self._clean_text(
                " ".join(root.xpath(".//text() | .//@aria-label").getall())
            )
            if not text:
                continue
            if COMMENTS_RE.search(text) or VIEWS_RE.search(text):
                return text

        return None

    def _extract_reaction_count(
        self,
        response,
        primary_scope=None,
        engagement_text: str | None = None,
    ) -> int | None:  # type: ignore[no-untyped-def]
        action_count = self._extract_count_from_action_label(response, primary_scope, "Curtir")
        if action_count is not None:
            return action_count

        reaction_label_count = self._extract_reel_reaction_count(response)
        if reaction_label_count is not None:
            return reaction_label_count

        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            reaction_nodes = root.xpath(
                ".//*[@aria-label]"
            )
            for node in reaction_nodes:
                node_text = self._clean_text(
                    " ".join(node.xpath(".//text() | ./@aria-label").getall())
                )
                if not node_text:
                    continue

                generic_match = REACTIONS_GENERIC_RE.search(node_text)
                if generic_match is not None:
                    count = self._parse_count_token(generic_match.group(1))
                    if count is not None:
                        return count

                count = self._extract_reaction_count_from_text(node_text)
                if count is not None and (
                    "reag" in self._strip_accents(node_text).lower()
                    or "curtir" in self._strip_accents(node_text).lower()
                    or "amei" in self._strip_accents(node_text).lower()
                ):
                    return count

        if engagement_text:
            generic_match = REACTIONS_GENERIC_RE.search(engagement_text)
            if generic_match is not None:
                count = self._parse_count_token(generic_match.group(1))
                if count is not None:
                    return count

            comment_match = COMMENTS_RE.search(engagement_text)
            reaction_limit = comment_match.start() if comment_match else len(engagement_text)
            count = self._extract_reaction_count_from_text(engagement_text[:reaction_limit])
            if count is not None:
                return count

        return None

    def _extract_reel_reaction_count(self, response) -> int | None:  # type: ignore[no-untyped-def]
        labels = response.xpath(
            "//*[contains(@aria-label, 'Amei') "
            "or contains(@aria-label, 'Curtir') "
            "or contains(@aria-label, 'reaç') "
            "or contains(@aria-label, 'reac')]/@aria-label"
        ).getall()

        for label in labels:
            cleaned = self._clean_text(label)
            if not cleaned:
                continue

            match = REACTION_LABEL_RE.search(cleaned)
            if match is not None:
                count = self._parse_count_token(match.group(1))
                if count is not None:
                    return count

            generic = REACTIONS_GENERIC_RE.search(cleaned)
            if generic is not None:
                count = self._parse_count_token(generic.group(1))
                if count is not None:
                    return count

        return None

    def _extract_comment_count(
        self,
        response,
        primary_scope=None,
        comment_match: re.Match[str] | None = None,
    ) -> int | None:  # type: ignore[no-untyped-def]
        action_count = self._extract_count_from_action_label(response, primary_scope, "Comentar")
        if action_count is not None:
            return action_count

        direct_count = self._extract_count_from_match(comment_match)
        if direct_count is not None:
            return direct_count

        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            labels = root.xpath(".//*[@aria-label]/@aria-label").getall()
            for label in labels:
                cleaned = self._clean_text(label) or ""
                match = COMMENTS_RE.search(cleaned)
                if match:
                    count = self._parse_count_token(match.group(1))
                    if count is not None:
                        return count

            text = self._clean_text(" ".join(root.xpath(".//text()").getall())) or ""
            match = COMMENTS_RE.search(text)
            if match:
                count = self._parse_count_token(match.group(1))
                if count is not None:
                    return count

        return None

    def _extract_view_count(
        self,
        response,
        primary_scope=None,
        engagement_text: str | None = None,
    ) -> int | None:  # type: ignore[no-untyped-def]
        direct_count = self._extract_count_from_match(
            VIEWS_RE.search(engagement_text) if engagement_text else None
        )
        if direct_count is not None:
            return direct_count

        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            labels = root.xpath(".//*[@aria-label]/@aria-label").getall()
            for label in labels:
                cleaned = self._clean_text(label) or ""
                match = VIEWS_RE.search(cleaned)
                if match:
                    count = self._parse_count_token(match.group(1))
                    if count is not None:
                        return count

            text = self._clean_text(" ".join(root.xpath(".//text()").getall())) or ""
            match = VIEWS_RE.search(text)
            if match:
                count = self._parse_count_token(match.group(1))
                if count is not None:
                    return count

        return None

    def _extract_count_from_action_label(self, response, primary_scope, label: str) -> int | None:  # type: ignore[no-untyped-def]
        normalized_label = self._strip_accents(label).lower()

        search_roots = []
        if primary_scope is not None:
            search_roots.append(primary_scope)

        main = response.xpath("(//*[@role='main'])[1]")
        if main:
            search_roots.append(main)

        search_roots.append(response)

        for root in search_roots:
            action_nodes = root.xpath(".//*[@aria-label]")
            for action_node in action_nodes:
                aria_label = self._clean_text(action_node.xpath("./@aria-label").get())
                if not aria_label:
                    continue

                comparable = self._strip_accents(aria_label).lower()
                if normalized_label not in comparable:
                    continue

                direct_count = self._extract_reaction_count_from_text(aria_label)
                if direct_count is not None:
                    return direct_count

                sibling_text = self._clean_text(
                    " ".join(action_node.xpath("following-sibling::*[1]//text()").getall())
                )
                count = self._extract_reaction_count_from_text(sibling_text or "")
                if count is not None:
                    return count

                parent_text = self._clean_text(
                    " ".join(action_node.xpath("../text() | ../@aria-label").getall())
                )
                count = self._extract_reaction_count_from_text(parent_text or "")
                if count is not None:
                    return count

        return None

    def _extract_external_links(self, response) -> list[str]:  # type: ignore[no-untyped-def]
        links: list[str] = []
        seen: set[str] = set()
        for href in response.xpath("//*[@role='main']//a[@href]/@href").getall():
            decoded = decode_facebook_redirect_url(href) or href
            if not decoded.startswith("http"):
                continue
            host = urlsplit(decoded).netloc.lower()
            if host.endswith("facebook.com"):
                continue
            if decoded in seen:
                continue
            seen.add(decoded)
            links.append(decoded)
        return links

    def _extract_page_bio(self, response, profile_name: str | None) -> str | None:  # type: ignore[no-untyped-def]
        candidates = response.xpath(
            "//*[@role='main']//*[text()[normalize-space()]]//text()"
        ).getall()
        for text in candidates:
            cleaned = self._clean_text(text)
            if not cleaned:
                continue
            lowered = self._strip_accents(cleaned).lower()
            if profile_name and cleaned == profile_name:
                continue
            if FOLLOWERS_RE.search(cleaned) or FOLLOWING_RE.search(cleaned):
                continue
            if lowered in PAGE_CATEGORY_SKIP_TEXTS:
                continue
            if len(cleaned) < 35:
                continue
            return cleaned
        return None

    def _extract_page_category(self, response, profile_name: str | None) -> str | None:  # type: ignore[no-untyped-def]
        candidates = response.xpath(
            "//*[@role='main']//*[text()[normalize-space()]]//text()"
        ).getall()
        for text in candidates:
            cleaned = self._clean_text(text)
            if not cleaned:
                continue
            lowered = self._strip_accents(cleaned).lower()
            if profile_name and cleaned == profile_name:
                continue
            if FOLLOWERS_RE.search(cleaned) or FOLLOWING_RE.search(cleaned):
                continue
            if lowered in PAGE_CATEGORY_SKIP_TEXTS:
                continue
            if len(cleaned) > 60:
                continue
            return cleaned
        return None

    @classmethod
    def _extract_count_from_match(cls, match: re.Match[str] | None) -> int | None:
        if match is None:
            return None
        return cls._parse_count_token(match.group(1))

    @classmethod
    def _extract_reaction_count_from_text(cls, text: str) -> int | None:
        tokens = COUNT_TOKEN_RE.findall(text)
        if not tokens:
            return None
        return cls._parse_count_token(tokens[-1])

    @staticmethod
    def _parse_count_token(token: str | None) -> int | None:
        if token is None:
            return None
        raw = token.replace("\xa0", " ").strip().lower()
        multiplier = 1
        if any(unit in raw for unit in ("milhao", "milhoes")):
            multiplier = 1_000_000
        elif re.search(r"\bmi\b", raw):
            multiplier = 1_000_000
        elif "mil" in raw:
            multiplier = 1_000

        number_text = re.sub(r"[^\d,.-]", "", raw).replace(".", "")
        if not number_text:
            return None
        normalized = number_text.replace(",", ".")
        try:
            return int(float(normalized) * multiplier)
        except ValueError:
            return None

    @staticmethod
    def _parse_published_at(published_at_text: str | None) -> str | None:
        if published_at_text is None:
            return None

        cleaned = FacebookEnrichSpider._clean_text(published_at_text)
        if not cleaned:
            return None

        date_match = PT_BR_DATE_RE.search(cleaned)
        if date_match is not None:
            month_name = FacebookEnrichSpider._strip_accents(date_match.group("month").lower())
            month = PT_BR_MONTHS.get(month_name)
            if month is None:
                return None

            day = int(date_match.group("day"))
            year = int(date_match.group("year"))
            return f"{year:04d}-{month:02d}-{day:02d}"

        date_no_year_match = PT_BR_DATE_NO_YEAR_RE.search(cleaned)
        if date_no_year_match is not None:
            month_name = FacebookEnrichSpider._strip_accents(date_no_year_match.group("month").lower())
            month = PT_BR_MONTHS.get(month_name)
            if month is None:
                return None

            day = int(date_no_year_match.group("day"))
            now = datetime.now().astimezone()
            year = now.year

            try:
                candidate = datetime(year, month, day).date()
            except ValueError:
                return None

            if candidate > now.date():
                year -= 1

            return f"{year:04d}-{month:02d}-{day:02d}"

        relative_match = RELATIVE_TIME_RE.search(cleaned)
        if relative_match is None:
            return None

        value = int(relative_match.group("value"))
        unit = relative_match.group("unit").lower()

        if unit in {"m", "min"}:
            delta = timedelta(minutes=value)
        elif unit == "h":
            delta = timedelta(hours=value)
        elif unit == "d":
            delta = timedelta(days=value)
        elif unit == "sem":
            delta = timedelta(weeks=value)
        else:
            return None

        return (datetime.now().astimezone() - delta).date().isoformat()


if __name__ == "__main__":
    print("FacebookEnrichSpider ready")
