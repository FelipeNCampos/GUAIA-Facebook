from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_PARAMS = {
    "__cft__",
    "__tn__",
    "eid",
    "fbclid",
    "locale",
    "locale2",
    "paipv",
    "ref",
    "refid",
    "refsrc",
    "set",
}

FACEBOOK_HOST_ALIASES = {
    "facebook.com": "www.facebook.com",
    "m.facebook.com": "www.facebook.com",
    "mobile.facebook.com": "www.facebook.com",
    "mbasic.facebook.com": "www.facebook.com",
    "web.facebook.com": "www.facebook.com",
    "www.facebook.com": "www.facebook.com",
}

GOOGLE_REDIRECT_HOSTS = {
    "google.com",
    "www.google.com",
}

FACEBOOK_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.|mobile\.|mbasic\.|web\.)?facebook\.com[^\s\"'<>]+",
    re.IGNORECASE,
)


def build_search_query(subject: str) -> str:
    return f'site:facebook.com "{subject}"'


def build_searxng_search_url(
    *,
    base_url: str,
    subject: str,
    language: str = "pt-BR",
    region: str = "br",
    category: str = "general",
    enabled_engines: str | None = "google,bing",
    safe_search: int = 0,
    results_per_page: int = 10,
    page_number: int = 1,
) -> str:
    params = {
        "q": subject,
        "language": language,
        "pageno": max(1, page_number),
        "format": "json",
        "categories": category,
        "safesearch": max(0, safe_search),
    }
    if enabled_engines:
        params["engines"] = enabled_engines
    if region:
        params["locale"] = f"{language}_{region.upper()}"
    if results_per_page > 0:
        params["count"] = max(1, results_per_page)

    resolved_base_url = base_url.rstrip("/")
    query_string = urlencode(params, quote_via=quote_plus)
    return f"{resolved_base_url}/search?{query_string}"


def extract_facebook_url(raw_url: str) -> str | None:
    if not raw_url:
        return None

    candidate = _extract_candidate_from_google_redirect(raw_url) or raw_url

    candidate = unquote(candidate).strip()
    if not candidate:
        return None

    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower()
    normalized_host = FACEBOOK_HOST_ALIASES.get(host)
    if normalized_host is None:
        return None

    cleaned_query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key not in TRACKING_QUERY_PARAMS and not key.startswith("__")
        for value in values
    ]
    query_string = "&".join(
        f"{quote_plus(key)}={quote_plus(value)}" for key, value in sorted(cleaned_query)
    )

    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", normalized_host, path, query_string, ""))


def normalize_url(raw_url: str) -> str | None:
    return extract_facebook_url(raw_url)


def strip_tracking_params(raw_url: str) -> str | None:
    normalized = normalize_url(raw_url)
    if normalized is None:
        return None

    parsed = urlsplit(normalized)
    cleaned_query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key not in TRACKING_QUERY_PARAMS and not key.startswith("__")
        for value in values
    ]
    query_string = urlencode(cleaned_query, doseq=True, quote_via=quote_plus)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") or "/",
            query_string,
            "",
        )
    )


def decode_facebook_redirect_url(raw_url: str) -> str | None:
    if not raw_url:
        return None

    parsed = urlsplit(raw_url)
    if parsed.netloc.lower() not in {"l.facebook.com", "lm.facebook.com"}:
        return None

    target = parse_qs(parsed.query).get("u", [None])[0]
    if not target:
        return None
    return unquote(target).strip() or None


def extract_facebook_urls_from_text(raw_text: str) -> list[str]:
    if not raw_text:
        return []

    candidates = [raw_text, html.unescape(raw_text), unquote(raw_text)]
    normalized_urls: list[str] = []
    seen: set[str] = set()

    for candidate_text in candidates:
        prepared_text = (
            candidate_text.replace("\\/", "/")
            .replace("\\u0026", "&")
            .replace("%2F", "/")
            .replace("%3A", ":")
        )
        for match in FACEBOOK_URL_PATTERN.findall(prepared_text):
            cleaned = match
            normalized = normalize_url(cleaned)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            normalized_urls.append(normalized)

    return normalized_urls


def _extract_candidate_from_google_redirect(raw_url: str) -> str | None:
    parsed = urlsplit(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path

    is_relative_google_redirect = raw_url.startswith("/url?") or raw_url.startswith("./url?")
    is_absolute_google_redirect = host in GOOGLE_REDIRECT_HOSTS and path == "/url"

    if not is_relative_google_redirect and not is_absolute_google_redirect:
        return None

    query = parse_qs(parsed.query)
    return query.get("q", query.get("url", [None]))[0] or ""
