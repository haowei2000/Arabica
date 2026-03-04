"""Prompt templates for the prompt-calling strategy.

Generates text descriptions of tools for injection into the system prompt
and defines the XML convention for tool call output.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Per-tool description template
# ---------------------------------------------------------------------------

_TOOL_TEMPLATE = """\
### {name}
{description}

Parameters:
{params_block}"""

_PARAM_LINE = "- **{name}** ({type_str}{required}): {description}"


def _format_param(name: str, prop: dict[str, Any], required_list: list[str]) -> str:
    """Format a single parameter line."""
    type_str = prop.get("type", "any")
    # Handle array types
    if type_str == "array" and "items" in prop:
        items_type = prop["items"].get("type", "any")
        type_str = f"array[{items_type}]"
    # Handle enum values
    if "enum" in prop:
        type_str += f", one of: {prop['enum']}"

    req = ", required" if name in required_list else ", optional"
    desc = prop.get("description", "")
    if "default" in prop:
        desc += f" (default: {prop['default']})"
    return _PARAM_LINE.format(
        name=name,
        type_str=type_str,
        required=req,
        description=desc,
    )


def format_tool_description(tool_class: type) -> str:
    """Generate a human-readable description of a single tool.

    Args:
        tool_class: A ``BaseTool`` subclass with ``METADATA`` and
            ``InputSchema`` class attributes.

    Returns:
        Markdown-formatted tool description.
    """
    metadata = tool_class.METADATA
    schema = tool_class.InputSchema.model_json_schema()
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if properties:
        lines = [
            _format_param(name, prop, required)
            for name, prop in properties.items()
        ]
        params_block = "\n".join(lines)
    else:
        params_block = "  (no parameters)"

    return _TOOL_TEMPLATE.format(
        name=metadata.name,
        description=metadata.description,
        params_block=params_block,
    )


# ---------------------------------------------------------------------------
# System prompt injection
# ---------------------------------------------------------------------------

_SYSTEM_INJECTION = """\
<platform-injection>
## Available Tools

You have access to the following tools. When you need to use a tool, \
output ONE OR MORE tool call blocks in the following XML format (each on its own line):

```
<tool_call>
{{"name": "tool_name", "arguments": {{"param1": "value1", "param2": "value2"}}}}
</tool_call>
```

Rules:
- The JSON inside <tool_call> must be valid JSON.
- You may output multiple <tool_call> blocks if you want to call several tools.
- If you do NOT need to call a tool, simply respond with normal text (no <tool_call> tags).
- Always provide required parameters. Optional parameters can be omitted.

{tool_descriptions}
</platform-injection>"""


def build_tools_system_prompt(tool_classes: list[type]) -> str:
    """Build the tool-instruction block to inject into the system prompt.

    Args:
        tool_classes: List of ``BaseTool`` subclasses.

    Returns:
        A string to append to (or embed in) the system prompt.
    """
    descriptions = "\n\n".join(
        format_tool_description(tc) for tc in tool_classes
    )
    return _SYSTEM_INJECTION.format(tool_descriptions=descriptions)
