"""CRUD operations for Task model."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.runs.task import Task


class TaskCRUD:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_workspace(
        self,
        workspace_id: str | UUID,
        run_id: str | UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[Task]]:
        """List tasks in a workspace with optional filters."""
        stmt = select(Task).where(
            Task.workspace_id == str(workspace_id)
        )
        if run_id is not None:
            stmt = stmt.where(Task.run_id == str(run_id))
        if status is not None:
            stmt = stmt.where(Task.status == status)

        count_stmt = select(Task).where(
            Task.workspace_id == str(workspace_id)
        )
        if run_id is not None:
            count_stmt = count_stmt.where(Task.run_id == str(run_id))
        if status is not None:
            count_stmt = count_stmt.where(Task.status == status)

        total_result = await self.db.execute(count_stmt)
        total = len(total_result.scalars().all())

        stmt = stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return total, list(result.scalars().all())
