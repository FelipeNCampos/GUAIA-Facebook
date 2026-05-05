from __future__ import annotations

import scrapy
from common.logging import get_logger

logger = get_logger(__name__)


class FacebookEnrichSpider(scrapy.Spider):
    name = "facebook_enrich"

    def start_requests(self):  # type: ignore[no-untyped-def]
        logger.info("FacebookEnrichSpider scaffold ready")
        if False:
            yield None


if __name__ == "__main__":
    print("FacebookEnrichSpider scaffold ready")
