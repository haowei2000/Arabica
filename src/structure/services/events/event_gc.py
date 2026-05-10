"""Plugin-style event GC strategies for active run/workspace memory.

The persistent ``event`` table remains the audit log. GC strategies only decide
which active events should be moved into archive Context rows and marked
``is_archived``. TTL is measured by event execution progress, never wall-clock
time: each newer event can reduce an older event's remaining TTL by a
strategy-specific decay cost.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

from structure.core.enums import EventType

DEFAULT_EVENT_GC_STRATEGY = "event_count_ttl"


@dataclass(frozen=True)
class EventTTL:
    """TTL policy for one event type.

    ``ttl_events`` is compared against accumulated decay from subsequent events.
    For example, ``ttl_events=3`` means the event expires once newer events have
    reduced its TTL by more than 3. ``pin`` keeps the event active unless callers
    explicitly pass ``include_pinned=True``.
    """

    ttl_events: int | None = None
    pin: bool = False


DEFAULT_POLICY: dict[EventType, EventTTL] = {
    # Durable conversation and audit milestones.
    EventType.USER_MESSAGE: EventTTL(pin=True),
    EventType.AGENT_MESSAGE: EventTTL(pin=True),
    EventType.RUN_CREATED: EventTTL(pin=True),
    EventType.RUN_COMPLETED: EventTTL(pin=True),
    EventType.RUN_FAILED: EventTTL(pin=True),
    EventType.RUN_CANCELLED: EventTTL(pin=True),
    EventType.ARTIFACT_CREATE: EventTTL(pin=True),
    EventType.ARTIFACT_UPDATE: EventTTL(pin=True),
    EventType.ARTIFACT_VERSION: EventTTL(pin=True),
    EventType.TASK_COMPLETE: EventTTL(pin=True),
    EventType.SYSTEM_ERROR: EventTTL(pin=True),
    # Short-lived reasoning and tool state.
    EventType.AGENT_TOKEN: EventTTL(ttl_events=0),
    EventType.AGENT_HEARTBEAT: EventTTL(ttl_events=0),
    EventType.AGENT_THINKING: EventTTL(ttl_events=2),
    EventType.AGENT_PLAN_STEP: EventTTL(ttl_events=4),
    EventType.AGENT_QUERY: EventTTL(ttl_events=2),
    EventType.TOOL_PENDING: EventTTL(ttl_events=1),
    EventType.TOOL_CLIENT_REQUEST: EventTTL(ttl_events=2),
    EventType.TOOL_CALL: EventTTL(ttl_events=3),
    EventType.TOOL_RESULT: EventTTL(ttl_events=3),
    EventType.TOOL_ERROR: EventTTL(ttl_events=6),
    EventType.USING_CONTEXT: EventTTL(ttl_events=1),
    EventType.CONTEXT_RATED: EventTTL(ttl_events=4),
    # Medium-lived lifecycle/task signal.
    EventType.RUN_STATE_CHANGE: EventTTL(ttl_events=8),
    EventType.TASK_CREATE: EventTTL(ttl_events=10),
    EventType.TASK_UPDATE: EventTTL(ttl_events=6),
    EventType.TASK_DELETE: EventTTL(ttl_events=6),
    EventType.TASK_ASSIGN: EventTTL(ttl_events=6),
    EventType.SYSTEM_NOTIFICATION: EventTTL(ttl_events=4),
}


DEFAULT_DECAY_RULES: dict[str, dict[str, int]] = {
    "*": {"*": 1},
    str(EventType.AGENT_TOKEN): {"*": 4},
    str(EventType.AGENT_HEARTBEAT): {"*": 4},
    str(EventType.AGENT_THINKING): {
        str(EventType.AGENT_MESSAGE): 3,
        str(EventType.TOOL_CALL): 2,
    },
    str(EventType.TOOL_CALL): {
        str(EventType.TOOL_RESULT): 3,
        str(EventType.TOOL_ERROR): 3,
    },
    str(EventType.TOOL_PENDING): {
        str(EventType.TOOL_RESULT): 3,
        str(EventType.TOOL_ERROR): 3,
    },
    str(EventType.USING_CONTEXT): {"*": 2},
}


class _EventLike:
    event_type: str | EventType
    sequence: int


@runtime_checkable
class EventGCStrategy(Protocol):
    """Protocol implemented by event GC strategy plugins."""

    name: ClassVar[str]

    def select_candidates(
        self,
        events: Sequence[_EventLike],
        *,
        scope: str,
        config: Mapping[str, Any] | None = None,
    ) -> list[_EventLike]:
        """Return active events that should be archived."""


class EventGCStrategyRegistry:
    """Small registry for Python-defined event GC strategy plugins."""

    _strategies: ClassVar[dict[str, EventGCStrategy]] = {}

    @classmethod
    def register(cls, strategy: EventGCStrategy | type[EventGCStrategy]):
        instance = strategy() if isinstance(strategy, type) else strategy
        name = getattr(instance, "name", None)
        if not name:
            raise ValueError("Event GC strategy must define a non-empty 'name'")
        cls._strategies[str(name)] = instance
        return strategy

    @classmethod
    def get(cls, name: str) -> EventGCStrategy:
        try:
            return cls._strategies[name]
        except KeyError as exc:
            available = ", ".join(sorted(cls._strategies)) or "(none)"
            raise ValueError(
                f"Unknown event GC strategy '{name}'. Available: {available}"
            ) from exc

    @classmethod
    def list_names(cls) -> list[str]:
        return sorted(cls._strategies)


def register_event_gc_strategy(strategy: type[EventGCStrategy]):
    """Decorator for registering an event GC strategy class."""
    EventGCStrategyRegistry.register(strategy)
    return strategy


def _event_type_value(event_type: str | EventType) -> str:
    return event_type.value if isinstance(event_type, EventType) else str(event_type)


def _ttl_from_raw(raw: Any) -> EventTTL:
    if isinstance(raw, EventTTL):
        return raw
    if raw is None:
        return EventTTL(ttl_events=None)
    if isinstance(raw, bool):
        return EventTTL(pin=raw)
    if isinstance(raw, int):
        return EventTTL(ttl_events=raw)
    if isinstance(raw, Mapping):
        return EventTTL(
            ttl_events=raw.get("ttl_events"),
            pin=bool(raw.get("pin", False)),
        )
    raise TypeError(f"Unsupported EventTTL override: {raw!r}")


def _merge_ttl_policy(config: Mapping[str, Any]) -> dict[str, EventTTL]:
    policy = {_event_type_value(key): value for key, value in DEFAULT_POLICY.items()}
    for event_type, raw in (config.get("ttl_overrides") or {}).items():
        policy[str(event_type)] = _ttl_from_raw(raw)
    return policy


def _merge_decay_rules(config: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    merged = {old: dict(newer) for old, newer in DEFAULT_DECAY_RULES.items()}
    for old_event_type, rules in (config.get("decay_rules") or {}).items():
        old_key = str(old_event_type)
        merged.setdefault(old_key, {})
        for newer_event_type, cost in dict(rules).items():
            merged[old_key][str(newer_event_type)] = max(0, int(cost))
    return merged


@register_event_gc_strategy
class EventCountTTLStrategy:
    """Default event-count TTL strategy with per-event-type decay rules."""

    name: ClassVar[str] = DEFAULT_EVENT_GC_STRATEGY

    def select_candidates(
        self,
        events: Sequence[_EventLike],
        *,
        scope: str,
        config: Mapping[str, Any] | None = None,
    ) -> list[_EventLike]:
        cfg = dict(config or {})
        allowed_types = set(cfg.get("event_types") or [])
        include_pinned = bool(cfg.get("include_pinned", False))
        default_ttl_events = int(cfg.get("default_ttl_events", 6))
        pinned_ttl_events = int(cfg.get("pinned_ttl_events", default_ttl_events))
        keep_last_floor = int(
            cfg.get(
                "keep_last_floor",
                500 if scope == "workspace" else 100,
            )
        )

        ttl_policy = _merge_ttl_policy(cfg)
        decay_rules = _merge_decay_rules(cfg)
        protected = self._protected_recent_events(events, keep_last_floor)

        candidates: list[_EventLike] = []
        newer_type_counts: Counter[str] = Counter()
        for event in reversed(events):
            event_type = _event_type_value(event.event_type)
            if id(event) not in protected and (
                not allowed_types or event_type in allowed_types
            ):
                ttl = ttl_policy.get(
                    event_type,
                    EventTTL(ttl_events=default_ttl_events),
                )
                if not ttl.pin or include_pinned:
                    ttl_events = ttl.ttl_events
                    if ttl_events is None:
                        ttl_events = (
                            pinned_ttl_events if ttl.pin else default_ttl_events
                        )

                    ttl_decay = self.ttl_decay_from_counts(
                        event_type,
                        newer_type_counts,
                        decay_rules,
                    )
                    if ttl_decay > ttl_events:
                        candidates.append(event)

            newer_type_counts[event_type] += 1

        candidates.reverse()
        return candidates

    @staticmethod
    def ttl_decay_from_counts(
        event_type: str,
        newer_type_counts: Mapping[str, int],
        decay_rules: Mapping[str, Mapping[str, int]] | None = None,
    ) -> int:
        rules = decay_rules or DEFAULT_DECAY_RULES
        return sum(
            count
            * EventCountTTLStrategy.decay_cost(
                event_type,
                newer_event_type,
                rules,
            )
            for newer_event_type, count in newer_type_counts.items()
        )

    @staticmethod
    def ttl_decay_for(
        event: _EventLike,
        newer_events: Sequence[_EventLike],
        decay_rules: Mapping[str, Mapping[str, int]] | None = None,
    ) -> int:
        rules = decay_rules or DEFAULT_DECAY_RULES
        event_type = _event_type_value(event.event_type)
        return sum(
            EventCountTTLStrategy.decay_cost(
                event_type,
                _event_type_value(newer.event_type),
                rules,
            )
            for newer in newer_events
        )

    @staticmethod
    def decay_cost(
        event_type: str,
        newer_event_type: str,
        decay_rules: Mapping[str, Mapping[str, int]],
    ) -> int:
        for old_key, new_key in (
            (event_type, newer_event_type),
            (event_type, "*"),
            ("*", newer_event_type),
            ("*", "*"),
        ):
            cost = decay_rules.get(old_key, {}).get(new_key)
            if cost is not None:
                return max(0, int(cost))
        return 1

    @staticmethod
    def _protected_recent_events(
        events: Sequence[_EventLike],
        keep_last_floor: int,
    ) -> set[int]:
        if keep_last_floor <= 0:
            return set()
        return {id(event) for event in events[-keep_last_floor:]}


@dataclass
class EventGarbageCollector:
    """Compatibility wrapper around a registered event GC strategy."""

    strategy: EventGCStrategy = field(
        default_factory=lambda: EventGCStrategyRegistry.get(DEFAULT_EVENT_GC_STRATEGY)
    )
    config: Mapping[str, Any] = field(default_factory=dict)
    scope: str = "run"

    def collect(self, events: Iterable[_EventLike]) -> list[_EventLike]:
        event_list = list(events)
        candidates = {
            id(event)
            for event in self.strategy.select_candidates(
                event_list,
                scope=self.scope,
                config=self.config,
            )
        }
        return [event for event in event_list if id(event) not in candidates]

    def partition(
        self,
        events: Sequence[_EventLike],
    ) -> tuple[list[_EventLike], list[_EventLike]]:
        expired = self.strategy.select_candidates(
            events,
            scope=self.scope,
            config=self.config,
        )
        expired_ids = {id(event) for event in expired}
        live = [event for event in events if id(event) not in expired_ids]
        return live, expired


__all__ = [
    "DEFAULT_DECAY_RULES",
    "DEFAULT_EVENT_GC_STRATEGY",
    "DEFAULT_POLICY",
    "EventCountTTLStrategy",
    "EventGCStrategy",
    "EventGCStrategyRegistry",
    "EventGarbageCollector",
    "EventTTL",
    "register_event_gc_strategy",
]
