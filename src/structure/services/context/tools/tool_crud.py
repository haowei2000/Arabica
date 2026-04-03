"""
Tool CRUD Operations

Manages tools (both inner and external) in the unified tool table.

Name-conflict prevention
~~~~~~~~~~~~~~~~~~~~~~~~
* **Reserved names** – user tools may not share a ``name`` with any
  registered InnerTool (e.g. ``http_request``, ``code_execution``).
* **Chain-step validation** – every ``tool_name`` referenced in
  ``chain`` steps must exist in the ToolRegistry at creation time.
* **Per-user uniqueness** – enforced at the application layer (the DB
  has no composite unique constraint on ``(user_id, name)``).
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.context.tools import Tool
from structure.services.context.context_syncer import ContextSyncer

logger = logging.getLogger(__name__)

# Regex: tool names must be identifier-like (letters, digits, underscores, hyphens)
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,98}[a-zA-Z0-9]$")


class ToolCRUD:
    """CRUD operations for user tools (external tools in the unified tool table)"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # ------------------------------------------------------------------
    # Name / chain validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_reserved_names() -> set[str]:
        """Return all InnerTool names currently registered.

        These names are reserved and cannot be used for user tools.
        """
        from structure.registries.core import ToolRegistry

        return set(ToolRegistry.list_tools())

    @staticmethod
    def validate_tool_name(name: str, reserved: set[str] | None = None) -> None:
        """Validate a tool name.

        Raises:
            ValueError: On invalid format or reserved-name collision.
        """
        if not _TOOL_NAME_RE.match(name):
            raise ValueError(
                f"Tool name '{name}' is invalid. "
                "Must start with a letter, end with a letter or digit, "
                "and contain only letters, digits, underscores, or hyphens "
                "(2-100 characters)."
            )

        if reserved is None:
            reserved = ToolCRUD._get_reserved_names()

        if name in reserved:
            raise ValueError(
                f"Tool name '{name}' is reserved (conflicts with a built-in tool). "
                "Please choose a different name."
            )

    async def get_tool_by_id(
        self, tool_id: UUID, user_id: UUID | None = None
    ) -> Tool | None:
        """
        Get tool by ID

        Args:
            tool_id: Tool ID
            user_id: Optional user ID for ownership check

        Returns:
            Tool | None: Tool instance or None
        """
        query = select(Tool).where(Tool.id == tool_id)

        if user_id:
            # Only return if owned by user or is public
            query = query.where(
                (Tool.user_id == user_id) | (Tool.is_public == True)  # noqa: E712
            )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_tool_by_name(self, user_id: UUID, tool_name: str) -> Tool | None:
        """
        Get external tool by name for a specific user

        Args:
            user_id: User ID
            tool_name: Tool name

        Returns:
            Tool | None: Tool instance or None
        """
        result = await self.db.execute(
            select(Tool).where(
                Tool.user_id == user_id,
                Tool.name == tool_name,
                Tool.tool_type == "external",
            )
        )
        return result.scalar_one_or_none()

    async def list_user_tools(
        self,
        user_id: UUID,
        workspace_id: UUID | None = None,
        enabled_only: bool = True,
        include_public: bool = True,
        tool_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Tool]:
        """
        List tools for a user

        Args:
            user_id: User ID
            workspace_id: Optional workspace filter
            enabled_only: Whether to return only enabled tools
            include_public: Whether to include public tools from other users
            tool_type: Optional filter by tool_type ("inner", "external", or None for both)
            tags: Optional filter by tags (tool must contain ALL specified tags)

        Returns:
            list[Tool]: List of tools
        """
        query = select(Tool)

        if tool_type:
            query = query.where(Tool.tool_type == tool_type)

        if tool_type == "inner":
            # Inner tools are always visible to all users
            pass
        elif include_public:
            query = query.where(
                (Tool.user_id == user_id)
                | (Tool.is_public == True)  # noqa: E712
                | (Tool.tool_type == "inner")
            )
        else:
            query = query.where((Tool.user_id == user_id) | (Tool.tool_type == "inner"))

        if workspace_id:
            query = query.where(
                (Tool.workspace_id == workspace_id) | (Tool.workspace_id.is_(None))
            )

        if enabled_only:
            query = query.where(Tool.enabled == True)  # noqa: E712

        # Filter by tags (tool must contain ALL specified tags)
        if tags:
            for tag in tags:
                query = query.where(Tool.tags.contains([tag]))

        query = query.order_by(
            Tool.tool_type.asc(), Tool.last_used_at.desc().nulls_last()
        )

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_tool(
        self, tool_id: UUID, user_id: UUID, auto_commit: bool = True
    ) -> bool:
        """
        Delete an external tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            auto_commit: Whether to commit immediately

        Returns:
            bool: True if deleted, False if not found/not owner
        """
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return False

        await ContextSyncer(self.db).remove_tool(tool)
        await self.db.delete(tool)

        if auto_commit:
            await self.db.commit()

        # Invalidate dynamic tool cache so workers pick up the change.
        from structure.registries.dynamic_loader import DynamicToolLoader

        DynamicToolLoader.invalidate_cache(user_id=user_id)

        logger.info(f"Deleted user tool: {tool.name} (id={tool_id})")
        return True

    async def increment_usage(self, tool_id: UUID, auto_commit: bool = True) -> None:
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
    ) -> Tool | None:
        """
        Enable or disable a tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            enabled: New enabled status
            auto_commit: Whether to commit immediately

        Returns:
            Tool | None: Updated tool or None
        """
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return None

        tool.enabled = enabled

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(tool)

        # Invalidate dynamic tool cache so workers pick up the change.
        from structure.registries.dynamic_loader import DynamicToolLoader

        DynamicToolLoader.invalidate_cache(user_id=user_id)

        logger.info(f"Set tool {tool.name} enabled={enabled}")
        return tool
