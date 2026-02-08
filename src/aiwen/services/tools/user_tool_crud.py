"""
User Tool CRUD Operations

Manages user-defined custom tools in the database.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.tools.user_tool import UserTool
from aiwen.schemas.tools.user_tool import UserToolCreate, UserToolUpdate

logger = logging.getLogger(__name__)


class UserToolCRUD:
    """CRUD operations for user tools"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create_tool(
        self, user_id: UUID, tool_data: UserToolCreate, auto_commit: bool = True
    ) -> UserTool:
        """
        Create a new user tool

        Args:
            user_id: Owner user ID
            tool_data: Tool creation data
            auto_commit: Whether to commit immediately

        Returns:
            UserTool: Created tool instance

        Raises:
            ValueError: If tool name already exists for this user
        """
        # Check if tool name already exists for this user
        existing = await self.get_tool_by_name(user_id, tool_data.name)
        if existing:
            raise ValueError(
                f"Tool with name '{tool_data.name}' already exists for this user"
            )

        # Create tool
        tool = UserTool(
            user_id=user_id,
            workspace_id=tool_data.workspace_id,
            name=tool_data.name,
            display_name=tool_data.display_name,
            description=tool_data.description,
            execution_mode=tool_data.execution_mode,
            input_schema=tool_data.input_schema,
            output_schema=tool_data.output_schema,
            code=tool_data.code,
            http_config=tool_data.http_config,
            container_config=tool_data.container_config,
            client_config=tool_data.client_config,
            celery_config=tool_data.celery_config,
            category=tool_data.category,
            tags=tool_data.tags,
            version=tool_data.version,
            timeout=tool_data.timeout,
            enabled=tool_data.enabled,
            is_public=tool_data.is_public,
        )

        self.db.add(tool)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(tool)
        else:
            await self.db.flush()

        logger.info(f"Created user tool: {tool.name} (id={tool.id}, user={user_id})")
        return tool

    async def get_tool_by_id(
        self, tool_id: UUID, user_id: UUID | None = None
    ) -> UserTool | None:
        """
        Get tool by ID

        Args:
            tool_id: Tool ID
            user_id: Optional user ID for ownership check

        Returns:
            UserTool | None: Tool instance or None
        """
        query = select(UserTool).where(UserTool.id == tool_id)

        if user_id:
            # Only return if owned by user or is public
            query = query.where(
                (UserTool.user_id == user_id) | (UserTool.is_public == True)  # noqa: E712
            )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_tool_by_name(
        self, user_id: UUID, tool_name: str
    ) -> UserTool | None:
        """
        Get tool by name for a specific user

        Args:
            user_id: User ID
            tool_name: Tool name

        Returns:
            UserTool | None: Tool instance or None
        """
        result = await self.db.execute(
            select(UserTool).where(
                UserTool.user_id == user_id, UserTool.name == tool_name
            )
        )
        return result.scalar_one_or_none()

    async def list_user_tools(
        self,
        user_id: UUID,
        workspace_id: UUID | None = None,
        enabled_only: bool = True,
        include_public: bool = True,
    ) -> list[UserTool]:
        """
        List tools for a user

        Args:
            user_id: User ID
            workspace_id: Optional workspace filter
            enabled_only: Whether to return only enabled tools
            include_public: Whether to include public tools from other users

        Returns:
            list[UserTool]: List of tools
        """
        # Build query
        query = select(UserTool)

        if include_public:
            # User's own tools OR public tools
            query = query.where(
                (UserTool.user_id == user_id) | (UserTool.is_public == True)  # noqa: E712
            )
        else:
            # Only user's own tools
            query = query.where(UserTool.user_id == user_id)

        if workspace_id:
            query = query.where(UserTool.workspace_id == workspace_id)

        if enabled_only:
            query = query.where(UserTool.enabled == True)  # noqa: E712

        # Order by most recently used first
        query = query.order_by(UserTool.last_used_at.desc().nulls_last())

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_tool(
        self,
        tool_id: UUID,
        user_id: UUID,
        tool_data: UserToolUpdate,
        auto_commit: bool = True,
    ) -> UserTool | None:
        """
        Update a tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            tool_data: Update data
            auto_commit: Whether to commit immediately

        Returns:
            UserTool | None: Updated tool or None if not found/not owner
        """
        # Get tool and verify ownership
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return None

        # Update fields
        update_data = tool_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(tool, field, value)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(tool)
        else:
            await self.db.flush()

        logger.info(f"Updated user tool: {tool.name} (id={tool_id})")
        return tool

    async def delete_tool(
        self, tool_id: UUID, user_id: UUID, auto_commit: bool = True
    ) -> bool:
        """
        Delete a tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            auto_commit: Whether to commit immediately

        Returns:
            bool: True if deleted, False if not found/not owner
        """
        # Get tool and verify ownership
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return False

        await self.db.delete(tool)

        if auto_commit:
            await self.db.commit()

        logger.info(f"Deleted user tool: {tool.name} (id={tool_id})")
        return True

    async def increment_usage(
        self, tool_id: UUID, auto_commit: bool = True
    ) -> None:
        """
        Increment tool usage count and update last_used_at

        Args:
            tool_id: Tool ID
            auto_commit: Whether to commit immediately
        """
        from datetime import UTC, datetime

        tool = await self.get_tool_by_id(tool_id)
        if tool:
            tool.usage_count += 1
            tool.last_used_at = datetime.now(UTC)

            if auto_commit:
                await self.db.commit()
            else:
                await self.db.flush()

    async def toggle_enabled(
        self, tool_id: UUID, user_id: UUID, enabled: bool, auto_commit: bool = True
    ) -> UserTool | None:
        """
        Enable or disable a tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            enabled: New enabled status
            auto_commit: Whether to commit immediately

        Returns:
            UserTool | None: Updated tool or None
        """
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return None

        tool.enabled = enabled

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(tool)

        logger.info(f"Set tool {tool.name} enabled={enabled}")
        return tool
