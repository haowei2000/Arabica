# aiwen/services/agent/tool_crud.py
"""CRUD operations for Tool registry model."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.tools import Tool
from aiwen.schemas.context.tools.tool import ToolCreate, ToolUpdate


class ToolCRUD:
    """CRUD operations for Tool model."""

    def __init__(self, db_session: AsyncSession):
        """Initialize ToolCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db_session = db_session

    async def create_tool(
        self, data: ToolCreate, user_id: UUID | None = None, auto_commit: bool = False
    ) -> Tool:
        """Create a new tool.

        Args:
            data: Tool creation data
            user_id: ID of the user creating the tool
            auto_commit: If True, immediately commit the transaction

        Returns:
            Created Tool instance
        """
        tool = Tool(
            id=uuid4(),
            name=data.name,
            tool_code=data.tool_code,
            description=data.description,
            tool_type=data.tool_type,
            input_schema=data.input_schema,
            config=data.config,
            user_id=str(user_id) if user_id else None,
            enabled=data.enabled,
            is_public=data.is_public,
            version=data.version,
            created_at=datetime.now(UTC),
        )

        self.db_session.add(tool)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(tool)
        return tool

    async def get_tool(self, tool_id: UUID) -> Tool | None:
        """Get a tool by its UUID.

        Args:
            tool_id: UUID of the tool

        Returns:
            Tool instance if found, None otherwise
        """
        result = await self.db_session.execute(select(Tool).where(Tool.id == tool_id))
        return result.scalar_one_or_none()

    async def get_tool_by_code(self, tool_code: str) -> Tool | None:
        """Get a tool by its unique code.

        Args:
            tool_code: Unique code of the tool

        Returns:
            Tool instance if found, None otherwise
        """
        result = await self.db_session.execute(
            select(Tool).where(Tool.tool_code == tool_code)
        )
        return result.scalar_one_or_none()

    async def list_tools(
        self,
        user_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
        enabled_only: bool = False,
        include_public: bool = True,
    ) -> tuple[list[Tool], int]:
        """List tools with pagination.

        Args:
            user_id: If provided, return tools owned by this user (+ public)
            skip: Number of records to skip
            limit: Maximum number of records to return
            enabled_only: If True, only return enabled tools
            include_public: If True, include public tools alongside user's own

        Returns:
            Tuple of (list of tools, total count)
        """
        conditions = []

        if enabled_only:
            conditions.append(Tool.enabled == True)  # noqa: E712

        if user_id:
            if include_public:
                conditions.append(
                    or_(
                        Tool.user_id == user_id,
                        Tool.is_public == True,  # noqa: E712
                    )
                )
            else:
                conditions.append(Tool.user_id == user_id)

        # Count query
        count_stmt = select(func.count(Tool.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = select(Tool)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Tool.created_at.desc()).offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update_tool(
        self,
        tool_id: UUID,
        data: ToolUpdate,
        auto_commit: bool = False,
    ) -> Tool | None:
        """Update an existing tool.

        Args:
            tool_id: UUID of the tool to update
            data: Update data
            auto_commit: If True, immediately commit the transaction

        Returns:
            Updated Tool instance or None if not found
        """
        tool = await self.get_tool(tool_id)
        if not tool:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(tool, key, value)

        tool.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(tool)
        return tool

    async def delete_tool(self, tool_id: UUID, auto_commit: bool = False) -> bool:
        """Delete a tool.

        Args:
            tool_id: UUID of the tool to delete
            auto_commit: If True, immediately commit the transaction

        Returns:
            True if deleted, False if not found
        """
        tool = await self.get_tool(tool_id)
        if not tool:
            return False

        await self.db_session.delete(tool)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return True

    async def list_tools_by_type(
        self,
        tool_type: str,
        user_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Tool], int]:
        """List tools filtered by execution type.

        Args:
            tool_type: Tool execution type (server/sandbox/client/async)
            user_id: If provided, filter by user
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of tools, total count)
        """
        conditions = [
            Tool.tool_type == tool_type,
            Tool.enabled == True,  # noqa: E712
        ]

        if user_id:
            conditions.append(
                or_(
                    Tool.user_id == user_id,
                    Tool.is_public == True,  # noqa: E712
                )
            )

        count_stmt = select(func.count(Tool.id)).where(and_(*conditions))
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = (
            select(Tool)
            .where(and_(*conditions))
            .order_by(Tool.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_tools_by_ids(self, tool_ids: list[UUID]) -> list[Tool]:
        """Get multiple tools by their UUIDs.

        Args:
            tool_ids: List of tool UUIDs

        Returns:
            List of Tool instances found
        """
        if not tool_ids:
            return []

        stmt = select(Tool).where(Tool.id.in_(tool_ids)).order_by(Tool.name)
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())
