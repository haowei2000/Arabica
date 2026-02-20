# aiwen/services/events/event_crud.py
"""CRUD operations for Event model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.events.event import Event
from aiwen.models.workspaces.workspace_member import WorkspaceMember


def _normalize_uuid(val: str | UUID) -> str:
    """Normalize a UUID value to string."""
    if isinstance(val, UUID):
        return str(val)
    return val


class EventCRUD:
    """CRUD operations for Event model.

    Provides standard database queries for events:
    get by ID, list with pagination, and filtered search
    by user, workspace, run, and event type.
    """

    def __init__(self, db_session: AsyncSession):
        """Initialize EventCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def get_by_id(self, event_id: str | UUID) -> Event | None:
        """Get event by ID.

        Args:
            event_id: The event ID

        Returns:
            Event instance or None if not found
        """
        normalized_id = _normalize_uuid(event_id)
        stmt = select(Event).where(Event.id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        event_type: str,
        workspace_id: str | UUID,
        run_id: str | UUID | None = None,
        user_id: str | UUID | None = None,
        payload: dict[str, Any] | None = None,
        sequence: int = 0,
        parent_event_id: str | UUID | None = None,
        auto_commit: bool = False,
    ) -> Event:
        """Create a new event.

        For most use cases, prefer EventPublisher.publish() which also
        handles sequencing and Redis broadcasting. Use this method only
        when you need raw event insertion without side effects.

        Args:
            event_type: Event type string
            workspace_id: Workspace ID
            run_id: Run ID (optional)
            user_id: User ID (optional)
            payload: Event payload data
            sequence: Event sequence number
            parent_event_id: Parent event ID (optional)
            auto_commit: If True, immediately commit

        Returns:
            Created Event instance
        """
        event = Event(
            event_type=event_type,
            workspace_id=_normalize_uuid(workspace_id),
            run_id=_normalize_uuid(run_id) if run_id else None,
            user_id=_normalize_uuid(user_id) if user_id else None,
            payload=payload,
            sequence=sequence,
            parent_event_id=_normalize_uuid(parent_event_id)
            if parent_event_id
            else None,
        )
        self.db.add(event)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(event)
        return event

    async def delete(
        self,
        event_id: str | UUID,
        auto_commit: bool = False,
    ) -> bool:
        """Delete an event by ID.

        Args:
            event_id: The event ID
            auto_commit: If True, immediately commit

        Returns:
            True if deleted, False if not found
        """
        event = await self.get_by_id(event_id)
        if not event:
            return False

        await self.db.delete(event)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    # ── Search by workspace ───────────────────────────────────────

    async def list_by_workspace(
        self,
        workspace_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
        event_types: list[str] | None = None,
    ) -> tuple[list[Event], int]:
        """List events for a workspace with optional type filter.

        Args:
            workspace_id: The workspace ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            event_types: Filter by event types (optional)

        Returns:
            Tuple of (list of events, total count)
        """
        normalized_id = _normalize_uuid(workspace_id)

        conditions = [Event.workspace_id == normalized_id]
        if event_types:
            conditions.append(Event.event_type.in_(event_types))

        total = await self._count(conditions)

        stmt = (
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.sequence.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    # ── Search by run ─────────────────────────────────────────────

    async def list_by_run(
        self,
        run_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
        event_types: list[str] | None = None,
    ) -> tuple[list[Event], int]:
        """List events for a run with optional type filter.

        Args:
            run_id: The run ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            event_types: Filter by event types (optional)

        Returns:
            Tuple of (list of events, total count)
        """
        normalized_id = _normalize_uuid(run_id)

        conditions = [Event.run_id == normalized_id]
        if event_types:
            conditions.append(Event.event_type.in_(event_types))

        total = await self._count(conditions)

        stmt = (
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.sequence.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    # ── Search by user ────────────────────────────────────────────

    async def list_by_user(
        self,
        user_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
        event_types: list[str] | None = None,
        workspace_id: str | UUID | None = None,
    ) -> tuple[list[Event], int]:
        """List events triggered by a user.

        Args:
            user_id: The user ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            event_types: Filter by event types (optional)
            workspace_id: Narrow to a specific workspace (optional)

        Returns:
            Tuple of (list of events, total count)
        """
        normalized_user_id = _normalize_uuid(user_id)

        member_ws_subq = select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == normalized_user_id,
            WorkspaceMember.invitation_status == "accepted",
        )
        ownership_condition = or_(
            Event.user_id == normalized_user_id,
            Event.workspace_id.in_(member_ws_subq),
        )
        conditions = [ownership_condition]
        if event_types:
            conditions.append(Event.event_type.in_(event_types))
        if workspace_id:
            conditions.append(Event.workspace_id == _normalize_uuid(workspace_id))

        total = await self._count(conditions)

        stmt = (
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    # ── Combined search ───────────────────────────────────────────

    async def search(
        self,
        workspace_id: str | UUID | None = None,
        run_id: str | UUID | None = None,
        user_id: str | UUID | None = None,
        event_types: list[str] | None = None,
        from_sequence: int | None = None,
        to_sequence: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Event], int]:
        """Search events with multiple filters.

        Args:
            workspace_id: Filter by workspace (optional)
            run_id: Filter by run (optional)
            user_id: Filter by user (optional)
            event_types: Filter by event types (optional)
            from_sequence: Minimum sequence number inclusive (optional)
            to_sequence: Maximum sequence number inclusive (optional)
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of events, total count)
        """
        conditions: list = []

        if workspace_id:
            conditions.append(Event.workspace_id == _normalize_uuid(workspace_id))
        if run_id:
            conditions.append(Event.run_id == _normalize_uuid(run_id))
        if user_id:
            conditions.append(Event.user_id == _normalize_uuid(user_id))
        if event_types:
            conditions.append(Event.event_type.in_(event_types))
        if from_sequence is not None:
            conditions.append(Event.sequence >= from_sequence)
        if to_sequence is not None:
            conditions.append(Event.sequence <= to_sequence)

        total = await self._count(conditions)

        stmt = select(Event)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Event.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    # ── Helpers ────────────────────────────────────────────────────

    async def get_latest_by_run(
        self,
        run_id: str | UUID,
        event_type: str | None = None,
    ) -> Event | None:
        """Get the latest event for a run.

        Args:
            run_id: The run ID
            event_type: Optionally restrict to a specific event type

        Returns:
            Most recent Event or None
        """
        normalized_id = _normalize_uuid(run_id)

        conditions = [Event.run_id == normalized_id]
        if event_type:
            conditions.append(Event.event_type == event_type)

        stmt = (
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.sequence.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_run(self, run_id: str | UUID) -> int:
        """Count events in a run.

        Args:
            run_id: The run ID

        Returns:
            Number of events
        """
        normalized_id = _normalize_uuid(run_id)
        stmt = select(func.count(Event.id)).where(Event.run_id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def count_by_workspace(self, workspace_id: str | UUID) -> int:
        """Count events in a workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            Number of events
        """
        normalized_id = _normalize_uuid(workspace_id)
        stmt = select(func.count(Event.id)).where(Event.workspace_id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _count(self, conditions: list) -> int:
        """Count events matching conditions.

        Args:
            conditions: List of SQLAlchemy filter conditions

        Returns:
            Total count
        """
        count_stmt = select(func.count(Event.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        return count_result.scalar() or 0
