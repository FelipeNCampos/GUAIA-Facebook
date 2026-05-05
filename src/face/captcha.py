from __future__ import annotations


def is_captcha_url(url: str) -> bool:
    lowered = url.lower()
    return "captcha" in lowered or "checkpoint" in lowered
