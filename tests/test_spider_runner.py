from __future__ import annotations

import types

import pytest
from face.scrapy_runtime import resolve_asyncio_event_loop_path
from face.spiders.runner import run_google_search_spider


def test_resolve_asyncio_event_loop_path_uses_full_uvloop_import_path(monkeypatch) -> None:
    monkeypatch.setattr("face.scrapy_runtime.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "face.scrapy_runtime.importlib.util.find_spec",
        lambda name: object() if name == "uvloop" else None,
    )

    assert resolve_asyncio_event_loop_path() == "uvloop.Loop"


def test_resolve_asyncio_event_loop_path_is_disabled_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("face.scrapy_runtime.platform.system", lambda: "Windows")

    assert resolve_asyncio_event_loop_path() is None


def test_run_google_search_spider_raises_when_bootstrap_fails(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class FakeCrawlerProcess:
        def __init__(self, settings) -> None:
            self.settings = settings
            self.bootstrap_failed = False

        def crawl(self, spider_cls, **job_payload) -> None:
            events.append(("crawl", spider_cls.name, job_payload))

        def start(self, stop_after_crawl: bool = True) -> None:
            events.append(("start", stop_after_crawl))
            self.bootstrap_failed = True

    monkeypatch.setattr("face.spiders.runner.CrawlerProcess", FakeCrawlerProcess)
    monkeypatch.setattr(
        "face.spiders.runner.build_spider_settings",
        lambda: types.SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="google_search spider bootstrap failed"):
        run_google_search_spider({"id_query": "job-1", "subject": "tema", "query_source": "api"})

    assert events[0][0] == "crawl"
    assert events[1] == ("start", True)
