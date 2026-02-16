# aiwen/services/events/event_consumer.py
"""Event Consumer service for subscribing to and replaying events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
import json
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.config.factory import get_settings
from aiwen.core.enums import EventType
from aiwen.models.events.event import Event
from aiwen.schemas.events.event_payloads import EventResponse

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
EVENT_TYPE_ERROR = _redis_cfg.event_type_error
EVENT_TYPE_KEEPALIVE = _redis_cfg.event_type_keepalive
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_STREAM_EVENTS_SUFFIX = _redis_cfg.stream_events_suffix
REDIS_WORKSPACE_LABEL = _redis_cfg.workspace_label


class EventConsumer:
    """Event Consumer for real-time event streaming via Redis.

    Subscribes to Redis Streams and yields events as they arrive.
    Used for SSE (Server-Sent Events) or WebSocket connections.
    """

    def __init__(self, redis_client: redis_async.Redis):
        """Initialize EventConsumer.

        Args:
            redis_client: Redis async client
        """
        self.redis = redis_client

    async def subscribe_run(
        self,
        run_id: UUID | str,
        last_id: str = "$",
        timeout_ms: int = 300000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to events for a specific run.

        Args:
            run_id: Run ID to subscribe to
            last_id: Last event ID received ($ for new events only, 0 for all)
            timeout_ms: Read timeout in milliseconds

        Yields:
            Event data dictionaries
        """
        stream_name = f"{REDIS_RUN_LABEL}:{run_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
        current_id = last_id

        while True:
            try:
                # XREAD with blocking
                result = await self.redis.xread(
                    streams={stream_name: current_id},
                    block=timeout_ms,
                    count=100,
                )

                if not result:
                    # Timeout, yield keepalive
                    yield {"type": EVENT_TYPE_KEEPALIVE, "timestamp": datetime.now().isoformat()}
                    continue

                for stream, messages in result:
                    for message_id, data in messages:
                        current_id = message_id
                        event = self._parse_event_data(data)
                        yield event

            except asyncio.CancelledError:
                logger.debug(f"Run subscription cancelled for {run_id}")
                break
            except Exception as e:
                logger.error(f"Error reading from stream {stream_name}: {e}")
                yield {"type": EVENT_TYPE_ERROR, "message": str(e)}
                await asyncio.sleep(1)  # Brief pause before retry

    async def subscribe_workspace(
        self,
        workspace_id: UUID | str,
        last_id: str = "$",
        timeout_ms: int = 300000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to events for a specific workspace.

        Args:
            workspace_id: Workspace ID to subscribe to
            last_id: Last event ID received ($ for new events only)
            timeout_ms: Read timeout in milliseconds

        Yields:
            Event data dictionaries
        """
        stream_name = f"{REDIS_WORKSPACE_LABEL}:{workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
        current_id = last_id

        while True:
            try:
                result = await self.redis.xread(
                    streams={stream_name: current_id},
                    block=timeout_ms,
                    count=100,
                )

                if not result:
                    yield {"type": EVENT_TYPE_KEEPALIVE, "timestamp": datetime.now().isoformat()}
                    continue

                for stream, messages in result:
                    for message_id, data in messages:
                        current_id = message_id
                        event = self._parse_event_data(data)
                        yield event

            except asyncio.CancelledError:
                logger.debug(f"Workspace subscription cancelled for {workspace_id}")
                break
            except Exception as e:
                logger.error(f"Error reading from stream {stream_name}: {e}")
                yield {"type": EVENT_TYPE_ERROR, "message": str(e)}
                await asyncio.sleep(1)

    def _parse_event_data(self, data: dict[bytes, bytes]) -> dict[str, Any]:
        """Parse event data from Redis.

        Args:
            data: Raw Redis stream data

        Returns:
            Parsed event dictionary
        """
        # Decode bytes to strings
        decoded = {
            k.decode() if isinstance(k, bytes) else k: v.decode()
            if isinstance(v, bytes)
            else v
            for k, v in data.items()
        }

        # Parse JSON payload
        if "payload" in decoded:
            try:
                decoded["payload"] = json.loads(decoded["payload"])
            except json.JSONDecodeError:
                pass

        # Convert sequence to int
        if "sequence" in decoded:
            decoded["sequence"] = int(decoded["sequence"])

        return decoded


class EventReplayer:
    """Event Replayer for historical event retrieval from PostgreSQL.

    Used to replay events for state reconstruction or catching up
    after reconnection.
    """

    def __init__(self, db: AsyncSession):
        """Initialize EventReplayer.

        Args:
            db: SQLAlchemy async session
        """
        self.db = db

    async def replay_run(
        self,
        run_id: UUID | str,
        from_sequence: int = 0,
        to_sequence: int | None = None,
        limit: int = 1000,
    ) -> list[EventResponse]:
        """Replay events for a run from PostgreSQL.

        Args:
            run_id: Run ID to replay events for
            from_sequence: Start sequence (inclusive)
            to_sequence: End sequence (inclusive, optional)
            limit: Maximum number of events to return

        Returns:
            List of EventResponse objects
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id

        stmt = select(Event).where(
            Event.run_id == run_id_str,
            Event.sequence >= from_sequence,
        )

        if to_sequence is not None:
            stmt = stmt.where(Event.sequence <= to_sequence)

        stmt = stmt.order_by(Event.sequence).limit(limit)

        result = await self.db.execute(stmt)
        events = result.scalars().all()

        return [EventResponse.model_validate(e) for e in events]

    async def replay_workspace(
        self,
        workspace_id: UUID | str,
        from_sequence: int = 0,
        limit: int = 1000,
        event_types: list[str] | None = None,
    ) -> list[EventResponse]:
        """Replay events for a workspace from PostgreSQL.

        Args:
            workspace_id: Workspace ID to replay events for
            from_sequence: Start sequence (inclusive)
            limit: Maximum number of events to return
            event_types: Filter by event types (optional)

        Returns:
            List of EventResponse objects
        """
        workspace_id_str = (
            str(workspace_id) if isinstance(workspace_id, UUID) else workspace_id
        )

        stmt = select(Event).where(
            Event.workspace_id == workspace_id_str,
            Event.sequence >= from_sequence,
        )

        if event_types:
            stmt = stmt.where(Event.event_type.in_(event_types))

        stmt = stmt.order_by(Event.sequence).limit(limit)

        result = await self.db.execute(stmt)
        events = result.scalars().all()

        return [EventResponse.model_validate(e) for e in events]

    async def get_latest_state(
        self,
        run_id: UUID | str,
    ) -> dict[str, Any]:
        """Reconstruct the latest state by replaying all events.

        This method replays all events for a run and computes the
        current state, including:
        - All messages exchanged
        - Tool calls and results
        - Current run status

        Args:
            run_id: Run ID to get state for

        Returns:
            Dictionary representing current state
        """
        events = await self.replay_run(run_id, limit=10000)

        state = {
            "messages": [],
            "tool_calls": {},
            "status": "unknown",
            "last_sequence": 0,
        }

        for event in events:
            state["last_sequence"] = event.sequence

            if event.event_type == EventType.USER_MESSAGE:
                state["messages"].append(
                    {
                        "role": "user",
                        "content": event.payload.get("content", "")
                        if event.payload
                        else "",
                        "timestamp": event.created_at.isoformat(),
                    }
                )

            elif event.event_type == EventType.AGENT_MESSAGE:
                state["messages"].append(
                    {
                        "role": "assistant",
                        "content": event.payload.get("content", "")
                        if event.payload
                        else "",
                        "timestamp": event.created_at.isoformat(),
                    }
                )

            elif event.event_type == EventType.TOOL_CALL:
                if event.payload:
                    tool_id = event.payload.get("tool_id")
                    if tool_id:
                        state["tool_calls"][tool_id] = {
                            "name": event.payload.get("tool_name"),
                            "arguments": event.payload.get("arguments"),
                            "status": "pending",
                        }

            elif event.event_type == EventType.TOOL_RESULT:
                if event.payload:
                    tool_id = event.payload.get("tool_id")
                    if tool_id and tool_id in state["tool_calls"]:
                        state["tool_calls"][tool_id]["result"] = event.payload.get(
                            "result"
                        )
                        state["tool_calls"][tool_id]["status"] = (
                            "success" if event.payload.get("success") else "error"
                        )

            elif event.event_type == EventType.RUN_STATE_CHANGE:
                if event.payload:
                    state["status"] = event.payload.get("new_state", state["status"])

        return state

    async def get_events_after(
        self,
        run_id: UUID | str,
        after_sequence: int,
        limit: int = 100,
    ) -> list[EventResponse]:
        """Get events after a specific sequence (for catch-up).

        Args:
            run_id: Run ID
            after_sequence: Get events after this sequence (exclusive)
            limit: Maximum number of events

        Returns:
            List of EventResponse objects
        """
        return await self.replay_run(
            run_id=run_id,
            from_sequence=after_sequence + 1,
            limit=limit,
        )
