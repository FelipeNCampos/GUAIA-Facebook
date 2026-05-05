from __future__ import annotations


class RetryToDlqMiddleware:
    def process_exception(self, request, exception, spider):  # type: ignore[no-untyped-def]
        return None
