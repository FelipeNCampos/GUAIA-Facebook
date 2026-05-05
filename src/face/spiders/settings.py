from __future__ import annotations

import platform

from face.config import get_settings

settings = get_settings()

BOT_NAME = "face"
SPIDER_MODULES = ["face.spiders"]
NEWSPIDER_MODULE = "face.spiders"

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": settings.playwright_headless,
    "args": [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ],
}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30000

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
if platform.system() != "Windows":
    ASYNCIO_EVENT_LOOP = "uvloop"

CONCURRENT_REQUESTS = settings.scrapy_concurrent_requests
CONCURRENT_REQUESTS_PER_DOMAIN = settings.scrapy_concurrent_requests_per_domain
DOWNLOAD_DELAY = settings.scrapy_download_delay
RANDOMIZE_DOWNLOAD_DELAY = settings.scrapy_randomize_download_delay
AUTOTHROTTLE_ENABLED = settings.scrapy_autothrottle_enabled
AUTOTHROTTLE_TARGET_CONCURRENCY = settings.scrapy_autothrottle_target_concurrency
RETRY_ENABLED = settings.scrapy_retry_enabled
RETRY_TIMES = settings.scrapy_retry_times
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]
RETRY_EXCEPTIONS = [
    "scrapy.core.downloader.handlers.http11.TunnelError",
    "twisted.internet.error.TimeoutError",
]
CLOSESPIDER_ERRORCOUNT = settings.scrapy_closespider_errorcount

ITEM_PIPELINES = {
    "face.pipelines.cache.CachePipeline": 100,
    "face.pipelines.persist.PersistPipeline": 200,
    "face.pipelines.events.EventsPipeline": 300,
}

DOWNLOADER_MIDDLEWARES = {
    "face.middlewares.proxy.ProxyRotationMiddleware": 500,
    "face.middlewares.retry.RetryToDlqMiddleware": 550,
}
