from __future__ import annotations

from face.url_classifier import classify_url
from face.url_utils import (
    build_bing_search_url,
    build_google_custom_search_url,
    build_google_search_url,
    extract_facebook_urls_from_text,
    normalize_url,
)


def test_build_google_search_url_includes_subject_and_dates() -> None:
    url = build_google_search_url(
        subject="candidato teste",
        start_date="2026-05-01",
        end_date="2026-05-05",
    )

    assert "site%3Afacebook.com" in url
    assert "candidato+teste" in url
    assert "after%3A2026-05-01" in url
    assert "before%3A2026-05-05" in url
    assert "hl=pt-BR" in url
    assert "gl=br" in url
    assert "num=10" in url
    assert "pws=0" in url
    assert "filter=0" in url


def test_build_google_custom_search_url_uses_official_api_parameters() -> None:
    url = build_google_custom_search_url(
        api_key="api-key",
        search_engine_id="engine-id",
        subject="candidato teste",
        start_date="2026-05-01",
        end_date="2026-05-05",
        start_index=11,
        results_per_page=20,
    )

    assert url.startswith("https://customsearch.googleapis.com/customsearch/v1?")
    assert "key=api-key" in url
    assert "cx=engine-id" in url
    assert "site%3Afacebook.com" in url
    assert "num=10" in url
    assert "start=11" in url


def test_build_bing_search_url_includes_subject_and_dates() -> None:
    url = build_bing_search_url(
        subject="candidato teste",
        start_date="2026-05-01",
        end_date="2026-05-05",
        first_result=11,
    )

    assert url.startswith("https://www.bing.com/search?")
    assert "site%3Afacebook.com" in url
    assert "candidato+teste" in url
    assert "after%3A2026-05-01" in url
    assert "before%3A2026-05-05" in url
    assert "setlang=pt-BR" in url
    assert "cc=BR" in url
    assert "count=10" in url
    assert "first=11" in url


def test_normalize_url_extracts_google_redirect_and_removes_tracking() -> None:
    raw = (
        "/url?q=https%3A%2F%2Fm.facebook.com%2Ffoo%2Fposts%2F123%2F%3Frefsrc%3Ddeprecated"
        "%26__tn__%3DR%26fbclid%3Dtracking"
    )

    normalized = normalize_url(raw)

    assert normalized == "https://www.facebook.com/foo/posts/123"


def test_normalize_url_extracts_absolute_google_redirect_url() -> None:
    raw = (
        "https://www.google.com/url?sa=t&url=https%3A%2F%2Fwww.facebook.com%2Ffoo%2Fposts"
        "%2F123%3Ffbclid%3Dtracking"
    )

    normalized = normalize_url(raw)

    assert normalized == "https://www.facebook.com/foo/posts/123"


def test_normalize_url_extracts_dot_relative_google_redirect_url() -> None:
    raw = (
        "./url?esrc=s&q=&rct=j&sa=U&url=https%3A%2F%2Fm.facebook.com%2Fbar%2Fvideos%2F456"
        "%2F%3F__tn__%3DR"
    )

    normalized = normalize_url(raw)

    assert normalized == "https://www.facebook.com/bar/videos/456"


def test_extract_facebook_urls_from_text_supports_escaped_urls() -> None:
    raw_text = (
        '<script>var data = {"link":"https:\\/\\/m.facebook.com\\/foo\\/posts\\/123\\/?ref=watch"};'
        'var next = "https%3A%2F%2Fwww.facebook.com%2Fbar%2Fvideos%2F456%3F__tn__%3DR";</script>'
    )

    urls = extract_facebook_urls_from_text(raw_text)

    assert urls == [
        "https://www.facebook.com/foo/posts/123",
        "https://www.facebook.com/bar/videos/456",
    ]


def test_classify_url_supports_post_video_and_group() -> None:
    assert classify_url("https://www.facebook.com/foo/posts/123") == "post"
    assert classify_url("https://www.facebook.com/foo/videos/456") == "video"
    assert classify_url("https://www.facebook.com/groups/bar") == "group"
