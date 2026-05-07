from __future__ import annotations

import os

from sqlalchemy.orm import Session, sessionmaker

from face.config import Settings, get_settings

AUTHENTICATED_CONTEXT_NAME = "authenticated"


def _should_force_headless(resolved: Settings) -> bool:
    headless_mode = resolved.playwright_headless_mode.strip().lower()
    if headless_mode in {"headless", "true", "1"}:
        return True
    if headless_mode in {"headed", "false", "0"}:
        return False
    if resolved.playwright_headless:
        return True
    if os.name == "nt":
        return False
    return not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def playwright_launch_options(settings: Settings | None = None) -> dict[str, object]:
    resolved = settings or get_settings()
    return {
        "headless": _should_force_headless(resolved),
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    }


def playwright_context_kwargs(settings: Settings | None = None) -> dict[str, object]:
    resolved = settings or get_settings()
    return {
        "user_data_dir": resolved.playwright_user_data_dir,
    }


def authenticated_context_kwargs(settings: Settings | None = None) -> dict[str, object]:
    return playwright_context_kwargs(settings)


async def create_authenticated_context(
    context,  # type: ignore[no-untyped-def]
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
):
    resolved = settings or get_settings()

    if session_factory is None:
        from face.repository import create_session_factory

        session_factory = create_session_factory(resolved)

    from face.login import load_cookies_from_db

    with session_factory() as session:
        cookies = load_cookies_from_db(session, resolved.facebook_session_profile)

    if cookies:
        await context.add_cookies(cookies)

    return context
