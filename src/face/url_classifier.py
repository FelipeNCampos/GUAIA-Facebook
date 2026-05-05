from __future__ import annotations


def classify_url(url: str) -> str:
    if "/posts/" in url:
        return "post"
    if "/permalink/" in url:
        return "post"
    if "/videos/" in url:
        return "video"
    if "/watch/" in url:
        return "video"
    if "/reel/" in url or "/reels/" in url:
        return "reel"
    if "/groups/" in url:
        return "group"
    if "/events/" in url:
        return "event"
    if "/photos/" in url:
        return "photo"
    return "page"
