"""Explicit worker routing decisions derived from run state and durable events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from structure.models.events.event import Event
from structure.schemas.events.event_payloads import EventType
from structure.schemas.runs.run import RunStatus

_TERMINAL_STATES = {
    RunStatus.FINISHED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}


class RunRouteAction(StrEnum):
    """Worker action selected for one event."""

    IGNORE = "ignore"
    CALL_EXECUTOR = "call_executor"
    EXECUTE_TOOL = "execute_tool"
    COMPLETE_RUN = "complete_run"
    FAIL_RUN = "fail_run"


def _event_type(event: Event) -> str:
    return str(event.event_type)


def _payload(event: Event) -> dict[str, Any]:
    return event.payload or {}


def _tool_id(event: Event) -> str | None:
    payload = _payload(event)
    value = payload.get("tool_id")
    return str(value) if value else None


@dataclass(frozen=True, slots=True)
class RunExecutionSnapshot:
    """Read model used by the worker router.

    The snapshot is intentionally derived from durable events plus the current
    run status. It avoids routing off a compact event-code string, making the
    execution decision inspectable and easier to extend.
    """

    run_status: str | None
    has_terminal_event: bool
    latest_user_message_id: str | None
    unresolved_tool_call_ids: tuple[str, ...]
    pending_user_input_tool_id: str | None
    should_call_executor: bool
    should_execute_tool: bool

    @classmethod
    def from_events(
        cls,
        events: list[Event],
        *,
        run_status: str | None = None,
    ) -> RunExecutionSnapshot:
        latest_user_message_id: str | None = None
        tool_calls: dict[str, Event] = {}
        resolved_tool_ids: set[str] = set()
        pending_user_input_tool_id: str | None = None
        has_terminal_event = run_status in _TERMINAL_STATES

        for event in events:
            event_type = _event_type(event)
            if event_type == str(EventType.USER_MESSAGE):
                latest_user_message_id = str(event.id)
            elif event_type == str(EventType.TOOL_CALL):
                if tool_id := _tool_id(event):
                    tool_calls[tool_id] = event
            elif event_type in {
                str(EventType.TOOL_RESULT),
                str(EventType.TOOL_ERROR),
                str(EventType.USER_FEEDBACK),
            }:
                if tool_id := _tool_id(event):
                    resolved_tool_ids.add(tool_id)
            elif event_type == str(EventType.AGENT_QUERY):
                if tool_id := _tool_id(event):
                    pending_user_input_tool_id = tool_id
            elif event_type in {
                str(EventType.RUN_COMPLETED),
                str(EventType.RUN_FAILED),
                str(EventType.RUN_CANCELLED),
            }:
                has_terminal_event = True
            elif event_type == str(EventType.RUN_STATE_CHANGE):
                new_state = _payload(event).get("new_state")
                if new_state in _TERMINAL_STATES:
                    has_terminal_event = True

        unresolved = tuple(
            tool_id for tool_id in tool_calls if tool_id not in resolved_tool_ids
        )
        should_call_executor = bool(latest_user_message_id) and not has_terminal_event
        should_execute_tool = bool(unresolved) and not has_terminal_event

        return cls(
            run_status=run_status,
            has_terminal_event=has_terminal_event,
            latest_user_message_id=latest_user_message_id,
            unresolved_tool_call_ids=unresolved,
            pending_user_input_tool_id=pending_user_input_tool_id,
            should_call_executor=should_call_executor,
            should_execute_tool=should_execute_tool,
        )


class RunEventRouter:
    """Map one event plus a snapshot to an explicit worker action."""

    def route(self, event: Event, snapshot: RunExecutionSnapshot) -> RunRouteAction:
        if snapshot.has_terminal_event:
            return RunRouteAction.IGNORE

        event_type = _event_type(event)
        if _payload(event).get("_source") == "trigger" and event_type in {
            str(EventType.TOOL_CALL),
            str(EventType.TOOL_RESULT),
            str(EventType.TOOL_ERROR),
        }:
            return RunRouteAction.IGNORE

        if event_type == str(EventType.TOOL_CALL):
            return RunRouteAction.EXECUTE_TOOL

        if event_type in {
            str(EventType.USER_MESSAGE),
            str(EventType.USER_FEEDBACK),
            str(EventType.TOOL_RESULT),
            str(EventType.TOOL_ERROR),
            str(EventType.TO_EXECUTOR),
        }:
            return RunRouteAction.CALL_EXECUTOR

        if event_type == str(EventType.RUN_COMPLETED):
            return RunRouteAction.COMPLETE_RUN
        if event_type in {str(EventType.RUN_FAILED), str(EventType.RUN_CANCELLED)}:
            return RunRouteAction.FAIL_RUN

        return RunRouteAction.IGNORE
