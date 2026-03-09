"""Dynamic Tool Loader

Loads user-defined ExternalTool subclasses from the database at runtime.

Each database ``Tool`` record (tool_type="external") is transformed into a
concrete ``ExternalTool`` subclass with:
  - A ``ToolMetadata`` built from the record's name / description / etc.
  - A dynamically generated ``InputSchema`` (Pydantic model) from the
    record's ``input_schema`` JSON Schema dict.
  - ``inner_tool_name`` / ``parameter_mapping`` for single-delegation mode,
    or a list of ``ChainStep`` for pipeline mode.

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
from typing import Any
from uuid import UUID

from pydantic import Field, create_model
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.interfaces.tool import (
    ChainStep,
    ExternalTool,
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
)

logger = logging.getLogger(__name__)

# Mapping from JSON Schema type strings to Python types
_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_type_to_python(json_type: str | list) -> type:
    """Convert a JSON Schema ``type`` value to a Python type.

    Handles the common case of ``"type": "string"`` as well as
    nullable types like ``"type": ["string", "null"]``.
    """
    if isinstance(json_type, list):
        # e.g. ["string", "null"] → str | None
        non_null = [t for t in json_type if t != "null"]
        base = _JSON_TYPE_MAP.get(non_null[0], Any) if non_null else Any
        if "null" in json_type:
            return base | None  # type: ignore[return-value]
        return base
    return _JSON_TYPE_MAP.get(json_type, Any)


def _build_input_schema(
    json_schema: dict[str, Any] | None,
) -> type[ToolInputSchema]:
    """Build a Pydantic model from a JSON Schema dict.

    Args:
        json_schema: JSON Schema describing the tool's input parameters.
            Expected keys: ``properties``, ``required``.

    Returns:
        A dynamically created Pydantic model subclassing ``ToolInputSchema``.
    """
    if not json_schema:
        return ToolInputSchema  # No parameters

    properties = json_schema.get("properties", {})
    required_fields = set(json_schema.get("required", []))

    if not properties:
        return ToolInputSchema

    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        field_type = _json_type_to_python(prop.get("type", "string"))
        description = prop.get("description", "")
        default = prop.get("default", ...)

        if name in required_fields:
            # Required field — no default
            fields[name] = (field_type, Field(description=description))
        else:
            # Optional field
            if default is ...:
                default = None
                field_type = field_type | None  # type: ignore[assignment]
            fields[name] = (
                field_type,
                Field(default=default, description=description),
            )

    return create_model(
        "DynamicInputSchema",
        __base__=ToolInputSchema,
        **fields,
    )


# ---------------------------------------------------------------------------
# Cache structures
# ---------------------------------------------------------------------------

_CacheKey = tuple[UUID, UUID | None]  # (user_id, workspace_id)

# tool_types handled by DynamicToolLoader
_DYNAMIC_TOOL_TYPES = ("external", "mcp")


@dataclass(slots=True)
class _CacheEntry:
    """Cached tool classes with the DB fingerprint that produced them."""

    fingerprint: tuple[int, datetime | None]  # (count, max_updated_at)
    tool_classes: list[type[ExternalTool] | type[InnerTool]]


class DynamicToolLoader:
    """Creates ``ExternalTool`` subclasses from database ``Tool`` records.

    Maintains an in-memory cache keyed by ``(user_id, workspace_id)``.
    Before doing a full load, a lightweight fingerprint query checks
    whether the cache is still valid.
    """

    # Class-level cache shared across calls (Worker is single-threaded).
    _cache: dict[_CacheKey, _CacheEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def create_tool_class(tool_record) -> type[ExternalTool]:
        """Create a dynamic ``ExternalTool`` subclass from a DB record.

        Args:
            tool_record: A ``Tool`` ORM instance (tool_type="external").

        Returns:
            A concrete ``ExternalTool`` subclass ready for instantiation.
        """
        # Build InputSchema from JSON Schema
        input_schema_cls = _build_input_schema(tool_record.input_schema)

        # Build chain steps (pipeline mode)
        chain_steps: list[ChainStep] = []
        if tool_record.chain:
            for step_dict in tool_record.chain:
                chain_steps.append(
                    ChainStep(
                        tool_name=step_dict["tool_name"],
                        parameter_mapping=step_dict.get("parameter_mapping", {}),
                        extra_params=step_dict.get("extra_params", {}),
                    )
                )

        # Sanitize class name (Python identifiers only)
        safe_name = "".join(
            c if c.isalnum() or c == "_" else "_" for c in tool_record.name
        )

        # Create the dynamic subclass
        cls = type(
            f"DynExt_{safe_name}",
            (ExternalTool,),
            {
                "METADATA": ToolMetadata(
                    name=tool_record.name,
                    display_name=tool_record.display_name or tool_record.name,
                    description=tool_record.description or "",
                    category=tool_record.category or "custom",
                    tags=tool_record.tags or [],
                    timeout=tool_record.timeout or 30,
                ),
                "InputSchema": input_schema_cls,
                "inner_tool_name": tool_record.inner_tool_name or "",
                "parameter_mapping": tool_record.parameter_mapping or {},
                "chain": chain_steps,
            },
        )
        return cls

    @classmethod
    async def load_user_tools(
        cls,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID | None = None,
    ) -> list[type[ExternalTool] | type[InnerTool]]:
        """Load all enabled external tools for a user from the database.

        Uses a lightweight fingerprint (COUNT + MAX(updated_at)) to skip
        the full load when the user's tools have not changed since the
        last invocation.

        Args:
            db: Async database session.
            user_id: Owner user ID.
            workspace_id: Optional workspace filter.

        Returns:
            List of dynamic ``ExternalTool`` subclasses.
        """
        cache_key: _CacheKey = (user_id, workspace_id)

        # ── Fast-path: check fingerprint ──────────────────────────
        fingerprint = await cls._query_fingerprint(db, user_id, workspace_id)
        cached = cls._cache.get(cache_key)
        if cached is not None and cached.fingerprint == fingerprint:
            logger.debug(
                "Tool cache hit for user %s (count=%d)",
                user_id,
                fingerprint[0],
            )
            return cached.tool_classes

        # ── Slow-path: full load + rebuild ────────────────────────
        from aiwen.registries.core import ToolRegistry
        from aiwen.services.context.tools.tool_crud import ToolCRUD

        crud = ToolCRUD(db)
        # Load both external (user-defined delegations) and mcp (imported from MCP servers).
        tool_records = await crud.list_user_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            enabled_only=True,
            tool_type="external",
        )
        mcp_records = await crud.list_user_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            enabled_only=True,
            tool_type="mcp",
        )
        tool_records = list(tool_records) + list(mcp_records)

        # Collect InnerTool names for collision detection
        inner_names: set[str] = set(ToolRegistry.list_tools())

        # Deduplicate: user's own tools win over public tools with
        # the same name.  Tools that collide with InnerTool names are
        # skipped entirely (should not happen if CRUD validation is
        # in place, but acts as a safety net).
        seen_names: dict[str, UUID] = {}  # name → owner user_id
        tool_classes: list[type[ExternalTool]] = []

        for record in tool_records:
            name = record.name

            # Safety net: skip external tools that shadow InnerTools
            if name in inner_names:
                logger.warning(
                    "External tool '%s' (id=%s) shadows a built-in tool — skipped",
                    name,
                    record.id,
                )
                continue

            # Dedup: user's own tool takes priority over public tools
            if name in seen_names:
                prev_owner = seen_names[name]
                if prev_owner == user_id:
                    # Already loaded the user's own tool — skip duplicate
                    continue
                if record.user_id == user_id:
                    # Current record is the user's own — replace public
                    tool_classes = [
                        tc for tc in tool_classes if tc.METADATA.name != name
                    ]
                    logger.debug(
                        "User tool '%s' overrides public tool from user %s",
                        name,
                        prev_owner,
                    )
                else:
                    # Both are public from different users — keep first
                    continue

            try:
                if record.tool_type == "mcp":
                    from aiwen.registries.mcp_loader import build_mcp_tool_class
                    tool_cls = build_mcp_tool_class(record)
                else:
                    tool_cls = cls.create_tool_class(record)
                tool_classes.append(tool_cls)
                seen_names[name] = record.user_id
                logger.debug(
                    "Loaded %s tool: %s (id=%s)", record.tool_type, name, record.id,
                )
            except Exception as e:
                logger.error(
                    "Failed to load %s tool '%s': %s",
                    record.tool_type,
                    name,
                    e,
                    exc_info=True,
                )

        # Update cache
        cls._cache[cache_key] = _CacheEntry(
            fingerprint=fingerprint,
            tool_classes=tool_classes,
        )
        logger.info(
            "Loaded %d external tools for user %s (cache refreshed)",
            len(tool_classes),
            user_id,
        )
        return tool_classes

    @classmethod
    def invalidate_cache(
        cls,
        user_id: UUID | None = None,
        workspace_id: UUID | None = None,
    ) -> None:
        """Explicitly invalidate the cache.

        Args:
            user_id: Invalidate entries for this user only.
                If ``None``, the entire cache is cleared.
            workspace_id: Further narrow to a specific workspace.
        """
        if user_id is None:
            cls._cache.clear()
            return
        keys_to_remove = [
            k for k in cls._cache
            if k[0] == user_id and (workspace_id is None or k[1] == workspace_id)
        ]
        for k in keys_to_remove:
            del cls._cache[k]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _query_fingerprint(
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[int, datetime | None]:
        """Run a lightweight aggregate to detect tool changes.

        Returns:
            ``(count, max_updated_at)`` for the user's enabled external tools.
        """
        from aiwen.models.context.tools import Tool as ToolModel

        query = select(
            func.count(ToolModel.id),
            func.max(ToolModel.updated_at),
        ).where(
            ToolModel.tool_type.in_(list(_DYNAMIC_TOOL_TYPES)),
            ToolModel.enabled == True,  # noqa: E712
            (ToolModel.user_id == user_id) | (ToolModel.is_public == True),  # noqa: E712
        )

        if workspace_id is not None:
            query = query.where(
                (ToolModel.workspace_id == workspace_id)
                | ToolModel.workspace_id.is_(None)
            )

        result = await db.execute(query)
        row = result.one()
        return (row[0] or 0, row[1])
