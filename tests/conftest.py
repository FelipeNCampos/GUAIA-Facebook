from __future__ import annotations

import os

import pytest
from face.config import get_settings


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def integration_database_url() -> str:
    value = os.getenv("FACE_INTEGRATION_DATABASE_URL")
    if not value:
        pytest.skip("FACE_INTEGRATION_DATABASE_URL is not configured")
    return value


@pytest.fixture
def integration_rabbitmq_url() -> str:
    value = os.getenv("FACE_INTEGRATION_RABBITMQ_URL")
    if not value:
        pytest.skip("FACE_INTEGRATION_RABBITMQ_URL is not configured")
    return value


@pytest.fixture
def integration_service_environment(
    monkeypatch,
    integration_database_url: str,
    integration_rabbitmq_url: str,
):
    monkeypatch.setenv("DATABASE_URL", integration_database_url)
    monkeypatch.setenv("RABBITMQ_URL", integration_rabbitmq_url)
    monkeypatch.setenv("SCRAPY_DOWNLOAD_DELAY", "0")
    monkeypatch.setenv("SCRAPY_RANDOMIZE_DOWNLOAD_DELAY", "false")
    monkeypatch.setenv("SCRAPY_AUTOTHROTTLE_ENABLED", "false")
    monkeypatch.setenv("SCRAPY_RETRY_ENABLED", "false")
    monkeypatch.setenv("SCRAPY_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
