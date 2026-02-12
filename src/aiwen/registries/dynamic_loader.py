"""Dynamic Tool Loader

Loads user-defined ExternalTool subclasses from the database at runtime.

Each database ``Tool`` record (tool_type="external") is transformed into a
concrete ``ExternalTool`` subclass with:
  - A ``ToolMetadata`` built from the record's name / description / etc.
  - A dynamically generated ``InputSchema`` (Pydantic model) from the
    record's ``input_schema`` JSON Schema dict.
  - ``inner_tool_name`` / ``parameter_mapping`` for single-delegation mode,
    or a list of ``ChainStep`` for pipeline mode.

The Worker calls ``DynamicToolLoader.load_user_tools()`` before each run
to obtain the user's tool classes, then injects them into the per-run
ToolProvider and ToolCaller so the executor can discover and execute them.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import Field, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.registries.base_class.base_tool import (
    ChainStep,
    ExternalTool,
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


class DynamicToolLoader:
    """Creates ``ExternalTool`` subclasses from database ``Tool`` records."""

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
    ) -> list[type[ExternalTool]]:
        """Load all enabled external tools for a user from the database.

        Args:
            db: Async database session.
            user_id: Owner user ID.
            workspace_id: Optional workspace filter.

        Returns:
            List of dynamic ``ExternalTool`` subclasses.
        """
        from aiwen.services.context.tools.tool_crud import UserToolCRUD

        crud = UserToolCRUD(db)
        tool_records = await crud.list_user_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            enabled_only=True,
            tool_type="external",
        )

        tool_classes: list[type[ExternalTool]] = []
        for record in tool_records:
            try:
                tool_cls = cls.create_tool_class(record)
                tool_classes.append(tool_cls)
                logger.debug(
                    f"Loaded external tool: {record.name} (id={record.id})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to load external tool '{record.name}': {e}",
                    exc_info=True,
                )

        logger.info(
            f"Loaded {len(tool_classes)} external tools for user {user_id}"
        )
        return tool_classes
