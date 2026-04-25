"""Event-level garbage collection for the agent's working context.

The DB event log is append-only and is never truncated here --- this module
only decides which past events an agent still needs to *see* during the
current step.  Each ``EventType`` gets a TTL expressed either in agent
steps or in seconds; when both are set, whichever is smaller applies.

A step counter comes from the run's state machine (``run.step_count`` or an
in-memory tick); the collector is a pure function so it can be called from
any place that assembles the context prompt.

Design goals:
* Preserve the full audit trail on disk.
* Keep the rule table small enough that a human can reason about it.
* Let executors override the policy per-run without editing global state.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from structure.core.enums import EventType


@dataclass(frozen=True)
class EventTTL:
    """TTL for a single event type.

    ``steps`` is the number of subsequent agent steps after which the event
    becomes invisible; ``seconds`` is a wall-clock fallback.  A value of
    ``None`` means "never expire on this axis".  ``pin`` forces the event to
    stay visible for the whole run regardless of step/seconds (used for the
    initial user message, final artifact creation, etc.).
    """

    steps: int | None = None
    seconds: int | None = None
    pin: bool = False


# Default policy.  Noisy, transient signal is pruned quickly; durable turns
# and artifacts stay until end-of-run.  Tune per-executor if needed.
DEFAULT_POLICY: dict[EventType, EventTTL] = {
    # --- Pinned for the life of the run ---
    EventType.USER_MESSAGE: EventTTL(pin=True),
    EventType.AGENT_MESSAGE: EventTTL(pin=True),
    EventType.RUN_CREATED: EventTTL(pin=True),
    EventType.RUN_COMPLETED: EventTTL(pin=True),
    EventType.RUN_FAILED: EventTTL(pin=True),
    EventType.ARTIFACT_CREATE: EventTTL(pin=True),
    EventType.ARTIFACT_UPDATE: EventTTL(pin=True),
    EventType.ARTIFACT_VERSION: EventTTL(pin=True),
    EventType.TASK_COMPLETE: EventTTL(pin=True),
    # --- Short-lived reasoning / tool signal ---
    EventType.AGENT_THINKING: EventTTL(steps=2),
    EventType.AGENT_PLAN_STEP: EventTTL(steps=4),
    EventType.AGENT_QUERY: EventTTL(steps=2),
    EventType.TOOL_CALL: EventTTL(steps=3),
    EventType.TOOL_RESULT: EventTTL(steps=3),
    EventType.TOOL_ERROR: EventTTL(steps=6),
    EventType.TOOL_PENDING: EventTTL(steps=1),
    EventType.TOOL_CLIENT_REQUEST: EventTTL(steps=2),
    EventType.USING_CONTEXT: EventTTL(steps=1),
    EventType.CONTEXT_RATED: EventTTL(steps=4),
    # --- Noise: drop immediately from context (audit log keeps them) ---
    EventType.AGENT_TOKEN: EventTTL(steps=0),
    EventType.AGENT_HEARTBEAT: EventTTL(steps=0),
    # --- Medium-lived lifecycle / workspace changes ---
    EventType.RUN_STATE_CHANGE: EventTTL(steps=8),
    EventType.RUN_CANCELLED: EventTTL(pin=True),
    EventType.TASK_CREATE: EventTTL(steps=10),
    EventType.TASK_UPDATE: EventTTL(steps=6),
    EventType.TASK_DELETE: EventTTL(steps=6),
    EventType.TASK_ASSIGN: EventTTL(steps=6),
    EventType.SYSTEM_ERROR: EventTTL(pin=True),
    EventType.SYSTEM_NOTIFICATION: EventTTL(steps=4),
}


@dataclass
class EventGarbageCollector:
    """Filter a sequence of events down to those still live at a given step.

    The collector is stateless; callers supply the current step and
    (optionally) the wall-clock ``now``.  Per-type overrides merge on top of
    :data:`DEFAULT_POLICY`.
    """

    overrides: dict[EventType, EventTTL] = field(default_factory=dict)
    default: EventTTL = field(default=EventTTL(steps=6))

    def ttl_for(self, event_type: EventType) -> EventTTL:
        if event_type in self.overrides:
            return self.overrides[event_type]
        return DEFAULT_POLICY.get(event_type, self.default)

    def is_live(
        self,
        event: _EventLike,
        *,
        current_step: int,
        now: datetime | None = None,
    ) -> bool:
        """Return True iff the event should still be visible to the agent."""
        ttl = self.ttl_for(EventType(event.event_type))
        if ttl.pin:
            return True

        if ttl.steps is not None:
            event_step = getattr(event, "step", None)
            if event_step is None:
                # Fall back to sequence-number as a step proxy.
                event_step = getattr(event, "sequence", current_step)
            if current_step - event_step > ttl.steps:
                return False

        if ttl.seconds is not None and event.created_at is not None:
            wall_now = now or datetime.now(UTC)
            if wall_now - event.created_at > timedelta(seconds=ttl.seconds):
                return False

        return True

    def collect(
        self,
        events: Iterable[_EventLike],
        *,
        current_step: int,
        now: datetime | None = None,
    ) -> list[_EventLike]:
        """Return only the events still live at ``current_step``."""
        return [
            e for e in events if self.is_live(e, current_step=current_step, now=now)
        ]

    def partition(
        self,
        events: Sequence[_EventLike],
        *,
        current_step: int,
        now: datetime | None = None,
    ) -> tuple[list[_EventLike], list[_EventLike]]:
        """Split ``events`` into ``(live, expired)``.

        Useful when callers want to summarise the expired batch into a
        single "... n older steps omitted ..." marker in the prompt
        rather than drop it silently.
        """
        live: list[_EventLike] = []
        expired: list[_EventLike] = []
        for e in events:
            (
                live if self.is_live(e, current_step=current_step, now=now) else expired
            ).append(e)
        return live, expired


class _EventLike:
    """Structural type for anything the collector can grade.

    Duck-typed on purpose so we do not import the ORM class in a pure-logic
    module; callers pass either :class:`structure.models.events.event.Event`
    or a lightweight adapter.
    """

    event_type: str | EventType
    sequence: int
    created_at: datetime | None
    step: int | None  # optional override if caller tracks explicit steps


__all__ = [
    "DEFAULT_POLICY",
    "EventGarbageCollector",
    "EventTTL",
]
