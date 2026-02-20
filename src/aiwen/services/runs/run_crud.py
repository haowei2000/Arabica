# aiwen/services/runs/run_crud.py
"""CRUD operations for Run model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.runs import TriggerType
from aiwen.models.runs.run import Run
from aiwen.models.workspaces.workspace import Workspace
from aiwen.models.workspaces.workspace_member import WorkspaceMember


def normalize_uuid_to_str(val: str | UUID) -> str:
    """Normalize a UUID value to string."""
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class RunCRUD:
    """CRUD operations for Run model."""

    def __init__(self, db_session: AsyncSession):
        """Initialize RunCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(
        self,
        workspace_id: str | UUID,
        app_id: str | UUID,
        user_id: str | UUID,
        parent_run_id: str | UUID | None = None,
        trigger_type: TriggerType = TriggerType.USER,
        auto_commit: bool = False,
    ) -> Run:
        """Create a new run.

        Args:
            workspace_id: Workspace ID
            app_id: App ID
            user_id: User ID who initiated
            parent_run_id: Parent run ID (for nested runs)
            trigger_type: Trigger type (user/tool_callback/agent/system)
            input_data: Initial input data
            auto_commit: If True, immediately commit

        Returns:
            Created Run instance
        """
        run = Run(
            workspace_id=normalize_uuid_to_str(workspace_id),
            app_id=normalize_uuid_to_str(app_id),
            user_id=normalize_uuid_to_str(user_id),
            parent_run_id=normalize_uuid_to_str(parent_run_id)
            if parent_run_id
            else None,
            trigger_type=trigger_type,
            status="pending",
        )
        self.db.add(run)

        # Increment workspace run count
        await self._increment_workspace_run_count(normalize_uuid_to_str(workspace_id))

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(run)
        return run

    async def get_by_id(
        self,
        run_id: str | UUID,
    ) -> Run | None:
        """Get run by ID.

        Args:
            run_id: The run ID

        Returns:
            Run instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(run_id)
        stmt = select(Run).where(Run.id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_and_user(
        self,
        run_id: str | UUID,
        user_id: str | UUID,
    ) -> Run | None:
        """Get run by ID with user validation.

        Args:
            run_id: The run ID
            user_id: The user ID

        Returns:
            Run instance or None if not found or unauthorized
        """
        normalized_id = normalize_uuid_to_str(run_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        stmt = select(Run).where(
            and_(
                Run.id == normalized_id,
                Run.user_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_workspace(
        self,
        workspace_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> tuple[list[Run], int]:
        """List runs for a workspace.

        Args:
            workspace_id: The workspace ID
            skip: Number of records to skip
            limit: Maximum number of records
            status: Filter by status (optional)

        Returns:
            Tuple of (list of runs, total count)
        """
        normalized_workspace_id = normalize_uuid_to_str(workspace_id)

        conditions = [Run.workspace_id == normalized_workspace_id]
        if status:
            conditions.append(Run.status == status)

        # Count query
        count_stmt = select(func.count(Run.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Run)
            .where(and_(*conditions))
            .order_by(Run.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def list_by_user(
        self,
        user_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
    ) -> tuple[list[Run], int]:
        """List runs for a user.

        Args:
            user_id: The user ID
            skip: Number of records to skip
            limit: Maximum number of records
            status: Filter by status (optional)

        Returns:
            Tuple of (list of runs, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        member_ws_subq = select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == normalized_user_id,
            WorkspaceMember.invitation_status == "accepted",
        )
        ownership_condition = or_(
            Run.user_id == normalized_user_id,
            Run.workspace_id.in_(member_ws_subq),
        )
        conditions = [ownership_condition]
        if status:
            conditions.append(Run.status == status)

        # Count query
        count_stmt = select(func.count(Run.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Run)
            .where(and_(*conditions))
            .order_by(Run.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def list_active_by_workspace(
        self,
        workspace_id: str | UUID,
    ) -> list[Run]:
        """List active (non-terminal) runs for a workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            List of active runs
        """
        normalized_workspace_id = normalize_uuid_to_str(workspace_id)

        stmt = (
            select(Run)
            .where(
                and_(
                    Run.workspace_id == normalized_workspace_id,
                    Run.status.in_(["pending", "running", "waiting"]),
                )
            )
            .order_by(Run.created_at.desc())
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        run_id: str | UUID,
        status: str | None = None,
        output_data: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
        waiting_for: dict[str, Any] | None = None,
        auto_commit: bool = False,
    ) -> Run | None:
        """Update a run.

        Args:
            run_id: The run ID
            status: New status (optional)
            output_data: Output data (optional)
            error: Error message (optional)
            error_code: Error code (optional)
            waiting_for: Waiting info (optional)
            auto_commit: If True, immediately commit

        Returns:
            Updated Run or None if not found
        """
        run = await self.get_by_id(run_id)
        if not run:
            return None

        if status is not None:
            run.status = status
            # Update timestamps based on status
            if status == "running" and run.started_at is None:
                run.started_at = datetime.now(UTC)
            elif status in ("finished", "failed", "cancelled"):
                run.completed_at = datetime.now(UTC)

        if output_data is not None:
            run.output_data = output_data
        if error is not None:
            run.error = error
        if error_code is not None:
            run.error_code = error_code
        if waiting_for is not None:
            run.waiting_for = waiting_for

        run.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(run)
        return run

    async def get_child_runs(
        self,
        parent_run_id: str | UUID,
    ) -> list[Run]:
        """Get child runs of a parent run.

        Args:
            parent_run_id: The parent run ID

        Returns:
            List of child runs
        """
        normalized_id = normalize_uuid_to_str(parent_run_id)
        stmt = (
            select(Run)
            .where(Run.parent_run_id == normalized_id)
            .order_by(Run.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_legacy_task_id(
        self,
        task_id: str | UUID,
    ) -> Run | None:
        """Get run by legacy task ID (for migration).

        Args:
            task_id: The legacy task ID

        Returns:
            Run instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(task_id)
        stmt = select(Run).where(Run.legacy_task_id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _increment_workspace_run_count(
        self,
        workspace_id: str,
    ) -> None:
        """Increment the workspace's run count.

        Args:
            workspace_id: The workspace ID
        """
        stmt = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if workspace:
            workspace.run_count += 1
            workspace.updated_at = datetime.now(UTC)
