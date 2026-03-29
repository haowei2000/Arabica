"""Dynamic Tool Loader

Loads MCP tool subclasses from the database at runtime.

Each database ``Tool`` record (tool_type="mcp") is transformed into a
concrete ``InnerTool`` subclass via ``build_mcp_tool_class`` that calls
the remote MCP server on execution.

**Caching strategy**

A full load (SELECT * → dynamic class creation) is expensive.  On each
call to ``load_user_tools()`` we first run a lightweight *fingerprint*
query — ``SELECT COUNT(*), MAX(updated_at)`` — and compare it against
the cached fingerprint.  If the fingerprint matches the cache is returned
immediately, skipping the full load entirely.

Cache entries are keyed by ``(user_id, workspace_id)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.interfaces.tool import InnerTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache structures
# ---------------------------------------------------------------------------

_CacheKey = tuple[UUID, UUID | None]  # (user_id, workspace_id)


@dataclass(slots=True)
class _CacheEntry:
    """Cached tool classes with the DB fingerprint that produced them."""

    fingerprint: tuple[int, datetime | None]  # (count, max_updated_at)
    tool_classes: list[type[InnerTool]]


class DynamicToolLoader:
    """Creates MCP ``InnerTool`` subclasses from database ``Tool`` records.

    Maintains an in-memory cache keyed by ``(user_id, workspace_id)``.
    Before doing a full load, a lightweight fingerprint query checks
    whether the cache is still valid.
    """

    # Class-level cache shared across calls (Worker is single-threaded).
    _cache: dict[_CacheKey, _CacheEntry] = {}

    @classmethod
    async def load_user_tools(
        cls,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID | None = None,
    ) -> list[type[InnerTool]]:
        """Load all enabled MCP tools for a user from the database.

        Uses a lightweight fingerprint (COUNT + MAX(updated_at)) to skip
        the full load when the user's tools have not changed.

        Args:
            db: Async database session.
            user_id: Owner user ID.
            workspace_id: Optional workspace filter.

        Returns:
            List of dynamic MCP ``InnerTool`` subclasses.
        """
        cache_key: _CacheKey = (user_id, workspace_id)

        fingerprint = await cls._query_fingerprint(db, user_id, workspace_id)
        cached = cls._cache.get(cache_key)
        if cached is not None and cached.fingerprint == fingerprint:
            logger.debug("Tool cache hit for user %s (count=%d)", user_id, fingerprint[0])
            return cached.tool_classes

        from structure.registries.core import ToolRegistry
        from structure.services.context.tools.tool_crud import ToolCRUD
        from structure.registries.mcp_loader import build_mcp_tool_class

        crud = ToolCRUD(db)
        mcp_records = await crud.list_user_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            enabled_only=True,
            tool_type="mcp",
        )

        inner_names: set[str] = set(ToolRegistry.list_tools())
        seen_names: dict[str, UUID] = {}
        tool_classes: list[type[InnerTool]] = []

        for record in mcp_records:
            name = record.name

            if name in inner_names:
                logger.warning(
                    "MCP tool '%s' (id=%s) shadows a built-in tool — skipped",
                    name, record.id,
                )
                continue

            if name in seen_names:
                prev_owner = seen_names[name]
                if prev_owner == user_id:
                    continue
                if record.user_id == user_id:
                    tool_classes = [tc for tc in tool_classes if tc.METADATA.name != name]
                else:
                    continue

            try:
                tool_cls = build_mcp_tool_class(record)
                tool_classes.append(tool_cls)
                seen_names[name] = record.user_id
                logger.debug("Loaded MCP tool: %s (id=%s)", name, record.id)
            except Exception as e:
                logger.error("Failed to load MCP tool '%s': %s", name, e, exc_info=True)

        cls._cache[cache_key] = _CacheEntry(fingerprint=fingerprint, tool_classes=tool_classes)
        logger.info("Loaded %d MCP tools for user %s (cache refreshed)", len(tool_classes), user_id)
        return tool_classes

    @classmethod
    def invalidate_cache(
        cls,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
    ) -> None:
        """Explicitly invalidate the cache."""
        if user_id is None:
            cls._cache.clear()
            return
        keys_to_remove = [
            k for k in cls._cache
            if k[0] == user_id and (workspace_id is None or k[1] == workspace_id)
        ]
        for k in keys_to_remove:
            del cls._cache[k]

    @staticmethod
    async def _query_fingerprint(
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[int, datetime | None]:
        """Run a lightweight aggregate to detect tool changes."""
        from structure.models.context.tools import Tool as ToolModel

        query = select(
            func.count(ToolModel.id),
            func.max(ToolModel.updated_at),
        ).where(
            ToolModel.tool_type == "mcp",
            ToolModel.enabled == True,  # noqa: E712
            (ToolModel.user_id == user_id) | (ToolModel.is_public == True),  # noqa: E712
        )

        if workspace_id is not None:
            query = query.where(
                (ToolModel.workspace_id == workspace_id) | ToolModel.workspace_id.is_(None)
            )

        result = await db.execute(query)
        row = result.one()
        return (row[0] or 0, row[1])
