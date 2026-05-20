from collections import Counter
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
    AGGRESSIVE_ACTIVE_MEMORY_GC_STRATEGY,
    CONTEXT_BATCH_GC_STRATEGY,
    DEFAULT_EVENT_GC_STRATEGY,
    AggressiveActiveMemoryGCStrategy,
    BatchAwareEventGCStrategy,
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


def test_event_count_ttl_suffix_count_decay_matches_event_scan():
    events = [
        _event(EventType.TOOL_CALL, 1),
        _event(EventType.TOOL_RESULT, 2),
        _event(EventType.AGENT_MESSAGE, 3),
        _event(EventType.TOOL_ERROR, 4),
    ]
    newer_type_counts = Counter(str(event.event_type) for event in events[1:])

    scan_decay = EventCountTTLStrategy.ttl_decay_for(events[0], events[1:])
    suffix_decay = EventCountTTLStrategy.ttl_decay_from_counts(
        str(events[0].event_type),
        newer_type_counts,
    )

    assert suffix_decay == scan_decay


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


def test_aggressive_active_memory_gc_expires_tool_results_earlier():
    events = [
        _event(EventType.TOOL_RESULT, 1),
        _event(EventType.USER_MESSAGE, 2),
    ]

    default_candidates = EventArchiveService.select_candidates(
        events,
        scope="run",
        strategy=DEFAULT_EVENT_GC_STRATEGY,
        strategy_config={},
        keep_last=0,
        include_pinned=False,
        event_types=None,
    )
    aggressive_candidates = EventArchiveService.select_candidates(
        events,
        scope="run",
        strategy=AGGRESSIVE_ACTIVE_MEMORY_GC_STRATEGY,
        strategy_config={},
        keep_last=0,
        include_pinned=False,
        event_types=None,
    )

    assert isinstance(
        EventGCStrategyRegistry.get(AGGRESSIVE_ACTIVE_MEMORY_GC_STRATEGY),
        AggressiveActiveMemoryGCStrategy,
    )
    assert default_candidates == []
    assert [event.sequence for event in aggressive_candidates] == [1]


def test_batch_aware_event_gc_archives_whole_load_key_batches():
    batch_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, 1),
        _event(EventType.AGENT_MESSAGE, 2),
        _event(EventType.AGENT_TOKEN, 3),
    ]
    for event in events[:2]:
        event.batch_id = batch_id
        event.batch_context_key = "run:batch:turn:1"
        event.batch_load_state = "load_key"
    events[2].batch_id = uuid4()
    events[2].batch_context_key = "run:batch:transient:0"
    events[2].batch_load_state = "load_all"

    candidates = EventArchiveService.select_candidates(
        events,
        scope="run",
        strategy=CONTEXT_BATCH_GC_STRATEGY,
        strategy_config={"keep_last_floor": 0},
        keep_last=None,
        include_pinned=False,
        event_types=None,
    )

    assert isinstance(
        EventGCStrategyRegistry.get(CONTEXT_BATCH_GC_STRATEGY),
        BatchAwareEventGCStrategy,
    )
    assert [event.sequence for event in candidates] == [1, 2]


def test_event_archive_splits_context_chunks_by_event_count():
    events = [_event(EventType.TOOL_RESULT, sequence) for sequence in range(1, 6)]

    chunks = EventArchiveService._split_event_chunks(
        events,
        max_events_per_archive_context=2,
        max_chars_per_archive_context=100_000,
    )

    assert [[event.sequence for event in chunk] for chunk in chunks] == [
        [1, 2],
        [3, 4],
        [5],
    ]


async def test_event_archive_dry_run_uses_lightweight_candidates_only():
    class DryRunArchiveService(EventArchiveService):
        loaded_full_events = False

        async def _load_run_event_candidates(self, run_id):
            return [
                _event(EventType.AGENT_TOKEN, 1),
                _event(EventType.TOOL_RESULT, 2),
            ]

        async def _load_full_events(self, candidates):
            self.loaded_full_events = True
            raise AssertionError("dry-run should not load full Event payloads")

    service = DryRunArchiveService(db=None)

    result = await service.archive_run_memory(
        uuid4(),
        user_id=uuid4(),
        keep_last=0,
        include_pinned=False,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.archived_count == 1
    assert service.loaded_full_events is False


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
        "archive_context_ids": [],
        "archive_paths": [],
        "archive_chunks": 0,
    }
