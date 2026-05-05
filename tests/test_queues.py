from __future__ import annotations

import asyncio
import json

from face.queues import QueueNames, RabbitMQConsumer, RabbitMQInfrastructure


class FakeIncomingMessage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.acked = False
        self.rejected_with: bool | None = None

    async def ack(self) -> None:
        self.acked = True

    async def reject(self, *, requeue: bool) -> None:
        self.rejected_with = requeue


class FakeQueue:
    def __init__(self, message) -> None:
        self.message = message
        self.calls: list[tuple[float | None, bool]] = []

    async def get(self, timeout: float | None = None, fail: bool = False):
        self.calls.append((timeout, fail))
        return self.message


class FakeChannel:
    def __init__(self, queue: FakeQueue) -> None:
        self.queue = queue
        self.closed = False
        self.declared: list[tuple[str, bool]] = []

    async def declare_queue(self, queue_name: str, durable: bool = True) -> FakeQueue:
        self.declared.append((queue_name, durable))
        return self.queue

    async def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    async def channel(self) -> FakeChannel:
        return self._channel

    async def close(self) -> None:
        self.closed = True


class FakeConnectionFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.calls = 0

    async def connect(self) -> FakeConnection:
        self.calls += 1
        return self._connection


def test_consumer_closes_resources_when_queue_is_empty() -> None:
    queue = FakeQueue(message=None)
    channel = FakeChannel(queue)
    connection = FakeConnection(channel)
    consumer = RabbitMQConsumer(connection=FakeConnectionFactory(connection))

    result = asyncio.run(consumer.get_json_message("face.search.request", timeout_seconds=2.0))

    assert result is None
    assert channel.closed is True
    assert connection.closed is True
    assert queue.calls == [(2.0, False)]


def test_consumer_ack_closes_resources_after_message_processing() -> None:
    message = FakeIncomingMessage({"id_query": "job-1"})
    queue = FakeQueue(message=message)
    channel = FakeChannel(queue)
    connection = FakeConnection(channel)
    consumer = RabbitMQConsumer(connection=FakeConnectionFactory(connection))

    consumed = asyncio.run(consumer.get_json_message("face.search.request"))
    assert consumed is not None
    asyncio.run(consumed.ack())

    assert message.acked is True
    assert channel.closed is True
    assert connection.closed is True


def test_consumer_reject_closes_resources_after_message_processing() -> None:
    message = FakeIncomingMessage({"id_query": "job-2"})
    queue = FakeQueue(message=message)
    channel = FakeChannel(queue)
    connection = FakeConnection(channel)
    consumer = RabbitMQConsumer(connection=FakeConnectionFactory(connection))

    consumed = asyncio.run(consumer.get_json_message("face.search.request"))
    assert consumed is not None
    asyncio.run(consumed.reject(True))

    assert message.rejected_with is True
    assert channel.closed is True
    assert connection.closed is True


def test_infrastructure_declares_all_minimum_queues() -> None:
    queue = FakeQueue(message=None)
    channel = FakeChannel(queue)
    connection = FakeConnection(channel)
    infrastructure = RabbitMQInfrastructure(
        connection=FakeConnectionFactory(connection),
        queue_names=QueueNames(),
    )

    asyncio.run(infrastructure.ensure_minimum_queues())

    assert [name for name, durable in channel.declared] == list(QueueNames().all())
    assert all(durable is True for _, durable in channel.declared)
    assert channel.closed is True
    assert connection.closed is True
