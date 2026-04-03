# structure/services/events/event_publisher.py
"""Event Publisher service for publishing events to PostgreSQL and Redis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis_async
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.factory import get_settings
from structure.models.events.event import Event
from structure.models.runs.run import Run
from structure.schemas.events.event_payloads import EventType
from structure.services.events.event_codec import RE_CODE_WORKSPACE

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_STREAM_EVENTS_SUFFIX = _redis_cfg.stream_events_suffix
REDIS_WORKSPACE_LABEL = _redis_cfg.workspace_label

# Event types that workers must consume from the executor stream.
# Defined at module level to avoid recreating the set on every publish() call.
_EXECUTOR_STREAM_TYPES: frozenset[str] = frozenset(
    {
        # External input events that the worker must receive to validate + re-publish
        # as TO_EXECUTOR.
        EventType.USER_MESSAGE,
        EventType.USER_FEEDBACK,
        EventType.TOOL_RESULT,
        EventType.TOOL_ERROR,
        # TOOL_CALL is dispatched directly to handle_tool_call (no TO_EXECUTOR hop).
        EventType.TOOL_CALL,
        # Infrastructure events handled before executor routing.
        EventType.RUN_CANCELLED,
        # Internal worker-routing event: carries validated events to the executor.
        EventType.TO_EXECUTOR,
    }
)


def _to_jsonable(value: Any) -> Any:
    """Recursively convert Python objects into JSON-serializable forms."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class EventPublisher:
    """Event Publisher service.

    Publishes events to:
    1. PostgreSQL (persistent storage, source of truth)
    2. Redis Streams (real-time broadcasting for SSE/WebSocket)

    Events are auto-sequenced per run and per workspace.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis_client: redis_async.Redis | None = None,
        run_buffer_provider: Callable[[str], list[Event] | None] | None = None,
        run_seq_cursor: dict[str, int] | None = None,
    ):
        """Initialize EventPublisher.

        Args:
            db: SQLAlchemy async session
            redis_client: Redis async client (optional, for real-time broadcasting)
            run_buffer_provider: Callable that returns the in-memory event buffer
                for a run_id, or None if that run is not being buffered.  When a
                buffer is returned, events are appended there instead of being
                written to PostgreSQL — DB sync happens at run completion.
            run_seq_cursor: Shared Worker-level dict mapping run_id → current max
                sequence.  Used to keep sequence numbers consistent across the
                multiple per-dispatch EventPublisher instances that service the
                same run without re-querying the DB every time.
        """
        self.db = db
        self.redis = redis_client
        self._run_buffer_provider = run_buffer_provider
        # Shared mutable dict owned by Worker; None for callers outside the worker.
        self._run_seq_cursor = run_seq_cursor
        # In-memory sequence counters keyed by run_id (or "ws:<workspace_id>").
        # Avoids a SELECT MAX(sequence) round-trip for every event after the first
        # one per run.  Safe because asyncio is single-threaded and the worker
        # serialises event processing per run via an asyncio.Lock.
        self._seq_counters: dict[str, int] = {}

    async def publish(
        self,
        event_type: EventType | str,
        workspace_id: UUID | str,
        run_id: UUID | str | None = None,
        user_id: UUID | str | None = None,
        payload: dict[str, Any] | None = None,
        executor_code: str | None = None,
        parent_event_id: UUID | str | None = None,
        app_id: UUID | str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        auto_commit: bool = False,
    ) -> Event:
        """Publish an event to PostgreSQL and Redis.

        Args:
            event_type: Type of event (from EventType enum or string)
            workspace_id: Workspace this event belongs to
            run_id: Run this event belongs to (optional)
            user_id: User who triggered the event (optional)
            payload: Event payload data
            parent_event_id: Parent event ID for hierarchical events
            auto_commit: Whether to commit the transaction

        Returns:
            Created Event instance
        """
        # Normalize IDs to strings for storage
        workspace_id_str = (
            str(workspace_id) if isinstance(workspace_id, UUID) else workspace_id
        )
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id
        app_id_str = str(app_id) if isinstance(app_id, UUID) else app_id
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
        parent_event_id_str = (
            str(parent_event_id)
            if isinstance(parent_event_id, UUID)
            else parent_event_id
        )
        event_type_str = (
            event_type.value if isinstance(event_type, EventType) else event_type
        )

        # Get the next sequence number for this run (or workspace if no run)
        sequence = await self._get_next_sequence(workspace_id_str, run_id_str)

        jsonable_payload = _to_jsonable(payload)

        # Build the event with explicit Python-side defaults so the object is
        # fully usable (including .id) without a DB flush or refresh.
        event = Event(
            id=uuid4(),
            created_at=datetime.now(UTC),
            event_type=event_type_str,
            workspace_id=workspace_id_str,
            run_id=run_id_str,
            app_id=app_id_str,
            user_id=user_id_str,
            payload=jsonable_payload,
            sequence=sequence,
            parent_event_id=parent_event_id_str,
            executor_code=executor_code,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        # Check if this run is in buffered mode (Redis-only during execution).
        buffer = (
            self._run_buffer_provider(run_id_str)
            if (self._run_buffer_provider and run_id_str)
            else None
        )

        if buffer is not None:
            # Buffered mode: hold in memory, broadcast to Redis for real-time SSE.
            # The buffer is flushed to DB atomically when the run completes/fails.
            buffer.append(event)
            if self.redis:
                await self._broadcast_to_redis(event)
        else:
            # Normal mode: persist to PostgreSQL immediately.
            self.db.add(event)
            if auto_commit:
                await self.db.commit()
            else:
                await self.db.flush()

            if run_id_str:
                await self._update_run_sequence(
                    run_id_str,
                    sequence,
                    auto_commit,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            if self.redis:
                await self._broadcast_to_redis(event)

        logger.debug(
            f"Published event {event.id} type={event_type_str} "
            f"workspace={workspace_id_str} run={run_id_str} seq={sequence}"
        )

        return event

    async def publish_batch(
        self,
        events: list[dict[str, Any]],
        auto_commit: bool = False,
    ) -> list[Event]:
        """Publish multiple events in a batch.

        Args:
            events: List of event dictionaries with keys:
                - event_type, workspace_id, run_id, user_id, payload, parent_event_id
            auto_commit: Whether to commit the transaction

        Returns:
            List of created Event instances
        """
        created_events = []

        for event_data in events:
            event = await self.publish(
                event_type=event_data["event_type"],
                workspace_id=event_data["workspace_id"],
                run_id=event_data.get("run_id"),
                user_id=event_data.get("user_id"),
                payload=event_data.get("payload"),
                parent_event_id=event_data.get("parent_event_id"),
                auto_commit=False,  # Batch commit at the end
            )
            created_events.append(event)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return created_events

    async def _get_next_sequence(self, workspace_id: str, run_id: str | None) -> int:
        """Return the next sequence number, using an in-memory counter.

        The first call for a given run (or workspace) queries the DB to find
        the current max; every subsequent call just increments the counter in
        memory.  This eliminates one ``SELECT MAX`` round-trip per event.

        When the Worker provides a shared ``run_seq_cursor`` dict, that dict is
        consulted before hitting the DB.  This prevents duplicate sequence numbers
        across the multiple per-dispatch EventPublisher instances that may serve
        the same run while it is being buffered (and therefore not committed to DB).
        """
        key = run_id if run_id else f"ws:{workspace_id}"

        if key not in self._seq_counters:
            # Check Worker-level cursor first — avoids a stale DB read when
            # buffered events have incremented the sequence but aren't in DB yet.
            if (
                self._run_seq_cursor is not None
                and run_id
                and run_id in self._run_seq_cursor
            ):
                self._seq_counters[key] = self._run_seq_cursor[run_id]
            else:
                # Cold start: read the current maximum from the DB.
                if run_id:
                    stmt = select(func.coalesce(func.max(Event.sequence), 0)).where(
                        Event.run_id == run_id
                    )
                else:
                    stmt = select(func.coalesce(func.max(Event.sequence), 0)).where(
                        Event.workspace_id == workspace_id,
                        Event.run_id.is_(None),
                    )
                result = await self.db.execute(stmt)
                self._seq_counters[key] = result.scalar() or 0

        self._seq_counters[key] += 1

        # Keep the Worker cursor in sync so the next publisher picks up correctly.
        if (
            self._run_seq_cursor is not None
            and run_id
            and run_id in self._run_seq_cursor
        ):
            self._run_seq_cursor[run_id] = self._seq_counters[key]

        return self._seq_counters[key]

    def release_sequence_counter(self, run_id: str) -> None:
        """Remove the in-memory counter for a completed run to free memory."""
        self._seq_counters.pop(run_id, None)

    async def _update_run_sequence(
        self,
        run_id: str,
        sequence: int,
        auto_commit: bool,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Update the run's last_event_sequence (and optionally token totals).

        Avoids the SELECT + ORM-update pattern (two round-trips) by issuing a
        single UPDATE statement directly.
        """
        values: dict[str, Any] = {
            "last_event_sequence": sequence,
            "updated_at": datetime.now(UTC),
        }
        if input_tokens or output_tokens:
            values["input_tokens"] = Run.input_tokens + input_tokens
            values["output_tokens"] = Run.output_tokens + output_tokens

        stmt = update(Run).where(Run.id == run_id).values(**values)
        await self.db.execute(stmt)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

    async def _broadcast_to_redis(self, event: Event) -> None:
        """Broadcast event to Redis Streams using a pipeline (one round-trip).

        Streams written:
          - run:{id}:events       – SSE clients subscribed to this run
          - workspace:{id}:events – SSE clients subscribed to the workspace / worker consumers
        """
        if not self.redis:
            return
        fields = event.to_redis_fields()
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                if event.run_id:
                    run_stream = (
                        f"{REDIS_RUN_LABEL}:{event.run_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
                    )
                    pipe.xadd(run_stream, fields, maxlen=1000, approximate=True)

                workspace_id = str(event.workspace_id)
                # Primary stream for both SSE and worker consumers
                workspace_stream = (
                    f"{RE_CODE_WORKSPACE}:{workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
                )
                pipe.xadd(workspace_stream, fields, maxlen=10000, approximate=True)

                await pipe.execute()

            logger.debug(
                f"Broadcast event {event.event_type} workspace={workspace_id} run={event.run_id}"
            )
        except Exception as e:
            logger.error(f"Failed to broadcast event {event.id} to Redis: {e}")
            # Don't fail the whole publication if Redis broadcast fails
