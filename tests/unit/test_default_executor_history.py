import json
from types import SimpleNamespace

import pytest

from structure.config.factory import get_settings
from structure.core.interfaces.executor import WaitingForTool
from structure.frameworks.tool_calling import (
    ChatMessage,
    PromptCallingStrategy,
    ToolCallRequest,
)
from structure.plugins.executors.default.concrete import (
    DefaultExecutor,
    _events_to_messages,
    _extract_context_tool_schemas,
    _unresolved_tool_call_ids,
)
from structure.schemas.events.event_payloads import EventType


@pytest.fixture(autouse=True)
def openai_env_contract(monkeypatch):
    monkeypatch.setenv("OPENAI__API_KEY", "test-key")
    monkeypatch.setenv("OPENAI__BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("OPENAI__MODEL", "test-model")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def test_system_prompt_head_is_stable_and_runtime_context_is_tail_loaded():
    workspace_id = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-000000000002"
    executor = DefaultExecutor(
        {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )

    messages, _ = executor.get_messages_and_tools(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "hello"},
            )
        ]
    )

    assert messages[0].role == "system"
    assert f"workspace_id: {workspace_id}" not in messages[0].content
    assert f"run_id: {run_id}" not in messages[0].content
    assert messages[1].role == "system"
    assert f"workspace_id: {workspace_id}" in messages[1].content
    assert f"run_id: {run_id}" in messages[1].content
    assert messages[-1].role == "user"
    assert messages[-1].content == "hello"


def test_executor_uses_openai_env_contract_over_config_values():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "legacy-key",
            "base_url": "http://legacy.example/v1",
            "model_name": "legacy-model",
        }
    )

    assert executor._api_key == "test-key"
    assert executor._base_url == "http://example.test/v1"
    assert executor.model_name == "test-model"
    assert not hasattr(executor, "model_provider")


def test_prompt_calling_keeps_tools_prompt_out_of_stable_system_head():
    api_messages = PromptCallingStrategy._inject_tools_prompt(
        [
            ChatMessage(role="system", content="stable head"),
            ChatMessage(role="user", content="hello"),
        ],
        "dynamic tools",
    )

    assert api_messages == [
        {"role": "system", "content": "stable head"},
        {"role": "system", "content": "dynamic tools"},
        {"role": "user", "content": "hello"},
    ]


def test_batch_plan_keeps_key_summaries_before_full_tail_events():
    workspace_id = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-000000000002"
    executor = DefaultExecutor(
        {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )

    messages, _ = executor.get_messages_and_tools_from_batch_plan(
        key_contents=["batch_key=run:old:turn:1\nsummary=old turn"],
        load_all_events=[
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "current question"},
            )
        ],
    )

    assert messages[0].role == "system"
    assert messages[1].role == "system"
    assert "batch_key=run:old:turn:1" in messages[1].content
    assert messages[2].role == "system"
    assert f"run_id: {run_id}" in messages[2].content
    assert messages[-1].role == "user"
    assert messages[-1].content == "current question"


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


def test_lazy_tool_schema_mode_uses_bootstrap_tools_until_schema_is_read():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [
        _tool_schema("list_context"),
        _tool_schema("read_context"),
        _tool_schema("codex_echo_tool"),
    ]

    active_tools = executor._resolve_active_tools_info(None)

    assert [tool["function"]["name"] for tool in active_tools] == [
        "list_context",
        "read_context",
    ]


def test_lazy_tool_schema_mode_merges_bootstrap_with_selected_schema():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [
        _tool_schema("list_context"),
        _tool_schema("read_context"),
        _tool_schema("codex_echo_tool"),
    ]

    active_tools = executor._resolve_active_tools_info(
        [_tool_schema("codex_echo_tool")]
    )

    assert [tool["function"]["name"] for tool in active_tools] == [
        "list_context",
        "read_context",
        "codex_echo_tool",
    ]


def test_get_messages_and_tools_keeps_tool_description_result_in_conversation():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [
        _tool_schema("list_context"),
        _tool_schema("read_context"),
        _tool_schema("codex_echo_tool"),
    ]

    messages, tools_info = executor.get_messages_and_tools(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Find an echo-like tool."},
            ),
            SimpleNamespace(
                event_type=str(EventType.AGENT_MESSAGE),
                payload={
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-read-description",
                            "name": "read_context",
                            "arguments": {"path": "/tools/codex_echo_tool/description"},
                        }
                    ],
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_id": "call-read-description",
                    "tool_name": "read_context",
                    "result": {
                        "data": {
                            "path": "/tools/codex_echo_tool/description",
                            "content": "Echoes input text back to the caller.",
                            "glance": "Echo tool",
                        }
                    },
                },
            ),
        ]
    )

    assert tools_info is None
    assert any(
        message.role == "tool" and "Echoes input text back" in str(message.content)
        for message in messages
    )


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


