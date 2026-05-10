from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest

from structure.core.enums import EventType
from structure.models.events.event import Event
from structure.services.events.event_archive import (
    EventArchiveResult,
    EventArchiveService,
    select_archive_candidates,
)
from structure.services.events.event_gc import (
    DEFAULT_EVENT_GC_STRATEGY,
    EventCountTTLStrategy,
    EventGCStrategyRegistry,
    register_event_gc_strategy,
)


def _event(event_type: EventType, sequence: int) -> Event:
    return Event(
        id=uuid4(),
        event_type=str(event_type),
        workspace_id=uuid4(),
        run_id=uuid4(),
        payload={"message": f"event {sequence}"},
        sequence=sequence,
        created_at=datetime.now(UTC),
        is_archived=False,
    )


def test_event_count_ttl_uses_newer_event_decay_not_wall_clock_time():
    events = [
        _event(EventType.TOOL_RESULT, 1),
        _event(EventType.AGENT_MESSAGE, 2),
        _event(EventType.AGENT_MESSAGE, 3),
    ]

    candidates = select_archive_candidates(
        events,
        scope="run",
        keep_last=0,
        include_pinned=False,
        strategy_config={
            "ttl_overrides": {str(EventType.TOOL_RESULT): 1},
        },
    )

    assert [event.sequence for event in candidates] == [1]


def test_event_count_ttl_applies_specialized_decay_rules():
    events = [
        _event(EventType.TOOL_CALL, 1),
        _event(EventType.TOOL_RESULT, 2),
    ]

    candidates = select_archive_candidates(
        events,
        scope="run",
        keep_last=0,
        include_pinned=False,
        strategy_config={
            "ttl_overrides": {str(EventType.TOOL_CALL): 2},
            "decay_rules": {
                str(EventType.TOOL_CALL): {
                    str(EventType.TOOL_RESULT): 3,
                }
            },
        },
    )

    assert [event.sequence for event in candidates] == [1]


def test_event_count_ttl_keeps_pinned_by_default_and_can_include_them():
    events = [
        _event(EventType.USER_MESSAGE, 1),
        _event(EventType.AGENT_MESSAGE, 2),
        _event(EventType.AGENT_TOKEN, 3),
        _event(EventType.TOOL_RESULT, 4),
    ]

    default_candidates = select_archive_candidates(
        events,
        scope="run",
        keep_last=0,
        include_pinned=False,
        strategy_config={"default_ttl_events": 0},
    )
    include_pinned_candidates = select_archive_candidates(
        events,
        scope="run",
        keep_last=0,
        include_pinned=True,
        strategy_config={"default_ttl_events": 0, "pinned_ttl_events": 0},
    )

    assert [event.sequence for event in default_candidates] == [3]
    assert [event.sequence for event in include_pinned_candidates] == [1, 2, 3]


def test_event_count_ttl_filters_event_types_and_respects_keep_last_floor():
    events = [
        _event(EventType.AGENT_TOKEN, 1),
        _event(EventType.TOOL_RESULT, 2),
        _event(EventType.AGENT_TOKEN, 3),
    ]

    candidates = select_archive_candidates(
        events,
        scope="run",
        keep_last=1,
        include_pinned=False,
        event_types=[str(EventType.AGENT_TOKEN)],
    )

    assert [event.sequence for event in candidates] == [1]


def test_event_count_ttl_strategy_exposes_decay_calculation():
    events = [
        _event(EventType.TOOL_CALL, 1),
        _event(EventType.TOOL_RESULT, 2),
        _event(EventType.AGENT_MESSAGE, 3),
    ]

    decay = EventCountTTLStrategy.ttl_decay_for(events[0], events[1:])

    assert decay == 4


def test_event_gc_strategy_registry_accepts_plugins():
    @register_event_gc_strategy
    class FakeEventGCStrategy:
        name: ClassVar[str] = "fake_event_gc_strategy"

        def select_candidates(self, events, *, scope, config=None):
            return list(events[:1])

    events = [_event(EventType.AGENT_TOKEN, 1), _event(EventType.AGENT_TOKEN, 2)]

    candidates = EventArchiveService.select_candidates(
        events,
        scope="run",
        strategy="fake_event_gc_strategy",
        strategy_config={},
        keep_last=None,
        include_pinned=False,
        event_types=None,
    )

    assert [event.sequence for event in candidates] == [1]


def test_event_gc_strategy_registry_reports_unknown_strategy():
    with pytest.raises(ValueError, match="Unknown event GC strategy"):
        EventGCStrategyRegistry.get("missing_event_gc_strategy")


def test_event_archive_result_serializes_as_api_shape():
    result = EventArchiveResult(
        scope="run",
        scope_id="run-1",
        dry_run=True,
        archived_count=2,
        skipped_count=3,
        active_count_before=5,
        archive_context_id=None,
        archive_path=None,
        reason="test",
        strategy=DEFAULT_EVENT_GC_STRATEGY,
    )

    assert result.as_dict() == {
        "scope": "run",
        "scope_id": "run-1",
        "dry_run": True,
        "archived_count": 2,
        "skipped_count": 3,
        "active_count_before": 5,
        "archive_context_id": None,
        "archive_path": None,
        "reason": "test",
        "strategy": DEFAULT_EVENT_GC_STRATEGY,
    }
