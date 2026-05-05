from __future__ import annotations

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from face.spiders import settings as spider_settings_module
from face.spiders.facebook_enrich import FacebookEnrichSpider
from face.spiders.google_search import GoogleSearchSpider


def build_spider_settings() -> Settings:
    settings = Settings()
    settings.setmodule(spider_settings_module)
    return settings


def _run_spider(
    spider_cls: type[GoogleSearchSpider] | type[FacebookEnrichSpider],
    job_payload: dict[str, object],
) -> None:
    process = CrawlerProcess(settings=build_spider_settings())
    process.crawl(spider_cls, **job_payload)
    process.start(stop_after_crawl=True)
    if process.bootstrap_failed:
        raise RuntimeError(f"{spider_cls.name} spider bootstrap failed")


def run_google_search_spider(job_payload: dict[str, object]) -> None:
    _run_spider(GoogleSearchSpider, job_payload)


def run_facebook_enrich_spider(job_payload: dict[str, object]) -> None:
    _run_spider(FacebookEnrichSpider, job_payload)