def test_user_message_attachments_render_as_context_paths_only():
    messages = _events_to_messages(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={
                    "message": "Analyze this file",
                    "attachments": [
                        {
                            "id": "ctx-1",
                            "name": "secret.txt",
                            "path": "/chat/uploads/ctx-1/secret.txt",
                            "content_type": "text/plain",
                            "size_bytes": 12,
                        }
                    ],
                },
            )
        ]
    )

    assert len(messages) == 1
    assert "Analyze this file" in messages[0].content
    assert "/chat/uploads/ctx-1/secret.txt" in messages[0].content
    assert "Use read_context" in messages[0].content
    assert "secret file body" not in messages[0].content


def test_agent_tool_call_replay_preserves_reasoning_content():
    messages = _events_to_messages(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Search the workspace"},
            ),
            SimpleNamespace(
                event_type=str(EventType.AGENT_MESSAGE),
                payload={
                    "content": "",
                    "reasoning_content": "I need the search_context tool.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "search_context",
                            "arguments": {"query": "DeepSeek"},
                        }
                    ],
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_id": "call-1",
                    "tool_name": "search_context",
                    "result": {"matches": ["DeepSeek"]},
                },
            ),
        ]
    )

    assistant = messages[1]
    assert assistant.role == "assistant"
    assert assistant.reasoning_content == "I need the search_context tool."

    api_message = assistant.to_openai_dict()
    assert api_message["reasoning_content"] == "I need the search_context tool."
    assert api_message["tool_calls"][0]["id"] == "call-1"
    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == "call-1"


def test_events_to_messages_compacts_large_tool_call_arguments_for_replay():
    large_content = "A" * 5_000

    messages = _events_to_messages(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Create the artifact"},
            ),
            SimpleNamespace(
                event_type=str(EventType.AGENT_MESSAGE),
                payload={
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-create",
                            "name": "create_artifact",
                            "arguments": {
                                "path": "/artifacts/report.md",
                                "content": large_content,
                            },
                        }
                    ],
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_id": "call-create",
                    "tool_name": "create_artifact",
                    "result": {"artifact_id": "artifact-1"},
                },
            ),
        ],
        tool_argument_max_chars=80,
    )

    arguments = messages[1].tool_calls[0].arguments
    serialized = json.dumps(arguments)
    assert arguments["content_history_replay_compacted"] is True
    assert arguments["content_original_chars"] == len(large_content)
    assert len(arguments["content"]) < 220
    assert large_content not in serialized


def test_events_to_messages_compacts_large_tool_results_for_replay():
    large_content = "Document body\n" + ("B" * 5_000)

    messages = _events_to_messages(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Read the document"},
            ),
            SimpleNamespace(
                event_type=str(EventType.AGENT_MESSAGE),
                payload={
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-read",
                            "name": "read_context",
                            "arguments": {"path": "/knowledge/source.md"},
                        }
                    ],
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_id": "call-read",
                    "tool_name": "read_context",
                    "result": {
                        "data": {
                            "path": "/knowledge/source.md",
                            "content": large_content,
                            "glance": "Source document",
                        }
                    },
                },
            ),
        ],
        tool_result_max_chars=96,
    )

    payload = json.loads(messages[2].content)
    data = payload["data"]
    assert data["content_history_replay_compacted"] is True
    assert data["content_original_chars"] == len(large_content)
    assert data["path"] == "/knowledge/source.md"
    assert len(data["content"]) < 260
    assert large_content not in messages[2].content


def test_unresolved_tool_call_ids_tracks_parallel_tool_results():
    raw_events = [
        SimpleNamespace(
            event_type=str(EventType.USER_MESSAGE),
            payload={"message": "Read two files"},
        ),
        SimpleNamespace(
            event_type=str(EventType.AGENT_MESSAGE),
            payload={
                "content": "",
                "tool_calls": [
                    {"id": "call-a", "name": "read_context", "arguments": {}},
                    {"id": "call-b", "name": "read_context", "arguments": {}},
                ],
            },
        ),
        SimpleNamespace(
            event_type=str(EventType.TOOL_RESULT),
            payload={"tool_id": "call-a", "tool_name": "read_context", "result": {}},
        ),
    ]

    assert _unresolved_tool_call_ids(raw_events) == {"call-b"}

    raw_events.append(
        SimpleNamespace(
            event_type=str(EventType.TOOL_RESULT),
            payload={"tool_id": "call-b", "tool_name": "read_context", "result": {}},
        )
    )

    assert _unresolved_tool_call_ids(raw_events) == set()


def test_events_to_messages_demotes_partial_parallel_tool_result():
    messages = _events_to_messages(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Read two files"},
            ),
            SimpleNamespace(
                event_type=str(EventType.AGENT_MESSAGE),
                payload={
                    "content": "",
                    "tool_calls": [
                        {"id": "call-a", "name": "read_context", "arguments": {}},
                        {"id": "call-b", "name": "read_context", "arguments": {}},
                    ],
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_id": "call-a",
                    "tool_name": "read_context",
                    "result": {"data": "first result"},
                },
            ),
        ]
    )

    assert all(message.role != "tool" for message in messages)
    assert messages[1].role == "assistant"
    assert messages[1].tool_calls is None
    assert messages[2].role == "user"
    assert "first result" in str(messages[2].content)


