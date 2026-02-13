# aiwen/services/events/event_publisher.py
"""Event Publisher service for publishing events to PostgreSQL and Redis."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.config.factory import get_settings
from aiwen.models.events.event import Event
from aiwen.models.runs.run import Run
from aiwen.schemas.events.event_payloads import EventType

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_STREAM_EVENTS_SUFFIX = _redis_cfg.stream_events_suffix
REDIS_WORKSPACE_LABEL = _redis_cfg.workspace_label


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

    def __init__(self, db: AsyncSession, redis_client: redis_async.Redis | None = None):
        """Initialize EventPublisher.

        Args:
            db: SQLAlchemy async session
            redis_client: Redis async client (optional, for real-time broadcasting)
        """
        self.db = db
        self.redis = redis_client

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

        # Create event in PostgreSQL
        event = Event(
            event_type=event_type_str,
            workspace_id=workspace_id_str,
            run_id=run_id_str,
            app_id=app_id_str,
            user_id=user_id_str,
            payload=_to_jsonable(payload),
            sequence=sequence,
            parent_event_id=parent_event_id_str,
            executor_code=executor_code,
        )

        self.db.add(event)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(event)

        # Update run's last_event_sequence if applicable
        if run_id_str:
            await self._update_run_sequence(run_id_str, sequence, auto_commit)

        # Broadcast to Redis Streams for real-time delivery
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
        """Get the next sequence number for a run or workspace.

        Args:
            workspace_id: Workspace ID
            run_id: Run ID (optional)

        Returns:
            Next sequence number
        """
        if run_id:
            # Get max sequence for this run
            stmt = select(func.coalesce(func.max(Event.sequence), 0)).where(
                Event.run_id == run_id
            )
        else:
            # Get max sequence for workspace-level events (no run_id)
            stmt = select(func.coalesce(func.max(Event.sequence), 0)).where(
                Event.workspace_id == workspace_id,
                Event.run_id.is_(None),
            )

        result = await self.db.execute(stmt)
        max_seq = result.scalar() or 0
        return max_seq + 1

    async def _update_run_sequence(
        self, run_id: str, sequence: int, auto_commit: bool
    ) -> None:
        """Update the run's last_event_sequence.

        Args:
            run_id: Run ID
            sequence: New sequence number
            auto_commit: Whether to commit
        """
        stmt = select(Run).where(Run.id == run_id)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if run:
            run.last_event_sequence = sequence
            run.updated_at = datetime.now(UTC)

            if auto_commit:
                await self.db.commit()
            else:
                await self.db.flush()

    async def _broadcast_to_redis(self, event: Event) -> None:
        """Broadcast event to Redis Streams.

        Args:
            event: Event to broadcast
        """
        if not self.redis:
            return
        fields = event.to_redis_fields()
        try:
            # Publish to run stream if applicable
            if event.run_id:
                run_stream = (
                    f"{REDIS_RUN_LABEL}:{event.run_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
                )
                await self.redis.xadd(
                    name=run_stream,
                    fields=fields,
                    maxlen=1000,
                    approximate=True,
                )
            logger.info(f"Success publish to {REDIS_RUN_LABEL} an event")
            # Publish to the workspace stream
            workspace_stream = f"{REDIS_WORKSPACE_LABEL}:{event.workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
            await self.redis.xadd(
                name=workspace_stream,
                fields=fields,
                maxlen=10000,
                approximate=True,
            )

            logger.info(f"Success publish to {REDIS_WORKSPACE_LABEL} an event")
            # Publish USER_MESSAGE events to global task queue for Worker consumption
            if event.event_type == EventType.USER_MESSAGE and event.run_id:
                await self.redis.xadd(
                    name=REDIS_EXECUTOR_LABEL,
                    fields=fields,
                    maxlen=10000,
                    approximate=True,
                )

                logger.info(f"Success publish to {REDIS_EXECUTOR_LABEL} an event")
        except Exception as e:
            logger.error(f"Failed to broadcast event {event.id} to Redis: {e}")
            # Don't fail the whole publication if Redis broadcast fails
