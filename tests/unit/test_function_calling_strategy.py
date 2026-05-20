from types import SimpleNamespace

import pytest

from structure.frameworks.tool_calling import (
    ChatMessage,
    FunctionCallingStrategy,
    LLMReasoningChunk,
    LLMResponse,
)


class _FakeCompletions:
    def __init__(self, stream):
        self.stream = stream
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.stream


class _FakeClient:
    def __init__(self, stream):
        self.completions = _FakeCompletions(stream)
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _chunk(*, reasoning=None, content=None, tool_calls=None, usage=None):
    choices = []
    if reasoning is not None or content is not None or tool_calls is not None:
        choices.append(
            SimpleNamespace(
                delta=SimpleNamespace(
                    reasoning_content=reasoning,
                    content=content,
                    tool_calls=tool_calls,
                )
            )
        )
    return SimpleNamespace(choices=choices, usage=usage)


@pytest.mark.asyncio
async def test_streaming_reasoning_content_and_request_options_are_preserved():
    tool_delta = SimpleNamespace(
        index=0,
        id="call-1",
        function=SimpleNamespace(name="search_context", arguments='{"query":"x"}'),
    )
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=22)
    stream = _FakeAsyncStream(
        [
            _chunk(reasoning="I should search. "),
            _chunk(content="Searching"),
            _chunk(tool_calls=[tool_delta]),
            _chunk(usage=usage),
        ]
    )
    client = _FakeClient(stream)

    strategy = FunctionCallingStrategy()
    items = [
        item
        async for item in strategy.call_llm_stream(
            [ChatMessage(role="user", content="Find x")],
            [{"type": "function", "function": {"name": "search_context"}}],
            model="deepseek-v4-pro",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            client=client,
            request_options={
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        )
    ]

    assert isinstance(items[0], LLMReasoningChunk)
    assert items[0].content == "I should search. "
    assert items[1] == "Searching"

    response = items[-1]
    assert isinstance(response, LLMResponse)
    assert response.content == "Searching"
    assert response.reasoning_content == "I should search. "
    assert response.input_tokens == 11
    assert response.output_tokens == 22
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "search_context"
    assert response.tool_calls[0].arguments == {"query": "x"}

    assert client.completions.kwargs["reasoning_effort"] == "high"
    assert client.completions.kwargs["extra_body"] == {
        "thinking": {"type": "enabled"}
    }
