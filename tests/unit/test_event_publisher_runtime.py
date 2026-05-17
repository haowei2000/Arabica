from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from structure.schemas.events.event_payloads import EventType
from structure.services.events.event_publisher import EventPublisher


class _FakeScalarResult:
    def scalar(self):
        return 0


class _FakePipeline:
    def __init__(self) -> None:
        self.xadd = MagicMock()
        self.execute = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRedis:
    def __init__(self) -> None:
        self.pipeline_obj = _FakePipeline()
        self.xadd = AsyncMock()

    def pipeline(self, transaction: bool = False):
        return self.pipeline_obj


@pytest.mark.asyncio
async def test_publish_durable_writes_db_then_queues_executor_command():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_FakeScalarResult())
    redis = _FakeRedis()
    publisher = EventPublisher(db, redis)  # type: ignore[arg-type]

    event = await publisher.publish_durable(
        event_type=EventType.USER_MESSAGE,
        workspace_id=uuid4(),
        run_id=uuid4(),
        payload={"message": "hello"},
        executor_code="SimpleAgent",
        auto_commit=True,
    )

    db.add.assert_called_once_with(event)
    assert db.flush.await_count >= 1
    db.commit.assert_awaited_once()
    assert redis.pipeline_obj.xadd.call_count == 2
    redis.xadd.assert_awaited_once()
    command_fields = redis.xadd.await_args.args[1]
    assert command_fields["event_id"] == str(event.id)
    assert command_fields["executor_code"] == "SimpleAgent"


@pytest.mark.asyncio
async def test_publish_realtime_does_not_write_db_or_command_stream():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_FakeScalarResult())
    redis = _FakeRedis()
    publisher = EventPublisher(db, redis)  # type: ignore[arg-type]

    await publisher.publish(
        event_type=EventType.AGENT_TOKEN,
        workspace_id=uuid4(),
        run_id=uuid4(),
        payload={"token": "a"},
    )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert redis.pipeline_obj.xadd.call_count == 2
    redis.xadd.assert_not_awaited()
