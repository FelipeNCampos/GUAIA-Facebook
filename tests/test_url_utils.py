from __future__ import annotations

from face.url_classifier import classify_url
from face.url_utils import (
    build_search_query,
    build_searxng_search_url,
    decode_facebook_redirect_url,
    extract_facebook_urls_from_text,
    normalize_url,
    strip_tracking_params,
)


def test_build_search_query_targets_facebook_without_date_filters() -> None:
    query = build_search_query("candidato teste")

    assert query == 'site:facebook.com "candidato teste"'
    assert "after:" not in query
    assert "before:" not in query


def test_build_searxng_search_url_uses_json_api_parameters() -> None:
    url = build_searxng_search_url(
        base_url="http://searxng:8080",
        subject=build_search_query("candidato teste"),
        enabled_engines="google,bing",
        page_number=2,
    )

    assert url.startswith("http://searxng:8080/search?")
    assert "site%3Afacebook.com" in url
    assert "candidato+teste" in url
    assert "format=json" in url
    assert "engines=google%2Cbing" in url
    assert "categories=general" in url
    assert "pageno=2" in url
    assert "count=10" in url


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
    assert classify_url("https://www.facebook.com/reel/123456") == "reel"
    assert classify_url("https://www.facebook.com/foo") == "page"


def test_strip_tracking_params_removes_classification_noise() -> None:
    url = (
        "https://www.facebook.com/reel/3943180995975088"
        "?locale=pt_BR&set=a.1&__cft__[0]=tracking&__tn__=R"
    )

    stripped = strip_tracking_params(url)

    assert stripped == "https://www.facebook.com/reel/3943180995975088"


def test_decode_facebook_redirect_url_extracts_external_target() -> None:
    raw = (
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.org%2Flanding%3Fa%3D1"
        "&h=tracking"
    )

    decoded = decode_facebook_redirect_url(raw)

    assert decoded == "https://example.org/landing?a=1"
