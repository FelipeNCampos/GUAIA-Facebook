from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_PARAMS = {
    "__cft__",
    "__tn__",
    "eid",
    "fbclid",
    "locale2",
    "paipv",
    "ref",
    "refid",
    "refsrc",
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


def build_google_search_url(
    *,
    subject: str,
    start_date: str | None = None,
    end_date: str | None = None,
    language: str = "pt-BR",
    region: str = "br",
    results_per_page: int = 10,
) -> str:
    query_parts = [f'site:facebook.com "{subject}"']
    if start_date:
        query_parts.append(f"after:{start_date}")
    if end_date:
        query_parts.append(f"before:{end_date}")
    params = {
        "q": " ".join(query_parts),
        "hl": language,
        "gl": region,
        "num": max(1, results_per_page),
        "pws": "0",
        "filter": "0",
    }
    return f"https://www.google.com/search?{urlencode(params, quote_via=quote_plus)}"


def build_google_custom_search_url(
    *,
    api_key: str,
    search_engine_id: str,
    subject: str,
    start_date: str | None = None,
    end_date: str | None = None,
    language: str = "pt-BR",
    region: str = "br",
    results_per_page: int = 10,
    start_index: int = 1,
) -> str:
    query_parts = [f'site:facebook.com "{subject}"']
    if start_date:
        query_parts.append(f"after:{start_date}")
    if end_date:
        query_parts.append(f"before:{end_date}")
    params = {
        "key": api_key,
        "cx": search_engine_id,
        "q": " ".join(query_parts),
        "hl": language,
        "gl": region,
        "num": min(max(1, results_per_page), 10),
        "start": max(1, start_index),
        "safe": "off",
    }
    query_string = urlencode(params, quote_via=quote_plus)
    return f"https://customsearch.googleapis.com/customsearch/v1?{query_string}"


def build_bing_search_url(
    *,
    subject: str,
    start_date: str | None = None,
    end_date: str | None = None,
    language: str = "pt-BR",
    region: str = "br",
    results_per_page: int = 10,
    first_result: int = 1,
) -> str:
    query_parts = [f'site:facebook.com "{subject}"']
    if start_date:
        query_parts.append(f"after:{start_date}")
    if end_date:
        query_parts.append(f"before:{end_date}")
    params = {
        "q": " ".join(query_parts),
        "setlang": language,
        "cc": region.upper(),
        "count": max(1, results_per_page),
        "first": max(1, first_result),
    }
    return f"https://www.bing.com/search?{urlencode(params, quote_via=quote_plus)}"


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
