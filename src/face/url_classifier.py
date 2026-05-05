from __future__ import annotations


def classify_url(url: str) -> str:
    if "/posts/" in url:
        return "post"
    if "/videos/" in url:
        return "video"
    return "page"
