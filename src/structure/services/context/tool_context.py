"""Helpers for representing tools as structured context entries."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from structure.utils.context import slugify

TOOL_CONTEXT_KIND_INDEX = "tool_index"
TOOL_CONTEXT_KIND_PROFILE = "tool_profile"
TOOL_CONTEXT_KIND_DESCRIPTION = "tool_description"
TOOL_CONTEXT_KIND_SCHEMA = "tool_schema"


@dataclass(frozen=True)
class ToolContextEntry:
    path: str
    glance: str
    content: str
    tags: list[str]
    meta: dict[str, Any]
    embed: bool = False


def tool_context_base_path(tool_name: str) -> str:
    """Return the canonical structured context base path for a tool."""
    return f"/tools/{slugify(tool_name)}"


def build_openai_tool_schema(tool: Any) -> dict[str, Any]:
    """Build the OpenAI-compatible function schema stored at /tools/*/schema."""
    tool_name = str(getattr(tool, "name", "") or "")
    display_name = getattr(tool, "display_name", None) or tool_name
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": getattr(tool, "description", None) or display_name,
            "parameters": getattr(tool, "input_schema", None) or {},
        },
    }


def _tool_tags(tool: Any, *extra: str) -> list[str]:
    tags = ["tool", *extra]
    for tag in getattr(tool, "tags", None) or []:
        if tag not in tags:
            tags.append(tag)
    return tags


def _tool_meta(tool: Any, *, kind: str) -> dict[str, Any]:
    tool_id = getattr(tool, "id", None)
    return {
        "context_kind": kind,
        "tool_id": str(tool_id) if tool_id else None,
        "tool_name": getattr(tool, "name", None),
        "display_name": getattr(tool, "display_name", None),
        "tool_code": getattr(tool, "tool_code", None),
        "tool_type": getattr(tool, "tool_type", None),
        "category": getattr(tool, "category", None),
        "enabled": getattr(tool, "enabled", None),
    }


def _description_markdown(tool: Any) -> str:
    name = str(getattr(tool, "name", "") or "")
    display_name = getattr(tool, "display_name", None) or name
    description = getattr(tool, "description", None) or ""
    category = getattr(tool, "category", None)
    tags = getattr(tool, "tags", None) or []
    timeout = getattr(tool, "timeout", None)
    inner_tool_name = getattr(tool, "inner_tool_name", None)

    lines = [
        f"# {display_name}",
        "",
        f"- name: `{name}`",
    ]
    if category:
        lines.append(f"- category: `{category}`")
    if tags:
        lines.append(f"- tags: {', '.join(f'`{tag}`' for tag in tags)}")
    if timeout is not None:
        lines.append(f"- timeout_seconds: {timeout}")
    if inner_tool_name:
        lines.append(f"- delegates_to: `{inner_tool_name}`")
    if description:
        lines.extend(["", description])

    lines.extend(
        [
            "",
            "Read the schema only when you have decided to call this tool:",
            f"`{tool_context_base_path(name)}/schema`",
        ]
    )
    return "\n".join(lines)


def build_tool_context_entries(tool: Any) -> list[ToolContextEntry]:
    """Build structured context entries for one tool.

    The schema is isolated under ``/tools/{name}/schema`` so discovery can use
    cheaper description/profile entries without loading function parameters.
    """
    name = str(getattr(tool, "name", "") or "")
    display_name = getattr(tool, "display_name", None) or name
    description = getattr(tool, "description", None) or ""
    base_path = tool_context_base_path(name)
    short_description = description[:120] if description else "No description"
    glance = f"{display_name} - {short_description}"
    schema_str = json.dumps(
        build_openai_tool_schema(tool),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    profile_content = "\n".join(
        [
            f"Tool: {name}",
            f"Display name: {display_name}",
            f"Description: {description}" if description else "Description: ",
            f"Description path: {base_path}/description",
            f"Schema path: {base_path}/schema",
        ]
    )

    return [
        ToolContextEntry(
            path=base_path,
            glance=glance,
            content=profile_content,
            tags=_tool_tags(tool, "profile"),
            meta=_tool_meta(tool, kind=TOOL_CONTEXT_KIND_PROFILE),
            embed=True,
        ),
        ToolContextEntry(
            path=f"{base_path}/description",
            glance=glance,
            content=_description_markdown(tool),
            tags=_tool_tags(tool, "description"),
            meta=_tool_meta(tool, kind=TOOL_CONTEXT_KIND_DESCRIPTION),
            embed=True,
        ),
        ToolContextEntry(
            path=f"{base_path}/schema",
            glance=f"Schema for {display_name}",
            content=schema_str,
            tags=_tool_tags(tool, "schema"),
            meta=_tool_meta(tool, kind=TOOL_CONTEXT_KIND_SCHEMA),
            embed=False,
        ),
    ]


def build_tools_index_content(tools: list[Any]) -> str:
    """Render a compact tool index that excludes full schemas."""
    lines = [
        "# Tool Index",
        "",
        "Use `/tools/{tool_name}/description` to inspect a tool, then read "
        "`/tools/{tool_name}/schema` only when you intend to call it.",
        "",
    ]
    for tool in sorted(tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "")
        display_name = getattr(tool, "display_name", None) or name
        description = getattr(tool, "description", None) or ""
        tags = getattr(tool, "tags", None) or []
        base_path = tool_context_base_path(name)
        tag_text = f" tags={','.join(tags)}" if tags else ""
        lines.append(
            f"- `{name}` ({display_name}){tag_text}: {description[:180]} "
            f"[description: `{base_path}/description`, schema: `{base_path}/schema`]"
        )
    return "\n".join(lines).rstrip()


def build_tool_index_entry(tools: list[Any]) -> ToolContextEntry:
    """Build the shared /tools/index context entry for a tool collection."""
    count = len(tools)
    return ToolContextEntry(
        path="/tools/index",
        glance=f"{count} available tool(s), descriptions only",
        content=build_tools_index_content(tools),
        tags=["tool", "index"],
        meta={"context_kind": TOOL_CONTEXT_KIND_INDEX, "tool_count": count},
        embed=True,
    )
