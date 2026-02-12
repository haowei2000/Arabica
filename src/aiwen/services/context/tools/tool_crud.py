"""
User Tool CRUD Operations

Manages user-defined custom tools (external tools) in the unified tool table.

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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.tools import Tool
from aiwen.schemas.context.tools.user_tool import UserToolCreate, UserToolUpdate

logger = logging.getLogger(__name__)

# Regex: tool names must be identifier-like (letters, digits, underscores, hyphens)
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,98}[a-zA-Z0-9]$")


class UserToolCRUD:
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
        from aiwen.registries.core import ToolRegistry

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
            reserved = UserToolCRUD._get_reserved_names()

        if name in reserved:
            raise ValueError(
                f"Tool name '{name}' is reserved (conflicts with a built-in tool). "
                "Please choose a different name."
            )

    @staticmethod
    def validate_chain_steps(
        chain: list[dict[str, Any]] | None,
        reserved: set[str] | None = None,
    ) -> None:
        """Validate that every chain step references an existing tool.

        Raises:
            ValueError: If a step references an unknown tool name.
        """
        if not chain:
            return

        if reserved is None:
            reserved = UserToolCRUD._get_reserved_names()

        for idx, step in enumerate(chain):
            step_tool = step.get("tool_name")
            if not step_tool:
                raise ValueError(
                    f"Chain step {idx} is missing 'tool_name'."
                )
            if step_tool not in reserved:
                raise ValueError(
                    f"Chain step {idx} references unknown tool '{step_tool}'. "
                    "Only registered built-in tools can be used in chain steps."
                )

    async def create_tool(
        self, user_id: UUID, tool_data: UserToolCreate, auto_commit: bool = True
    ) -> Tool:
        """
        Create a new external tool

        Args:
            user_id: Owner user ID
            tool_data: Tool creation data
            auto_commit: Whether to commit immediately

        Returns:
            Tool: Created tool instance

        Raises:
            ValueError: If tool name already exists, is reserved, or chain
                steps reference unknown tools.
        """
        # ── Name validation ──────────────────────────────────────
        reserved = self._get_reserved_names()
        self.validate_tool_name(tool_data.name, reserved)
        self.validate_chain_steps(tool_data.chain, reserved)

        # Check if tool name already exists for this user (external only)
        existing = await self.get_tool_by_name(user_id, tool_data.name)
        if existing:
            raise ValueError(
                f"Tool with name '{tool_data.name}' already exists for this user"
            )

        tool = Tool(
            user_id=user_id,
            tool_type="external",
            tool_code=f"ext_{tool_data.name}_{uuid4().hex[:8]}",
            workspace_id=tool_data.workspace_id,
            name=tool_data.name,
            display_name=tool_data.display_name,
            description=tool_data.description,
            inner_tool_name=tool_data.inner_tool_name,
            parameter_mapping=tool_data.parameter_mapping,
            chain=tool_data.chain,
            input_schema=tool_data.input_schema,
            output_schema=tool_data.output_schema,
            code=tool_data.code,
            http_config=tool_data.http_config,
            container_config=tool_data.container_config,
            client_config=tool_data.client_config,
            celery_config=tool_data.celery_config,
            category=tool_data.category,
            tags=tool_data.tags,
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
    ) -> list[Tool]:
        """
        List tools for a user

        Args:
            user_id: User ID
            workspace_id: Optional workspace filter
            enabled_only: Whether to return only enabled tools
            include_public: Whether to include public tools from other users
            tool_type: Optional filter by tool_type ("inner", "external", or None for both)

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

        query = query.order_by(
            Tool.tool_type.asc(), Tool.last_used_at.desc().nulls_last()
        )

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_tool(
        self,
        tool_id: UUID,
        user_id: UUID,
        tool_data: UserToolUpdate,
        auto_commit: bool = True,
    ) -> Tool | None:
        """
        Update an external tool

        Args:
            tool_id: Tool ID
            user_id: User ID (for ownership check)
            tool_data: Update data
            auto_commit: Whether to commit immediately

        Returns:
            Tool | None: Updated tool or None if not found/not owner

        Raises:
            ValueError: If updated chain steps reference unknown tools.
        """
        tool = await self.get_tool_by_id(tool_id, user_id)
        if not tool or tool.user_id != user_id:
            return None

        update_data = tool_data.model_dump(exclude_unset=True)

        # Validate chain steps if they are being updated
        if "chain" in update_data and update_data["chain"] is not None:
            self.validate_chain_steps(update_data["chain"])

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

        await self.db.delete(tool)

        if auto_commit:
            await self.db.commit()

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

        logger.info(f"Set tool {tool.name} enabled={enabled}")
        return tool
