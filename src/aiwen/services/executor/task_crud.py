# aiwen/services/agent/task_crud.py
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.agent_task import AgentTask
from aiwen.schemas.agents.input import TextInput


class AgentTaskCRUD:
    """CRUD operations for AgentTask model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize TaskCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db_session = db_session

    async def create_agent_task(
        self,
        app_id: UUID,
        user_id: UUID,
        task_type: str | None = None,
        payload: dict[str, Any] | TextInput | None = None,
        auto_commit: bool = False,
    ) -> AgentTask:
        """
        Create a new agent task.

        Args:
            app_id: Associated app identifier
            user_id: Associated user identifier
            task_type: Type of task
            payload: Task input data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created AgentTask instance
        """
        task = AgentTask(
            id=uuid4(),
            app_id=app_id,
            user_id=user_id,
            task_type=task_type,
            payload=payload,
            created_at=datetime.now(UTC),  # 保留时区信息
        )

        self.db_session.add(task)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return task

    async def get_agent_task_by_task_id(self, task_id: UUID) -> AgentTask | None:
        """
        Get task by UUID.

        Args:
            task_id: The task UUID to search for

        Returns:
            AgentTask instance or None if not found
        """
        stmt = select(AgentTask).where(AgentTask.id == task_id)
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_agent_task_status(
        self,
        task_id: UUID,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        progress: int | None = None,
        auto_commit: bool = False,
    ) -> AgentTask | None:
        """
        Update task status and related fields.

        Args:
            task_id: The task UUID to update
            status: New status
            result: Task result
            error: Error input
            progress: Progress percentage
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated AgentTask instance or None if not found
        """
        # Build update values
        values = {"status": status, "updated_at": datetime.now(UTC)}  # 保留时区信息

        if result is not None:
            values["result"] = result

        if error is not None:
            values["error"] = error

        if progress is not None:
            values["progress"] = progress

        # Set timestamps based on status
        if status == "running":
            values["started_at"] = datetime.now(UTC)  # 保留时区信息
        elif status in ["success", "failed"]:
            values["completed_at"] = datetime.now(UTC)  # 保留时区信息

        stmt = update(AgentTask).where(AgentTask.id == task_id).values(**values)
        await self.db_session.execute(stmt)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        # Return updated task
        return await self.get_agent_task_by_task_id(task_id)

    async def list_agent_tasks(
        self,
        app_id: str | None = None,
        user_id: UUID | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[AgentTask]:
        """
        List tasks with optional filtering.

        Args:
            app_id: Filter by app_id
            user_id: Filter by user_id
            status: Filter by status
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of AgentTask instances
        """
        stmt = select(AgentTask)

        if app_id:
            stmt = stmt.where(AgentTask.app_id == app_id)

        if user_id:
            stmt = stmt.where(AgentTask.user_id == user_id)

        if status:
            stmt = stmt.where(AgentTask.status == status)

        stmt = stmt.offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())

    async def delete_agent_task(self, task_id: UUID, auto_commit: bool = False) -> bool:
        """
        Delete a task by UUID.

        Args:
            task_id: The task UUID to delete
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            True if deleted, False if not found
        """
        stmt = delete(AgentTask).where(AgentTask.id == task_id)
        result = await self.db_session.execute(stmt)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return result.rowcount > 0
