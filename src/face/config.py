from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from face.scrapy_runtime import resolve_asyncio_event_loop_path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    app_log_json: bool = Field(default=True, alias="APP_LOG_JSON")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    database_url: str = Field(default="", alias="DATABASE_URL")
    rabbitmq_url: str = Field(default="", alias="RABBITMQ_URL")

    facebook_session_profile: str = Field(default="default", alias="FACEBOOK_SESSION_PROFILE")
    playwright_headless: bool = Field(default=False, alias="PLAYWRIGHT_HEADLESS")
    playwright_headless_mode: str = Field(
        default="auto",
        alias="PLAYWRIGHT_HEADLESS_MODE",
    )
    playwright_browsers_path: str = Field(
        default="/ms-playwright", alias="PLAYWRIGHT_BROWSERS_PATH"
    )
    playwright_user_data_dir: str = Field(
        default="./session_data", alias="PLAYWRIGHT_USER_DATA_DIR"
    )

    searxng_internal_url: str = Field(
        default="http://searxng:8080",
        alias="SEARXNG_INTERNAL_URL",
    )
    searxng_search_language: str = Field(
        default="pt-BR",
        alias="SEARXNG_SEARCH_LANGUAGE",
    )
    searxng_search_region: str = Field(default="br", alias="SEARXNG_SEARCH_REGION")
    searxng_search_category: str = Field(
        default="general",
        alias="SEARXNG_SEARCH_CATEGORY",
    )
    searxng_enabled_engines: str = Field(
        default="google,bing",
        alias="SEARXNG_ENABLED_ENGINES",
    )
    searxng_safe_search: int = Field(default=0, alias="SEARXNG_SAFE_SEARCH")
    searxng_results_per_page: int = Field(
        default=10,
        alias="SEARXNG_RESULTS_PER_PAGE",
    )
    search_max_pages: int = Field(default=5, alias="SEARCH_MAX_PAGES")
    search_block_retry_limit: int = Field(default=2, alias="SEARCH_BLOCK_RETRY_LIMIT")
    search_download_delay: float = Field(default=1.0, alias="SEARCH_DOWNLOAD_DELAY")
    search_concurrent_requests_per_domain: int = Field(
        default=2,
        alias="SEARCH_CONCURRENT_REQUESTS_PER_DOMAIN",
    )
    search_autothrottle_target_concurrency: float = Field(
        default=1.0,
        alias="SEARCH_AUTOTHROTTLE_TARGET_CONCURRENCY",
    )
    search_autothrottle_start_delay: float = Field(
        default=1.0,
        alias="SEARCH_AUTOTHROTTLE_START_DELAY",
    )
    search_autothrottle_max_delay: float = Field(
        default=30.0,
        alias="SEARCH_AUTOTHROTTLE_MAX_DELAY",
    )

    scrapy_concurrent_requests: int = Field(default=16, alias="SCRAPY_CONCURRENT_REQUESTS")
    scrapy_concurrent_requests_per_domain: int = Field(
        default=4,
        alias="SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN",
    )
    scrapy_download_delay: float = Field(default=2.0, alias="SCRAPY_DOWNLOAD_DELAY")
    scrapy_randomize_download_delay: bool = Field(
        default=True, alias="SCRAPY_RANDOMIZE_DOWNLOAD_DELAY"
    )
    scrapy_autothrottle_enabled: bool = Field(default=True, alias="SCRAPY_AUTOTHROTTLE_ENABLED")
    scrapy_autothrottle_target_concurrency: float = Field(
        default=2.0,
        alias="SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY",
    )
    scrapy_retry_enabled: bool = Field(default=True, alias="SCRAPY_RETRY_ENABLED")
    scrapy_retry_times: int = Field(default=3, alias="SCRAPY_RETRY_TIMES")
    scrapy_closespider_errorcount: int = Field(default=50, alias="SCRAPY_CLOSESPIDER_ERRORCOUNT")

    @property
    def scrapy_settings(self) -> dict[str, object]:
        settings: dict[str, object] = {
            "CONCURRENT_REQUESTS": self.scrapy_concurrent_requests,
            "CONCURRENT_REQUESTS_PER_DOMAIN": self.scrapy_concurrent_requests_per_domain,
            "DOWNLOAD_DELAY": self.scrapy_download_delay,
            "RANDOMIZE_DOWNLOAD_DELAY": self.scrapy_randomize_download_delay,
            "AUTOTHROTTLE_ENABLED": self.scrapy_autothrottle_enabled,
            "AUTOTHROTTLE_TARGET_CONCURRENCY": self.scrapy_autothrottle_target_concurrency,
            "RETRY_ENABLED": self.scrapy_retry_enabled,
            "RETRY_TIMES": self.scrapy_retry_times,
            "CLOSESPIDER_ERRORCOUNT": self.scrapy_closespider_errorcount,
            "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        }
        asyncio_event_loop = resolve_asyncio_event_loop_path()
        if asyncio_event_loop is not None:
            settings["ASYNCIO_EVENT_LOOP"] = asyncio_event_loop
        return settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
