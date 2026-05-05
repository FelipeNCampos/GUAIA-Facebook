from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from face.config import Settings, get_settings


def create_engine_from_settings(settings: Settings | None = None) -> Engine:
    resolved = settings or get_settings()
    if not resolved.database_url:
        raise ValueError("DATABASE_URL must be configured via environment variable")
    return create_engine(resolved.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    engine = create_engine_from_settings(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
