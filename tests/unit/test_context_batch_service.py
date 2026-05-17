from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from structure.core.enums import ContextBatchLoadState, EventType
from structure.models.events.event import Event
from structure.models.events.event_batch import EventBatch
from structure.services.events.context_batch_service import ContextBatchService


def _event(event_type: EventType, sequence: int, payload=None, run_id=None) -> Event:
    return Event(
        id=uuid4(),
        event_type=str(event_type),
        workspace_id=uuid4(),
        run_id=run_id or uuid4(),
        payload=payload or {},
        sequence=sequence,
        created_at=datetime.now(UTC),
        input_tokens=0,
        output_tokens=0,
        is_archived=False,
    )


@pytest.mark.asyncio
async def test_context_key_groups_user_turn_events():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=2)
    run_id = uuid4()
    service = ContextBatchService(db)

    key = await service.context_key_for_event(
        _event(EventType.AGENT_MESSAGE, 8, {"content": "answer"}, run_id=run_id)
    )

    assert key.context_key == f"run:{run_id}:turn:2"
    assert key.context_kind == "turn"


@pytest.mark.asyncio
async def test_context_key_keeps_tool_call_and_result_together():
    service = ContextBatchService(AsyncMock())
    tool_id = "call-123"

    call_key = await service.context_key_for_event(
        _event(
            EventType.TOOL_CALL,
            3,
            {"tool_name": "custom_tool", "tool_id": tool_id},
        )
    )
    result_key = await service.context_key_for_event(
        _event(
            EventType.TOOL_RESULT,
            4,
            {"tool_name": "custom_tool", "tool_id": tool_id},
            run_id=call_key.context_key.split(":")[1],
        )
    )

    assert call_key.context_kind == "tool"
    assert result_key.context_kind == "tool"
    assert call_key.context_key.endswith(f":tool:{tool_id}")
    assert result_key.context_key.endswith(f":tool:{tool_id}")


@pytest.mark.asyncio
async def test_context_key_groups_context_tool_path_events():
    service = ContextBatchService(AsyncMock())
    workspace_id = uuid4()

    call = Event(
        id=uuid4(),
        event_type=str(EventType.TOOL_CALL),
        workspace_id=workspace_id,
        run_id=uuid4(),
        payload={
            "tool_name": "read_context",
            "tool_id": "call-read",
            "arguments": {"path": "/tools/search/schema"},
        },
        sequence=1,
        created_at=datetime.now(UTC),
        is_archived=False,
    )
    result = Event(
        id=uuid4(),
        event_type=str(EventType.TOOL_RESULT),
        workspace_id=workspace_id,
        run_id=call.run_id,
        payload={
            "tool_name": "read_context",
            "tool_id": "call-read",
            "result": {"data": {"path": "/tools/search/schema"}},
        },
        sequence=2,
        created_at=datetime.now(UTC),
        is_archived=False,
    )

    call_key = await service.context_key_for_event(call)
    result_key = await service.context_key_for_event(result)

    assert call_key.context_key == result_key.context_key
    assert call_key.context_key == f"context:{workspace_id}:tools/search/schema"
    assert call_key.context_kind == "context"


@pytest.mark.asyncio
async def test_context_key_fallback_bucket_is_deterministic():
    service = ContextBatchService(AsyncMock())
    event = _event(EventType.SYSTEM_NOTIFICATION, 123, {"message": "notice"})

    key = await service.context_key_for_event(event)

    assert key.context_key.endswith(":misc:2")
    assert key.context_kind == "misc"


def test_load_policy_sets_recent_turns_and_transients():
    workspace_id = uuid4()
    current_run_id = uuid4()
    old_turn = EventBatch(
        id=uuid4(),
        workspace_id=workspace_id,
        run_id=uuid4(),
        context_key="run:old:turn:1",
        context_kind="turn",
        sequence_start=1,
        sequence_end=2,
    )
    recent_turn = EventBatch(
        id=uuid4(),
        workspace_id=workspace_id,
        run_id=current_run_id,
        context_key="run:current:turn:1",
        context_kind="turn",
        sequence_start=10,
        sequence_end=11,
    )
    transient = EventBatch(
        id=uuid4(),
        workspace_id=workspace_id,
        run_id=current_run_id,
        context_key="run:current:transient:1",
        context_kind="transient",
        sequence_start=12,
        sequence_end=15,
    )
    tool = EventBatch(
        id=uuid4(),
        workspace_id=workspace_id,
        run_id=current_run_id,
        context_key="run:current:tool:call-1",
        context_kind="tool",
        sequence_start=16,
        sequence_end=17,
    )

    assert (
        ContextBatchService._desired_load_state(old_turn, {recent_turn.id}, current_run_id)
        == ContextBatchLoadState.LOAD_KEY
    )
    assert (
        ContextBatchService._desired_load_state(
            recent_turn,
            {recent_turn.id},
            current_run_id,
        )
        == ContextBatchLoadState.LOAD_ALL
    )
    assert (
        ContextBatchService._desired_load_state(
            transient,
            {recent_turn.id},
            current_run_id,
        )
        == ContextBatchLoadState.NO_LOAD
    )
    assert (
        ContextBatchService._desired_load_state(tool, {recent_turn.id}, current_run_id)
        == ContextBatchLoadState.LOAD_ALL
    )
