from __future__ import annotations

import asyncio
import json
import subprocess

from face.queues import ConsumedMessage
from face.worker_runtime import run_search_worker_loop


class FakeConsumer:
    def __init__(self, messages: list[ConsumedMessage | None]) -> None:
        self.messages = messages
        self.calls = 0

    async def get_json_message(self, queue_name: str, *, timeout_seconds: float | None = None):
        self.calls += 1
        if self.messages:
            return self.messages.pop(0)
        return None


def test_search_worker_loop_dispatches_message_and_acks(monkeypatch) -> None:
    acked = {"value": False}
    rejected = {"value": False}

    async def ack() -> None:
        acked["value"] = True

    async def reject(requeue: bool) -> None:
        rejected["value"] = requeue

    message = ConsumedMessage(
        payload={"id_query": "job-1", "subject": "tema"},
        ack=ack,
        reject=reject,
    )
    consumer = FakeConsumer([message, None])

    def fake_run(args, check, env):  # type: ignore[no-untyped-def]
        assert args[-1] == "search"
        assert json.loads(env["FACE_SEARCH_JOB_JSON"])["id_query"] == "job-1"
        import face.worker_runtime as worker_runtime

        worker_runtime.running = False
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr("face.worker_runtime.subprocess.run", fake_run)

    import face.worker_runtime as worker_runtime

    worker_runtime.running = True
    asyncio.run(run_search_worker_loop(consumer, poll_interval_seconds=0))

    assert acked["value"] is True
    assert rejected["value"] is False


def test_search_worker_loop_rejects_message_on_failure(monkeypatch) -> None:
    acked = {"value": False}
    rejected = {"value": None}

    async def ack() -> None:
        acked["value"] = True

    async def reject(requeue: bool) -> None:
        rejected["value"] = requeue

    message = ConsumedMessage(
        payload={"id_query": "job-2", "subject": "tema"},
        ack=ack,
        reject=reject,
    )
    consumer = FakeConsumer([message])

    def fake_run(args, check, env):  # type: ignore[no-untyped-def]
        import face.worker_runtime as worker_runtime

        worker_runtime.running = False
        raise subprocess.CalledProcessError(returncode=1, cmd=args)

    monkeypatch.setattr("face.worker_runtime.subprocess.run", fake_run)

    import face.worker_runtime as worker_runtime

    worker_runtime.running = True
    asyncio.run(run_search_worker_loop(consumer, poll_interval_seconds=0))

    assert acked["value"] is False
    assert rejected["value"] is True
