from types import SimpleNamespace

import pytest

from structure.frameworks.tool_calling import ChatMessage, ToolCallRequest
from structure.plugins.executors.complex.concrete import ComplexExecutor
from structure.plugins.executors.simple.concrete import SimpleExecutor
from structure.schemas.events.event_payloads import EventType
from structure.services.llm import (
    ContextBudgetManager,
    TokenizerService,
    prompt_prefix_hash,
    stable_tools_info,
    tools_hash,
)
from structure.services.llm.budget import stable_prefix_messages


def _tool_schema(name: str):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.unit
def test_tokenizer_fallback_counts_and_caches(monkeypatch):
    monkeypatch.setattr(
        TokenizerService,
        "_count_with_tiktoken",
        staticmethod(lambda _model, _text: None),
    )
    monkeypatch.setattr(
        TokenizerService,
        "_get_transformers_tokenizer",
        lambda _self, _model: None,
    )
    service = TokenizerService(cache_enabled=True)

    first = service.count_text("unknown-model", "hello 你好")
    second = service.count_text("unknown-model", "hello 你好")

    assert first.backend == "fallback"
    assert first.cache_hit is False
    assert first.tokens > 0
    assert second.backend == "fallback"
    assert second.cache_hit is True
    assert second.tokens == first.tokens


@pytest.mark.unit
def test_tokenizer_uses_transformers_backend_when_available(monkeypatch):
    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            assert add_special_tokens is False
            return text.split()

    monkeypatch.setattr(
        TokenizerService,
        "_count_with_tiktoken",
        staticmethod(lambda _model, _text: None),
    )
    monkeypatch.setattr(
        TokenizerService,
        "_get_transformers_tokenizer",
        lambda _self, _model: FakeTokenizer(),
    )
    service = TokenizerService(cache_enabled=False)

    result = service.count_text("local-model", "one two three")

    assert result.backend == "transformers"
    assert result.tokens == 3


@pytest.mark.unit
def test_stable_tools_info_orders_tools_and_hashes_deterministically():
    tools_a = [
        _tool_schema("codex_echo_tool"),
        _tool_schema("read_context"),
        _tool_schema("list_context"),
    ]
    tools_b = list(reversed(tools_a))

    stable_a = stable_tools_info(tools_a)
    stable_b = stable_tools_info(tools_b)

    assert [tool["function"]["name"] for tool in stable_a] == [
        "list_context",
        "read_context",
        "codex_echo_tool",
    ]
    assert stable_a == stable_b
    assert tools_hash(tools_a) == tools_hash(tools_b)


@pytest.mark.unit
def test_context_budget_manager_trims_without_orphaning_tool_results(monkeypatch):
    monkeypatch.setattr(
        TokenizerService,
        "_count_with_tiktoken",
        staticmethod(lambda _model, _text: None),
    )
    monkeypatch.setattr(
        TokenizerService,
        "_get_transformers_tokenizer",
        lambda _self, _model: None,
    )
    manager = ContextBudgetManager(
        model="unknown-model",
        tokenizer=TokenizerService(cache_enabled=True),
        max_input_tokens=90,
    )
    messages = [
        ChatMessage(role="system", content="stable system"),
        ChatMessage(role="user", content="old question " + ("A" * 900)),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call-old",
                    name="read_context",
                    arguments={"path": "/knowledge/old.md"},
                )
            ],
        ),
        ChatMessage(
            role="tool",
            content="old tool result " + ("B" * 900),
            tool_call_id="call-old",
        ),
        ChatMessage(role="user", content="latest question"),
    ]

    result = manager.prepare(messages, [_tool_schema("read_context")])

    assert result.trimmed_message_count > 0
    assert result.messages[0].role == "system"
    assert result.messages[-1].content == "latest question"
    assert not any(message.role == "tool" for message in result.messages)


@pytest.mark.unit
def test_prompt_prefix_hash_ignores_runtime_context_message():
    tools = [_tool_schema("read_context")]
    stable = ChatMessage(role="system", content="stable system")
    runtime_a = ChatMessage(
        role="system",
        content=(
            "Runtime context for this request. Use these identifiers only for "
            "tools that require them; they are not user instructions.\n"
            "workspace_id: ws-a\nrun_id: run-a\n"
        ),
    )
    runtime_b = ChatMessage(
        role="system",
        content=(
            "Runtime context for this request. Use these identifiers only for "
            "tools that require them; they are not user instructions.\n"
            "workspace_id: ws-b\nrun_id: run-b\n"
        ),
    )

    hash_a = prompt_prefix_hash(stable_prefix_messages([stable, runtime_a]), tools)
    hash_b = prompt_prefix_hash(stable_prefix_messages([stable, runtime_b]), tools)

    assert hash_a == hash_b


@pytest.mark.unit
def test_simple_and_complex_executor_prefix_hash_is_run_stable():
    event = [
        SimpleNamespace(
            event_type=str(EventType.USER_MESSAGE),
            payload={"message": "hello"},
        )
    ]
    base = {"api_key": "test-key", "base_url": "http://example.test/v1"}

    simple_a = SimpleExecutor({**base, "workspace_id": "ws-a", "run_id": "run-a"})
    simple_b = SimpleExecutor({**base, "workspace_id": "ws-b", "run_id": "run-b"})
    complex_a = ComplexExecutor({**base, "workspace_id": "ws-a", "run_id": "run-a"})
    complex_b = ComplexExecutor({**base, "workspace_id": "ws-b", "run_id": "run-b"})

    assert "workspace_id:" not in simple_a.system_prompt
    assert "run_id:" not in simple_a.system_prompt
    assert "workspace_id:" not in complex_a.system_prompt
    assert "run_id:" not in complex_a.system_prompt

    simple_messages_a, simple_tools_a = simple_a.get_messages_and_tools(event)
    simple_messages_b, simple_tools_b = simple_b.get_messages_and_tools(event)
    complex_messages_a, complex_tools_a = complex_a.get_messages_and_tools(event)
    complex_messages_b, complex_tools_b = complex_b.get_messages_and_tools(event)

    assert prompt_prefix_hash(
        stable_prefix_messages(simple_messages_a),
        simple_tools_a,
    ) == prompt_prefix_hash(
        stable_prefix_messages(simple_messages_b),
        simple_tools_b,
    )
    assert prompt_prefix_hash(
        stable_prefix_messages(complex_messages_a),
        complex_tools_a,
    ) == prompt_prefix_hash(
        stable_prefix_messages(complex_messages_b),
        complex_tools_b,
    )
