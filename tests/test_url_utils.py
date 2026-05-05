from __future__ import annotations

from face.url_classifier import classify_url
from face.url_utils import build_google_search_url, normalize_url


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


def test_normalize_url_extracts_google_redirect_and_removes_tracking() -> None:
    raw = (
        "/url?q=https%3A%2F%2Fm.facebook.com%2Ffoo%2Fposts%2F123%2F%3Frefsrc%3Ddeprecated"
        "%26__tn__%3DR%26fbclid%3Dtracking"
    )

    normalized = normalize_url(raw)

    assert normalized == "https://www.facebook.com/foo/posts/123"


def test_classify_url_supports_post_video_and_group() -> None:
    assert classify_url("https://www.facebook.com/foo/posts/123") == "post"
    assert classify_url("https://www.facebook.com/foo/videos/456") == "video"
    assert classify_url("https://www.facebook.com/groups/bar") == "group"
