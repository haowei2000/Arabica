# structure/services/events/event_publisher.py
"""Event Publisher service for publishing events to PostgreSQL and Redis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis_async
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.factory import get_settings
from structure.models.events.event import Event
from structure.models.runs.run import Run
from structure.schemas.events.event_payloads import EventType
from structure.services.events.conversation_window import record_conversation_event
from structure.services.events.event_commands import (
    command_created_at_ms,
    executor_command_stream_name,
)

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_STREAM_EVENTS_SUFFIX = _redis_cfg.stream_events_suffix
REDIS_WORKSPACE_LABEL = _redis_cfg.workspace_label

# Event types that workers must consume from the executor command stream.
_EXECUTOR_COMMAND_TYPES: frozenset[str] = frozenset(
    {
        # External input events that the worker must receive to validate + re-publish
        # as TO_EXECUTOR.
        str(EventType.USER_MESSAGE),
        str(EventType.USER_FEEDBACK),
        str(EventType.TOOL_RESULT),
        str(EventType.TOOL_ERROR),
        # TOOL_CALL is dispatched directly to handle_tool_call (no TO_EXECUTOR hop).
        str(EventType.TOOL_CALL),
        # Infrastructure events handled before executor routing.
        str(EventType.RUN_CANCELLED),
        # Internal worker-routing event: carries validated events to the executor.
        str(EventType.TO_EXECUTOR),
    }
)

_VOLATILE_REALTIME_TYPES: frozenset[str] = frozenset(
    {
        str(EventType.AGENT_TOKEN),
        str(EventType.AGENT_HEARTBEAT),
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
                for a run_id, or None if that run is not being buffered. Kept for
                older worker hooks; semantic events now write through immediately.
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
        event_type_str = self._event_type_str(event_type)
        if event_type_str in _VOLATILE_REALTIME_TYPES:
            return await self.publish_realtime(
                event_type=event_type_str,
                workspace_id=workspace_id,
                run_id=run_id,
                user_id=user_id,
                payload=payload,
                executor_code=executor_code,
                parent_event_id=parent_event_id,
                app_id=app_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        return await self.publish_durable(
            event_type=event_type_str,
            workspace_id=workspace_id,
            run_id=run_id,
            user_id=user_id,
            payload=payload,
            executor_code=executor_code,
            parent_event_id=parent_event_id,
            app_id=app_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            auto_commit=auto_commit,
        )

    async def publish_durable(
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
        """Persist a semantic event before broadcasting it.

        Durable events are written to PostgreSQL first. When the transaction is
        committed here, executor-relevant events are then scheduled on the
        worker command stream. Callers that pass ``auto_commit=False`` are
        responsible for scheduling after their transaction commits.
        """
        event = await self._build_event(
            event_type=event_type,
            workspace_id=workspace_id,
            run_id=run_id,
            user_id=user_id,
            payload=payload,
            executor_code=executor_code,
            parent_event_id=parent_event_id,
            app_id=app_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        write_start = perf_counter()
        self.db.add(event)
        await self.db.flush()

        if event.run_id:
            await self._update_run_sequence(
                str(event.run_id),
                event.sequence,
                auto_commit=False,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        durable_event_write_ms = int((perf_counter() - write_start) * 1000)

        if self.redis:
            await self._broadcast_to_redis(event)
            if auto_commit and str(event.event_type) in _EXECUTOR_COMMAND_TYPES:
                await self.publish_executor_command(event)
        if auto_commit:
            record_conversation_event(event)

        logger.debug(
            "Published durable event %s type=%s workspace=%s run=%s seq=%s "
            "durable_event_write_ms=%s",
            event.id,
            event.event_type,
            event.workspace_id,
            event.run_id,
            event.sequence,
            durable_event_write_ms,
        )
        return event

    async def publish_realtime(
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
    ) -> Event:
        """Publish a volatile realtime event to Redis only."""
        event = await self._build_event(
            event_type=event_type,
            workspace_id=workspace_id,
            run_id=run_id,
            user_id=user_id,
            payload=payload,
            executor_code=executor_code,
            parent_event_id=parent_event_id,
            app_id=app_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if self.redis:
            await self._broadcast_to_redis(event)
        logger.debug(
            "Published realtime event %s type=%s workspace=%s run=%s seq=%s",
            event.id,
            event.event_type,
            event.workspace_id,
            event.run_id,
            event.sequence,
        )
        return event

    async def publish_executor_command(
        self,
        event: Event,
        *,
        executor_code: str | None = None,
    ) -> None:
        """Schedule a durable event for worker execution."""
        if not self.redis or not event.run_id:
            return

        workspace_id = str(event.workspace_id)
        stream_name = executor_command_stream_name(workspace_id)
        fields = {
            "event_id": str(event.id),
            "event_type": str(event.event_type),
            "workspace_id": workspace_id,
            "run_id": str(event.run_id),
            "executor_code": executor_code or event.executor_code or "",
            "created_at_ms": command_created_at_ms(),
        }
        try:
            await self.redis.xadd(stream_name, fields, maxlen=10000, approximate=True)
            logger.debug(
                "Queued executor command event=%s type=%s stream=%s",
                event.id,
                event.event_type,
                stream_name,
            )
        except Exception as exc:
            logger.error(
                "Failed to queue executor command event=%s stream=%s: %s",
                event.id,
                stream_name,
                exc,
                exc_info=True,
            )

    async def _build_event(
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
    ) -> Event:
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
        sequence = await self._get_next_sequence(workspace_id_str, run_id_str)

        return Event(
            id=uuid4(),
            created_at=datetime.now(UTC),
            event_type=self._event_type_str(event_type),
            workspace_id=workspace_id_str,
            run_id=run_id_str,
            app_id=app_id_str,
            user_id=user_id_str,
            payload=_to_jsonable(payload),
            sequence=sequence,
            parent_event_id=parent_event_id_str,
            executor_code=executor_code,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _event_type_str(event_type: EventType | str) -> str:
        return (
            event_type.value if isinstance(event_type, EventType) else str(event_type)
        )

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
            for event in created_events:
                if self.redis and str(event.event_type) in _EXECUTOR_COMMAND_TYPES:
                    await self.publish_executor_command(event)
                record_conversation_event(event)
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
          - workspace:{id}:events – SSE clients subscribed to the workspace
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
                workspace_stream = f"{REDIS_WORKSPACE_LABEL}:{workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
                pipe.xadd(workspace_stream, fields, maxlen=10000, approximate=True)

                await pipe.execute()

            logger.debug(
                f"Broadcast event {event.event_type} workspace={workspace_id} run={event.run_id}"
            )
        except Exception as e:
            logger.error(
                "Failed to broadcast event %s to Redis: %s redis_publish_error_count=1",
                event.id,
                e,
            )
            # Don't fail the whole publication if Redis broadcast fails
