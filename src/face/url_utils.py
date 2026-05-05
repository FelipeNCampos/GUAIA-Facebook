from __future__ import annotations

from urllib.parse import parse_qs, quote_plus, unquote, urlsplit, urlunsplit

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


def build_google_search_url(
    *,
    subject: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    query_parts = [f'site:facebook.com "{subject}"']
    if start_date:
        query_parts.append(f"after:{start_date}")
    if end_date:
        query_parts.append(f"before:{end_date}")
    return f"https://www.google.com/search?q={quote_plus(' '.join(query_parts))}"


def extract_facebook_url(raw_url: str) -> str | None:
    if not raw_url:
        return None

    candidate = raw_url
    if raw_url.startswith("/url?"):
        query = parse_qs(urlsplit(raw_url).query)
        candidate = query.get("q", query.get("url", [None]))[0] or ""

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
