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
    monkeypatch.setenv("TWOCAPTCHA_API_KEY", "test-key")
    monkeypatch.setenv("TWOCAPTCHA_ENABLED", "true")
    monkeypatch.setenv("TWOCAPTCHA_MAX_SOLVES_PER_RUN", "4")
    monkeypatch.setenv("TWOCAPTCHA_REQUEST_TIMEOUT", "90")
    monkeypatch.setenv("TWOCAPTCHA_POLL_INTERVAL", "7")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql+psycopg://user:pass@db:5432/app"
    assert settings.rabbitmq_url == "amqp://user:pass@mq:5672/"
    assert settings.scrapy_concurrent_requests == 32
    assert settings.search_max_pages == 9
    assert settings.twocaptcha_api_key == "test-key"
    assert settings.twocaptcha_enabled is True
    assert settings.twocaptcha_max_solves_per_run == 4
    assert settings.twocaptcha_request_timeout == 90
    assert settings.twocaptcha_poll_interval == 7


def test_repository_requires_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@mq:5672/")

    settings = Settings()

    with pytest.raises(ValueError, match="DATABASE_URL must be configured"):
        create_engine_from_settings(settings)
