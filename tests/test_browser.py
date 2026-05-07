from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from db.base import Base
from db.models import FaceSessionCookie
from face.browser import (
    AUTHENTICATED_CONTEXT_NAME,
    authenticated_context_kwargs,
    create_authenticated_context,
    playwright_launch_options,
)
from face.config import Settings
from face.spiders import settings as spider_settings
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class FakeBrowserContext:
    def __init__(self) -> None:
        self.cookies: list[dict[str, object]] = []

    async def add_cookies(self, cookies: list[dict[str, object]]) -> None:
        self.cookies.extend(cookies)


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_authenticated_context_kwargs_uses_user_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_USER_DATA_DIR", "/tmp/custom-session")

    from face.config import get_settings

    get_settings.cache_clear()
    kwargs = authenticated_context_kwargs()

    assert kwargs == {"user_data_dir": "/tmp/custom-session"}

    get_settings.cache_clear()


def test_create_authenticated_context_loads_active_unexpired_cookies(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cookies.db"
    session_factory = build_session_factory(f"sqlite:///{db_path}")
    now = datetime.now(UTC)

    with session_factory() as session:
        session.add_all(
            [
                FaceSessionCookie(
                    profile_name="test-profile",
                    cookie_name="c_user",
                    cookie_value="123",
                    domain=".facebook.com",
                    path="/",
                    expires_at=now + timedelta(days=1),
                    is_active=True,
                ),
                FaceSessionCookie(
                    profile_name="test-profile",
                    cookie_name="expired_cookie",
                    cookie_value="456",
                    domain=".facebook.com",
                    path="/",
                    expires_at=now - timedelta(days=1),
                    is_active=True,
                ),
                FaceSessionCookie(
                    profile_name="other-profile",
                    cookie_name="other_cookie",
                    cookie_value="789",
                    domain=".facebook.com",
                    path="/",
                    expires_at=now + timedelta(days=1),
                    is_active=True,
                ),
            ]
        )
        session.commit()

    monkeypatch.setenv("FACEBOOK_SESSION_PROFILE", "test-profile")

    from face.config import get_settings

    get_settings.cache_clear()
    context = FakeBrowserContext()

    asyncio.run(create_authenticated_context(context, session_factory=session_factory))

    assert len(context.cookies) == 1
    assert context.cookies[0]["name"] == "c_user"
    assert context.cookies[0]["value"] == "123"
    assert context.cookies[0]["domain"] == ".facebook.com"
    assert "expires" in context.cookies[0]

    get_settings.cache_clear()


def test_authenticated_context_name_constant() -> None:
    assert AUTHENTICATED_CONTEXT_NAME == "authenticated"


def test_playwright_launch_options_force_headless_without_display(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("face.browser.os.name", "posix")

    launch_options = playwright_launch_options(
        Settings(PLAYWRIGHT_HEADLESS=False, PLAYWRIGHT_HEADLESS_MODE="auto")
    )

    assert launch_options["headless"] is True


def test_playwright_launch_options_keep_headed_on_windows_without_display(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("face.browser.os.name", "nt")

    launch_options = playwright_launch_options(
        Settings(PLAYWRIGHT_HEADLESS=False, PLAYWRIGHT_HEADLESS_MODE="auto")
    )

    assert launch_options["headless"] is False


def test_playwright_launch_options_allow_explicit_headed_mode(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("face.browser.os.name", "posix")

    launch_options = playwright_launch_options(
        Settings(PLAYWRIGHT_HEADLESS=False, PLAYWRIGHT_HEADLESS_MODE="headed")
    )

    assert launch_options["headless"] is False


def test_playwright_launch_options_allow_explicit_headless_mode(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("face.browser.os.name", "nt")

    launch_options = playwright_launch_options(
        Settings(PLAYWRIGHT_HEADLESS=False, PLAYWRIGHT_HEADLESS_MODE="headless")
    )

    assert launch_options["headless"] is True


def test_spider_settings_define_authenticated_playwright_context() -> None:
    assert "authenticated" in spider_settings.PLAYWRIGHT_CONTEXTS
    assert spider_settings.PLAYWRIGHT_CONTEXTS["authenticated"] == authenticated_context_kwargs()
