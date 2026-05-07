from __future__ import annotations

import pytest
from face.config import Settings
from face.repository import create_engine_from_settings


def test_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db:5432/app")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@mq:5672/")
    monkeypatch.setenv("SCRAPY_CONCURRENT_REQUESTS", "32")
    monkeypatch.setenv("SEARCH_MAX_PAGES", "9")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql+psycopg://user:pass@db:5432/app"
    assert settings.rabbitmq_url == "amqp://user:pass@mq:5672/"
    assert settings.scrapy_concurrent_requests == 32
    assert settings.search_max_pages == 9


def test_repository_requires_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@mq:5672/")

    settings = Settings()

    with pytest.raises(ValueError, match="DATABASE_URL must be configured"):
        create_engine_from_settings(settings)
