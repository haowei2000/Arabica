#!/usr/bin/env python3
"""
Executor CRUD operations.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.executor.executor import ExecutorTemplate


class ExecutorCRUD:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def create_executor(
        self,
        executor_code: str,
        executor_name: str,
        config: dict = None,  # ty:ignore[invalid-parameter-default]  # noqa: RUF013
        enabled: bool = True,
        version: int = 1,
        auto_commit: bool = True,
    ) -> ExecutorTemplate:
        """
        Create a new executor.

        Args:
            executor_code: Unique code for the executor
            executor_name: Name of the executor
            config: Configuration dictionary
            enabled: Whether the executor is enabled
            version: Version number
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            Created ExecutorTemplate instance
        """
        executor = ExecutorTemplate(
            executor_code=executor_code,
            executor_name=executor_name,
            config=config or {},
            enabled=enabled,
            version=version,
        )
        self.db_session.add(executor)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(executor)
        return executor

    async def get_executor_by_code(self, executor_code: str) -> ExecutorTemplate | None:
        result = await self.db_session.execute(
            select(ExecutorTemplate).where(ExecutorTemplate.executor_code == executor_code)
        )
        return result.scalars().first()

    async def get_executor_by_id(self, executor_id: UUID) -> ExecutorTemplate | None:
        result = await self.db_session.execute(
            select(ExecutorTemplate).where(ExecutorTemplate.id == executor_id)
        )
        return result.scalars().first()

    async def list_executors(
        self, include_disabled: bool = True
    ) -> Sequence[ExecutorTemplate]:
        """
        List all executors.

        Args:
            include_disabled: If True, include disabled executors. If False, only return enabled ones.

        Returns:
            Sequence of ExecutorTemplate instances
        """
        query = select(ExecutorTemplate)
        if not include_disabled:
            query = query.where(ExecutorTemplate.enabled == True)  # noqa: E712

        result = await self.db_session.execute(query)
        return result.scalars().all()

    async def mark_executor_as_deleted(
        self, executor_code: str, auto_commit: bool = True
    ) -> bool:
        """
        Mark an executor as deleted by setting enabled=False.

        This is a soft delete - the record remains in the database but is marked as disabled.

        Args:
            executor_code: The executor code to mark as deleted
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            True if the executor was found and marked as deleted, False otherwise
        """
        result = await self.db_session.execute(
            select(ExecutorTemplate).where(ExecutorTemplate.executor_code == executor_code)
        )
        executor = result.scalars().first()

        if not executor:
            return False

        executor.enabled = False

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return True
