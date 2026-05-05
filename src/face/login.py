from __future__ import annotations

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
        cookies.append(
            {
                "name": row.cookie_name,
                "value": row.cookie_value,
                "domain": row.domain,
                "path": row.path,
            }
        )
    return cookies
