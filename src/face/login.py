from __future__ import annotations

from datetime import UTC, datetime

from db.models import FaceSessionCookie
from sqlalchemy import select
from sqlalchemy.orm import Session


def load_cookies_from_db(session: Session, profile_name: str) -> list[dict[str, object]]:
    rows = session.scalars(
        select(FaceSessionCookie).where(
            FaceSessionCookie.profile_name == profile_name,
            FaceSessionCookie.is_active.is_(True),
        )
    ).all()
    cookies: list[dict[str, object]] = []
    for row in rows:
        if row.expires_at is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                continue

        cookie = {
            "name": row.cookie_name,
            "value": row.cookie_value,
            "domain": row.domain,
            "path": row.path,
        }
        if row.expires_at is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            cookie["expires"] = int(expires_at.timestamp())
        cookies.append(cookie)
    return cookies
