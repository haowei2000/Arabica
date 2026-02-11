#!/usr/bin/env python3
"""
模块名称: {模块名称}

功能描述:
    {详细描述模块的主要功能、实现逻辑和用途}


依赖模块:
    - {依赖模块1}
    - {依赖模块2}

使用示例:
    {提供简单的使用示例代码}
"""

# aiwen/services/agent/agent_template_crud.py
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.executor.agent_template import AgentTemplate


class ExecutorCRUD:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def create_template(
        self,
        template_code: str,
        template_name: str,
        config: dict = None,
        enabled: bool = True,
        version: int = 1,
        auto_commit: bool = True,
    ) -> AgentTemplate:
        """
        Create a new agent template.

        Args:
            template_code: Unique code for the template
            template_name: Name of the template
            config: Configuration dictionary
            enabled: Whether the template is enabled
            version: Version number
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            Created AgentTemplate instance
        """
        template = AgentTemplate(
            template_code=template_code,
            template_name=template_name,
            config=config or {},
            enabled=enabled,
            version=version,
        )
        self.db_session.add(template)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(template)
        return template

    async def get_template_by_code(self, template_code: str) -> AgentTemplate | None:
        result = await self.db_session.execute(
            select(AgentTemplate).where(AgentTemplate.template_code == template_code)
        )
        return result.scalars().first()

    async def get_template_by_id(self, template_id: UUID) -> AgentTemplate | None:
        result = await self.db_session.execute(
            select(AgentTemplate).where(AgentTemplate.id == template_id)
        )
        return result.scalars().first()

    async def list_templates(
        self, include_disabled: bool = True
    ) -> Sequence[AgentTemplate]:
        """
        List all agent templates.

        Args:
            include_disabled: If True, include disabled templates. If False, only return enabled ones.

        Returns:
            Sequence of AgentTemplate instances
        """
        query = select(AgentTemplate)
        if not include_disabled:
            query = query.where(AgentTemplate.enabled == True)  # noqa: E712

        result = await self.db_session.execute(query)
        return result.scalars().all()

    async def mark_template_as_deleted(
        self, template_code: str, auto_commit: bool = True
    ) -> bool:
        """
        Mark a template as deleted by setting enabled=False.

        This is a soft delete - the record remains in the database but is marked as disabled.

        Args:
            template_code: The template code to mark as deleted
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            True if the template was found and marked as deleted, False otherwise
        """
        result = await self.db_session.execute(
            select(AgentTemplate).where(AgentTemplate.template_code == template_code)
        )
        template = result.scalars().first()

        if not template:
            return False

        template.enabled = False

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return True
