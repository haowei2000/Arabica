import json

from structure.core.interfaces.structurer import BaseStructurer
from structure.models.context.tools.tool import Tool
from structure.plugins.structurers import register_structurer
from structure.schemas.context.context_schema import ContextCore
from structure.utils.context import slugify


@register_structurer
class ToolStructurer(BaseStructurer):
    name = "Tool"
    description = "Multi-level tool structurer: overview → schema detail"

    def structure(self, input: Tool) -> list[ContextCore]:
        tool = input
        results: list[ContextCore] = []

        glance = tool.name
        if tool.description:
            glance = f"{tool.name} — {tool.description}"

        tool_path = slugify(f"/tool/{tool.name}")

        # Level 1: tool overview
        lines: list[str] = [f"{tool.name}/"]
        lines.append(f"    type: {tool.tool_type}")
        if tool.category:
            lines.append(f"    category: {tool.category}")
        if tool.tags:
            lines.append(f"    tags: {', '.join(tool.tags)}")

        # Show chain steps if pipeline mode
        if tool.chain:
            lines.append("    chain:")
            for i, step in enumerate(tool.chain):
                connector = "└── " if i == len(tool.chain) - 1 else "├── "
                step_name = step.get("tool_name", "?")
                lines.append(f"        {connector}{step_name}")
        # Show delegation target if single mode
        elif tool.inner_tool_name:
            lines.append(f"    delegates to: {tool.inner_tool_name}")

        # Show input params summary
        if tool.input_schema:
            props = tool.input_schema.get("properties") or {}
            required = set(tool.input_schema.get("required") or [])
            if props:
                lines.append("    params:")
                param_items = list(props.items())
                for i, (param, schema) in enumerate(param_items):
                    connector = "└── " if i == len(param_items) - 1 else "├── "
                    param_type = schema.get("type", "any")
                    req_mark = "*" if param in required else ""
                    lines.append(f"        {connector}{param}{req_mark}: {param_type}")

        results.append(ContextCore(
            glance=glance,
            content="\n".join(lines),
            path=tool_path,
        ))

        # Level 2: input schema detail (if present)
        if tool.input_schema:
            results.append(ContextCore(
                glance=f"{tool.name} / input schema",
                content=json.dumps(tool.input_schema, indent=2, ensure_ascii=False),
                path=f"{tool_path}/schema.ctx",
            ))

        return results
