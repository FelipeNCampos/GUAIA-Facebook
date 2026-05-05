from __future__ import annotations

import scrapy


class FacebookURLItem(scrapy.Item):
    item_type = scrapy.Field()
    id_query = scrapy.Field()
    url = scrapy.Field()
    url_normalized = scrapy.Field()
    category = scrapy.Field()
    query_source = scrapy.Field()
    search_page = scrapy.Field()
    search_position = scrapy.Field()
    source_query = scrapy.Field()
    discovered_via = scrapy.Field()
    record_id = scrapy.Field()


class FacebookRecordItem(scrapy.Item):
    item_type = scrapy.Field()
    id_query = scrapy.Field()
    url = scrapy.Field()
    url_normalized = scrapy.Field()
    category = scrapy.Field()
    query_source = scrapy.Field()
    record_id = scrapy.Field()
    status = scrapy.Field()
    payload = scrapy.Field()
    last_error = scrapy.Field()
