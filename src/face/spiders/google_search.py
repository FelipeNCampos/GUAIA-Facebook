from __future__ import annotations

import scrapy
from common.logging import get_logger

logger = get_logger(__name__)


class GoogleSearchSpider(scrapy.Spider):
    name = "google_search"

    def start_requests(self):  # type: ignore[no-untyped-def]
        logger.info("GoogleSearchSpider scaffold ready")
        if False:
            yield None


if __name__ == "__main__":
    print("GoogleSearchSpider scaffold ready")
