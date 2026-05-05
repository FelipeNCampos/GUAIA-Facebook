from __future__ import annotations

import pytest
from db.migration_runtime import connect_with_retry
from sqlalchemy.exc import OperationalError


def test_connect_with_retry_succeeds_after_transient_failure(monkeypatch):
    attempts = {"count": 0}

    def fake_sleep(_: float) -> None:
        return None

    def connect():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OperationalError("select 1", {}, OSError("connection refused"))
        return "connected"

    monkeypatch.setattr("db.migration_runtime.time.sleep", fake_sleep)

    result = connect_with_retry(connect, attempts=3, delay_seconds=0)

    assert result == "connected"
    assert attempts["count"] == 3


def test_connect_with_retry_raises_after_exhausting_attempts(monkeypatch):
    def fake_sleep(_: float) -> None:
        return None

    def connect():
        raise OperationalError("select 1", {}, OSError("connection refused"))

    monkeypatch.setattr("db.migration_runtime.time.sleep", fake_sleep)

    with pytest.raises(OperationalError):
        connect_with_retry(connect, attempts=2, delay_seconds=0)
