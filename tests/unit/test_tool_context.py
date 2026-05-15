import json
from types import SimpleNamespace
from uuid import uuid4

from structure.services.context.tool_context import (
    TOOL_CONTEXT_KIND_DESCRIPTION,
    TOOL_CONTEXT_KIND_PROFILE,
    TOOL_CONTEXT_KIND_SCHEMA,
    build_tool_context_entries,
    build_tool_index_entry,
)


def _tool(**overrides):
    defaults = {
        "id": uuid4(),
        "name": "codex_echo_tool",
        "display_name": "Codex Echo Tool",
        "description": "Echoes a text value back to the caller.",
        "tool_code": "mcp__codex_echo_tool",
        "tool_type": "mcp",
        "category": "test",
        "tags": ["echo", "test"],
        "enabled": True,
        "timeout": 30,
        "inner_tool_name": None,
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_tool_context_entries_splits_description_and_schema():
    entries = build_tool_context_entries(_tool())
    by_path = {entry.path: entry for entry in entries}

    assert set(by_path) == {
        "/tools/codex_echo_tool",
        "/tools/codex_echo_tool/description",
        "/tools/codex_echo_tool/schema",
    }
    assert (
        by_path["/tools/codex_echo_tool"].meta["context_kind"]
        == TOOL_CONTEXT_KIND_PROFILE
    )
    assert (
        by_path["/tools/codex_echo_tool/description"].meta["context_kind"]
        == TOOL_CONTEXT_KIND_DESCRIPTION
    )
    assert (
        by_path["/tools/codex_echo_tool/schema"].meta["context_kind"]
        == TOOL_CONTEXT_KIND_SCHEMA
    )
    assert "properties" not in by_path["/tools/codex_echo_tool/description"].content

    schema = json.loads(by_path["/tools/codex_echo_tool/schema"].content)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "codex_echo_tool"
    assert schema["function"]["parameters"]["properties"]["text"]["type"] == "string"


def test_build_tool_index_entry_contains_no_full_schema():
    entry = build_tool_index_entry([_tool()])

    assert entry.path == "/tools/index"
    assert "codex_echo_tool" in entry.content
    assert "/tools/codex_echo_tool/description" in entry.content
    assert "/tools/codex_echo_tool/schema" in entry.content
    assert '"properties"' not in entry.content
