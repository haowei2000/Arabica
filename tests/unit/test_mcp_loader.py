from types import SimpleNamespace

import pytest

from structure.registries.mcp_loader import build_mcp_tool_class


def _record(name: str, input_schema: dict):
    return SimpleNamespace(
        name=name,
        display_name=name,
        description="test tool",
        category="mcp",
        tags=[],
        timeout=30,
        config={},
        input_schema=input_schema,
    )


@pytest.mark.asyncio
async def test_arguments_wrapper_mcp_tool_accepts_direct_arguments():
    tool_cls = build_mcp_tool_class(
        _record(
            "read_context",
            {
                "type": "object",
                "required": ["arguments"],
                "properties": {
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    }
                },
            },
        )
    )

    validated = await tool_cls().validate_input(
        {"path": "tools/example", "workspace_id": "workspace-1"}
    )

    assert validated.model_dump() == {
        "arguments": {"path": "tools/example", "workspace_id": "workspace-1"}
    }


@pytest.mark.asyncio
async def test_direct_schema_mcp_tool_keeps_direct_arguments():
    tool_cls = build_mcp_tool_class(
        _record(
            "codex_route_echo_tool",
            {
                "type": "object",
                "required": ["marker"],
                "properties": {
                    "marker": {"type": "string"},
                    "note": {"type": "string", "default": ""},
                },
            },
        )
    )

    validated = await tool_cls().validate_input({"marker": "route1", "note": "ok"})

    assert validated.model_dump() == {"marker": "route1", "note": "ok"}
