from __future__ import annotations

import json
import logging

from common.logging import JsonFormatter, clear_log_context, set_log_context


def test_json_logging_includes_id_query():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test-json-logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="structured log",
        args=(),
        exc_info=None,
    )

    set_log_context(id_query="query-123")
    rendered = formatter.format(record)
    clear_log_context()

    payload = json.loads(rendered)
    assert payload["message"] == "structured log"
    assert payload["id_query"] == "query-123"