@pytest.mark.asyncio
async def test_on_tool_result_replays_when_history_has_all_parallel_results():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor._pending_tool_ids = {"call-a", "call-b"}

    async def fake_agentic_loop(messages, tools_info=None):
        yield SimpleNamespace(
            event_type=str(EventType.AGENT_MESSAGE),
            payload={"content": "continued"},
        )

    executor._agentic_loop = fake_agentic_loop
    events = [
        SimpleNamespace(
            event_type=str(EventType.USER_MESSAGE),
            payload={"message": "Read two files"},
        ),
        SimpleNamespace(
            event_type=str(EventType.AGENT_MESSAGE),
            payload={
                "content": "",
                "tool_calls": [
                    {"id": "call-a", "name": "read_context", "arguments": {}},
                    {"id": "call-b", "name": "read_context", "arguments": {}},
                ],
            },
        ),
        SimpleNamespace(
            event_type=str(EventType.TOOL_RESULT),
            payload={"tool_id": "call-a", "tool_name": "read_context", "result": {}},
        ),
        SimpleNamespace(
            event_type=str(EventType.TOOL_RESULT),
            payload={"tool_id": "call-b", "tool_name": "read_context", "result": {}},
        ),
    ]

    output = [event async for event in executor._on_tool_result(events)]

    assert executor._pending_tool_ids == set()
    assert len(output) == 1
    assert output[0].payload["content"] == "continued"


@pytest.mark.asyncio
async def test_emit_tool_calls_injects_runtime_ids_into_mcp_arguments_wrapper():
    workspace_id = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-000000000002"
    executor = DefaultExecutor(
        {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )

    events = executor._emit_tool_calls(
        [
            ToolCallRequest(
                id="call-1",
                name="read_context",
                arguments={"arguments": {"path": "/skills/demo/content"}},
            )
        ]
    )

    event = await anext(events)
    with pytest.raises(WaitingForTool):
        await anext(events)

    arguments = event.payload["arguments"]
    assert arguments["workspace_id"] == workspace_id
    assert arguments["run_id"] == run_id
    assert arguments["arguments"]["workspace_id"] == workspace_id
    assert arguments["arguments"]["run_id"] == run_id
    assert arguments["arguments"]["path"] == "/skills/demo/content"


def test_get_messages_and_tools_can_force_new_tool_after_schema_was_loaded():
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
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_name": "read_context",
                    "result": {
                        "data": {
                            "path": "/tools/codex_echo_tool/schema",
                            "content": json.dumps(_tool_schema("codex_echo_tool")),
                            "glance": "Echo tool",
                        }
                    },
                },
            ),
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={
                    "message": "please run the math tool",
                    "forced_tools": ["codex_math_tool"],
                },
            ),
        ]
    )

    assert tools_info == [_tool_schema("codex_math_tool")]


def test_get_messages_and_tools_loads_legacy_tool_schema_result():
    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )
    executor.tools_info = [
        _tool_schema("list_context"),
        _tool_schema("read_context"),
        _tool_schema("codex_echo_tool"),
    ]

    messages, tools_info = executor.get_messages_and_tools(
        [
            SimpleNamespace(
                event_type=str(EventType.USER_MESSAGE),
                payload={"message": "Load the legacy echo schema."},
            ),
            SimpleNamespace(
                event_type=str(EventType.TOOL_RESULT),
                payload={
                    "tool_name": "read_context",
                    "result": {
                        "data": {
                            "path": "/tools/codex_echo_tool",
                            "content": json.dumps(_tool_schema("codex_echo_tool")),
                            "glance": "Echo tool",
                        }
                    },
                },
            ),
        ]
    )

    assert tools_info is not None
    assert [tool["function"]["name"] for tool in tools_info] == ["codex_echo_tool"]
    active_tools = executor._resolve_active_tools_info(tools_info)
    assert [tool["function"]["name"] for tool in active_tools] == [
        "list_context",
        "read_context",
        "codex_echo_tool",
    ]
    assert all("codex_echo_tool" not in str(message.content) for message in messages)


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


def test_extract_context_tool_schemas_accepts_structured_schema_path():
    event = SimpleNamespace(
        payload={
            "tool_name": "read_context",
            "result": {
                "data": {
                    "path": "/tools/codex_echo_tool/schema",
                    "content": json.dumps(_tool_schema("codex_echo_tool")),
                    "glance": "Schema for Echo tool",
                }
            },
        }
    )

    tools_info = _extract_context_tool_schemas([event])

    assert len(tools_info) == 1
    assert tools_info[0]["function"]["name"] == "codex_echo_tool"


def test_extract_context_tool_schemas_ignores_tool_description_path():
    event = SimpleNamespace(
        payload={
            "tool_name": "read_context",
            "result": {
                "data": {
                    "path": "/tools/codex_echo_tool/description",
                    "content": "Echoes text back to the user",
                    "glance": "Echo tool",
                }
            },
        }
    )

    assert _extract_context_tool_schemas([event]) == []
