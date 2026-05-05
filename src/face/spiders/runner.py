from __future__ import annotations

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from face.spiders import settings as spider_settings_module
from face.spiders.google_search import GoogleSearchSpider


def build_spider_settings() -> Settings:
    settings = Settings()
    settings.setmodule(spider_settings_module)
    return settings


def run_google_search_spider(job_payload: dict[str, object]) -> None:
    process = CrawlerProcess(settings=build_spider_settings())
    process.crawl(GoogleSearchSpider, **job_payload)
    process.start(stop_after_crawl=True)
