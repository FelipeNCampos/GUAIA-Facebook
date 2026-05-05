from __future__ import annotations

from scrapy_playwright.handler import ScrapyPlaywrightDownloadHandler

from face.browser import (
    AUTHENTICATED_CONTEXT_NAME,
    authenticated_context_kwargs,
    create_authenticated_context,
)


class AuthenticatedScrapyPlaywrightDownloadHandler(ScrapyPlaywrightDownloadHandler):
    async def _create_browser_context(
        self,
        name: str,
        context_kwargs: dict | None,
        spider=None,  # type: ignore[no-untyped-def]
    ):
        if name == AUTHENTICATED_CONTEXT_NAME:
            merged_context_kwargs = {
                **authenticated_context_kwargs(),
                **(context_kwargs or {}),
            }
            context_wrapper = await super()._create_browser_context(
                name=name,
                context_kwargs=merged_context_kwargs,
                spider=spider,
            )
            await create_authenticated_context(context_wrapper.context)
            return context_wrapper

        return await super()._create_browser_context(
            name=name,
            context_kwargs=context_kwargs,
            spider=spider,
        )
