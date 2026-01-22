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

# aiwen/services/agents/agent_template_crud.py
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.agent_template import AgentTemplate


class AgentTemplateCRUD:
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

    async def list_templates(self) -> Sequence[AgentTemplate]:
        result = await self.db_session.execute(select(AgentTemplate))
        return result.scalars().all()
