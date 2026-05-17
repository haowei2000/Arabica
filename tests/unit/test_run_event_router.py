from __future__ import annotations

from uuid import uuid4

from structure.models.events.event import Event
from structure.schemas.events.event_payloads import EventType
from structure.schemas.runs.run import RunStatus
from structure.services.events.run_event_router import (
    RunEventRouter,
    RunExecutionSnapshot,
    RunRouteAction,
)


def _event(event_type: EventType, payload: dict | None = None) -> Event:
    return Event(
        id=uuid4(),
        event_type=str(event_type),
        workspace_id=uuid4(),
        run_id=uuid4(),
        payload=payload or {},
        sequence=1,
    )


def test_user_message_routes_to_executor():
    event = _event(EventType.USER_MESSAGE, {"message": "hi"})
    snapshot = RunExecutionSnapshot.from_events([event], run_status=RunStatus.PENDING)

    assert RunEventRouter().route(event, snapshot) == RunRouteAction.CALL_EXECUTOR
    assert snapshot.latest_user_message_id == str(event.id)


def test_tool_call_routes_to_tool_execution():
    event = _event(
        EventType.TOOL_CALL,
        {"tool_name": "read_context", "tool_id": "tool-1"},
    )
    snapshot = RunExecutionSnapshot.from_events([event], run_status=RunStatus.RUNNING)

    assert RunEventRouter().route(event, snapshot) == RunRouteAction.EXECUTE_TOOL
    assert snapshot.unresolved_tool_call_ids == ("tool-1",)


def test_tool_result_routes_back_to_executor():
    user = _event(EventType.USER_MESSAGE, {"message": "hi"})
    call = _event(EventType.TOOL_CALL, {"tool_id": "tool-1"})
    result = _event(EventType.TOOL_RESULT, {"tool_id": "tool-1", "result": {}})
    snapshot = RunExecutionSnapshot.from_events(
        [user, call, result],
        run_status=RunStatus.RUNNING,
    )

    assert RunEventRouter().route(result, snapshot) == RunRouteAction.CALL_EXECUTOR
    assert snapshot.unresolved_tool_call_ids == ()


def test_terminal_run_ignores_late_events():
    event = _event(EventType.USER_MESSAGE, {"message": "late"})
    snapshot = RunExecutionSnapshot.from_events(
        [event],
        run_status=RunStatus.FINISHED,
    )

    assert RunEventRouter().route(event, snapshot) == RunRouteAction.IGNORE


def test_trigger_sourced_tool_result_is_not_forwarded_again():
    event = _event(
        EventType.TOOL_RESULT,
        {"tool_id": "trigger-tool", "_source": "trigger"},
    )
    snapshot = RunExecutionSnapshot.from_events([event], run_status=RunStatus.RUNNING)

    assert RunEventRouter().route(event, snapshot) == RunRouteAction.IGNORE
