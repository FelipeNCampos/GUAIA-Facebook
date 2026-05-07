from __future__ import annotations

from urllib.parse import urlsplit


def classify_url(url: str) -> str:
    path = (urlsplit(url).path.rstrip("/") or "/").lower()

    if path.startswith("/reel/") or path.startswith("/reels/"):
        return "reel"
    if _is_video_path(path):
        return "video"
    if _is_post_path(path):
        return "post"
    if path.startswith("/groups/"):
        return "group"
    if path.startswith("/events/"):
        return "event"
    if path.startswith("/photos/"):
        return "photo"
    if _is_page_path(path):
        return "page"
    return "page"


def _is_video_path(path: str) -> bool:
    if path == "/watch" or path.startswith("/watch/"):
        return True
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    if parts[0] == "videos" and len(parts) >= 2:
        return True
    return len(parts) >= 3 and parts[1] == "videos"


def _is_post_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    if parts[0] in {"posts", "permalink"} and len(parts) >= 2:
        return True
    return len(parts) >= 3 and parts[1] in {"posts", "permalink"}


def _is_page_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    if parts[0] in {
        "groups",
        "events",
        "photos",
        "videos",
        "watch",
        "reel",
        "reels",
        "posts",
        "permalink",
    }:
        return False
    return len(parts) == 1 or path == "/profile.php"
