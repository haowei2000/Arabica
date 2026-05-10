import json
from types import SimpleNamespace

import pytest

from structure.plugins.executors.default.concrete import (
    DefaultExecutor,
    _extract_context_tool_schemas,
)
from structure.schemas.events.event_payloads import EventType


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, statement):
        self.statement = statement
        return _FakeResult(self.rows)


def _tool_schema(name: str):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.asyncio
async def test_global_history_orders_by_created_at_then_reverses(monkeypatch):
    newest = SimpleNamespace(name="newest")
    oldest = SimpleNamespace(name="oldest")
    fake_session = _FakeSession([newest, oldest])

    def fake_get_session(name):
        assert name == "structure"
        return fake_session

    monkeypatch.setattr("structure.extensions.database.get_session", fake_get_session)

    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )

    rows = await executor._fetch_events(global_scope=True, limit=2)

    assert rows == [oldest, newest]

    sql = str(fake_session.statement)
    assert "ORDER BY event.created_at DESC, event.sequence DESC" in sql


def test_get_messages_and_tools_defaults_to_executor_tool_set():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [_tool_schema("codex_echo_tool")]

    _, tools_info = executor.get_messages_and_tools(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "hello"},
            )
        ]
    )

    assert tools_info is None


def test_get_messages_and_tools_filters_forced_tools():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [
        _tool_schema("codex_echo_tool"),
        _tool_schema("codex_math_tool"),
    ]

    _, tools_info = executor.get_messages_and_tools(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={
                    "message": "@codex_echo_tool please run",
                    "forced_tools": ["codex_echo_tool"],
                },
            )
        ]
    )

    assert tools_info == [_tool_schema("codex_echo_tool")]


def test_extract_context_tool_schemas_accepts_leading_slash_path():
    event = SimpleNamespace(
        payload={
            "tool_name": "read_context",
            "result": {
                "data": {
                    "path": "/tools/codex_echo_tool",
                    "content": json.dumps(_tool_schema("codex_echo_tool")),
                    "glance": "Echo tool",
                }
            },
        }
    )

    tools_info = _extract_context_tool_schemas([event])

    assert len(tools_info) == 1
    assert tools_info[0]["function"]["name"] == "codex_echo_tool"
