"""Unit tests for Redis event stream naming."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from structure.core.enums.events import EventType
from structure.services.events.event_commands import executor_command_stream_name
from structure.services.events.event_publisher import EventPublisher


class _FakePipeline:
    def __init__(self) -> None:
        self.xadd_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self.xadd_calls.append(
            {
                "stream": stream,
                "fields": fields,
                "maxlen": maxlen,
                "approximate": approximate,
            }
        )

    async def execute(self):
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.pipeline_obj = _FakePipeline()
        self.xrange_calls = []

    def pipeline(self, transaction=False):
        self.pipeline_transaction = transaction
        return self.pipeline_obj

    async def xrange(self, stream, count=None):
        self.xrange_calls.append({"stream": stream, "count": count})
        return []


@pytest.mark.asyncio
async def test_event_publisher_uses_workspace_label_for_workspace_stream():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=MagicMock(return_value=0))
    db.add = MagicMock()
    db.flush = AsyncMock()

    redis = _FakeRedis()
    publisher = EventPublisher(db, redis)

    await publisher.publish(
        event_type=EventType.USER_MESSAGE,
        workspace_id="workspace-1",
        run_id="run-1",
        payload={"message": "hello"},
    )

    streams = [call["stream"] for call in redis.pipeline_obj.xadd_calls]
    assert streams == ["run:run-1:events", "workspace:workspace-1:events"]
    assert all("re.compile" not in stream for stream in streams)


def test_worker_uses_executor_command_stream_for_commands():
    assert (
        executor_command_stream_name("workspace-1") == "executor:workspace-1:commands"
    )
