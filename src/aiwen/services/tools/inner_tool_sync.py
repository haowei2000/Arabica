"""
InnerTool Database Sync

Syncs code-defined InnerTool classes to the unified tool table on startup.
Each InnerTool gets a row with tool_type='inner', making built-in tools
discoverable via the same API as user-defined external tools.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.tool import Tool
from aiwen.services.tools.base_tool import InnerTool, ToolMetadata

logger = logging.getLogger(__name__)


def _collect_all_inner_tools() -> list[type[InnerTool]]:
    """Import and collect all InnerTool classes from known modules."""
    all_tools: list[type[InnerTool]] = []

    try:
        from aiwen.services.tools.inner_tool.server_tools import (
            INNER_TOOLS,
            SERVER_TOOLS,
        )
        all_tools.extend(SERVER_TOOLS)
        all_tools.extend(INNER_TOOLS)
    except ImportError:
        logger.warning("Could not import server_tools")

    try:
        from aiwen.services.tools.inner_tool.browser_tools import (
            BROWSER_TOOLS,
        )
        all_tools.extend(BROWSER_TOOLS)
    except ImportError:
        logger.warning("Could not import browser_tools (playwright may not be installed)")

    return all_tools


def _extract_input_schema(tool_cls: type[InnerTool]) -> dict[str, Any]:
    """Extract the JSON Schema dict from an InnerTool's InputSchema."""
    schema = tool_cls.InputSchema.model_json_schema()
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }


async def sync_inner_tools_to_db(db: AsyncSession) -> int:
    """
    Upsert all InnerTool classes into the tool table with tool_type='inner'.

    For each InnerTool:
    - If a row exists (by name + tool_type='inner'), update its metadata
    - Otherwise, create a new row

    Args:
        db: Async database session

    Returns:
        Number of tools synced
    """
    tool_classes = _collect_all_inner_tools()
    synced = 0

    for tool_cls in tool_classes:
        meta: ToolMetadata = tool_cls.METADATA
        input_schema = _extract_input_schema(tool_cls)

        # Check if already exists
        result = await db.execute(
            select(Tool).where(
                Tool.name == meta.name,
                Tool.tool_type == "inner",
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update metadata
            existing.display_name = meta.display_name
            existing.description = meta.description
            existing.execution_mode = meta.execution_mode.value
            existing.category = meta.category
            existing.tags = meta.tags
            existing.timeout = meta.timeout
            existing.enabled = meta.enabled
            existing.input_schema = input_schema
        else:
            # Create new record
            tool = Tool(
                name=meta.name,
                tool_code=f"inner_{meta.name}",
                display_name=meta.display_name,
                description=meta.description,
                tool_type="inner",
                execution_mode=meta.execution_mode.value,
                category=meta.category,
                tags=meta.tags,
                timeout=meta.timeout,
                enabled=meta.enabled,
                input_schema=input_schema,
                user_id=None,
                is_public=True,
                verified=True,
            )
            db.add(tool)

        synced += 1

    await db.commit()
    return synced
