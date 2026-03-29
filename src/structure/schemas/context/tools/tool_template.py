"""
Tool Templates

Templates for creating ExternalTools via the API.
Each template provides a complete UserToolCreate body that users can
customize with their own parameters and mapping logic.

All custom tools use mapping-based delegation to registered InnerTools.
Templates are auto-generated from registered InnerTools via ``to_template()``.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolTemplate(BaseModel):
    """A pre-built tool creation template"""

    id: str = Field(..., description="Unique template identifier")
    name: str = Field(..., description="Template display name")
    description: str = Field(..., description="What this template does")
    execution_mode: str = Field(
        default="inner",
        description="Tool execution mode (mapping-based delegation)",
    )
    inner_tool_name: str | None = Field(
        None, description="InnerTool this template delegates to (for dynamic templates)"
    )
    category: str = Field(default="custom", description="Template category")
    tags: list[str] = Field(default_factory=list, description="Template tags")
    source: str = Field(
        default="static",
        description="Template source: 'static' (curated) or 'inner_tool' (auto-generated) or 'user_tool'",
    )
    template: dict[str, Any] = Field(
        ..., description="Pre-filled UserToolCreate body (ready to POST)"
    )


class ToolTemplateListResponse(BaseModel):
    """Response for listing available templates"""

    templates: list[ToolTemplate]
    total: int


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

# Static templates — all custom tools now use mapping-based delegation,
# so only dynamic (inner-tool-derived) templates are relevant.
TOOL_TEMPLATES: dict[str, ToolTemplate] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC TEMPLATES (from registered InnerTools)
# ═══════════════════════════════════════════════════════════════════════════════


def get_inner_tool_templates() -> dict[str, ToolTemplate]:
    """
    Generate ToolTemplate instances from all registered InnerTools.

    Returns:
        dict[str, ToolTemplate]: Dynamic templates keyed by template ID
    """
    from structure.core.interfaces.tool import InnerTool
    from structure.registries.core import ToolRegistry

    templates: dict[str, ToolTemplate] = {}

    for tool_name in ToolRegistry.list_tools(enabled_only=False):
        tool_class = ToolRegistry.get_tool_class(tool_name)
        if tool_class is None:
            continue

        # Only generate templates from InnerTool subclasses
        if not (issubclass(tool_class, InnerTool) and tool_class is not InnerTool):
            continue

        try:
            raw = tool_class.to_template()
            template = ToolTemplate(
                id=raw["id"],
                name=raw["name"],
                description=raw["description"],
                execution_mode="inner",
                inner_tool_name=raw.get("inner_tool_name"),
                category=raw.get("category", "general"),
                tags=raw.get("tags", []),
                source="inner_tool",
                template=raw["template"],
            )
            templates[template.id] = template
        except Exception:
            logger.warning(
                f"Failed to generate template from InnerTool '{tool_name}'",
                exc_info=True,
            )

    return templates


def tool_record_to_template(tool: Any) -> ToolTemplate:
    """Convert a Tool database record to a ToolTemplate.

    All custom tools use mapping-based delegation to an InnerTool.

    Args:
        tool: Tool SQLAlchemy model instance.

    Returns:
        ToolTemplate with pre-filled creation body derived from the record.
    """
    source = "inner" if getattr(tool, "tool_type", "") == "inner" else "user_tool"

    template_body: dict[str, Any] = {
        "name": f"copy_of_{tool.name}",
        "display_name": f"Copy of {tool.display_name or tool.name}",
        "description": tool.description or "",
        "category": tool.category or "custom",
        "tags": list(tool.tags or []),
        "timeout": tool.timeout,
        "input_schema": dict(tool.input_schema or {}),
        "execution_mode": "inner",
    }

    if tool.inner_tool_name:
        template_body["inner_tool_name"] = tool.inner_tool_name
        template_body["parameter_mapping"] = dict(tool.parameter_mapping or {})

    return ToolTemplate(
        id=f"from_tool_{tool.tool_code}",
        name=tool.display_name or tool.name,
        description=tool.description or "",
        execution_mode="inner",
        inner_tool_name=tool.inner_tool_name,
        category=tool.category or "custom",
        tags=list(tool.tags or []),
        source=source,
        template=template_body,
    )


def get_all_templates(
    source: str | None = None,
) -> list[ToolTemplate]:
    """
    Get all templates (static + dynamic), with optional filters.

    Args:
        source: Filter by source ("static" or "inner_tool")

    Returns:
        list[ToolTemplate]: Filtered list of templates
    """
    # Merge static and dynamic, with static taking precedence on ID collision
    all_templates: dict[str, ToolTemplate] = {}
    all_templates.update(get_inner_tool_templates())
    all_templates.update(TOOL_TEMPLATES)  # static overrides dynamic on collision

    result = list(all_templates.values())

    if source:
        result = [t for t in result if t.source == source]

    return result
