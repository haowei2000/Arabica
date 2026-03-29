"""Function-calling strategy using the OpenAI-compatible ``tools`` parameter.

Works with any provider exposing an OpenAI-compatible chat completions
endpoint (DashScope / Tongyi, Ollama, vLLM, etc.).
"""

from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
import uuid

from openai import AsyncOpenAI

from structure.frameworks.tool_calling.models import (
    ChatMessage,
    LLMResponse,
    ToolCallRequest,
)
from structure.frameworks.tool_calling.strategy import ToolCallingStrategy

logger = logging.getLogger(__name__)


class FunctionCallingStrategy(ToolCallingStrategy):
    """Uses the OpenAI ``tools`` parameter for native function calling."""

    # -- format_tools --------------------------------------------------------

    def format_tools(self, tool_classes: list[type]) -> list[dict[str, Any]]:
        """Return OpenAI function-calling JSON schemas.

        Reuses ``BaseTool.get_json_schema()`` which already produces the
        correct ``{"type": "function", "function": {...}}`` format.
        """
        schemas: list[dict[str, Any]] = []
        for tc in tool_classes:
            try:
                schemas.append(tc.get_json_schema())
            except Exception:
                name = getattr(getattr(tc, "METADATA", None), "name", tc)
                logger.error("Failed to get schema for tool %s", name, exc_info=True)
        return schemas

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        """Create an AsyncOpenAI client. Call once and reuse across iterations."""
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _to_api_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert ``ChatMessage`` list to OpenAI API dicts."""
        return [m.to_openai_dict() for m in messages]

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: list | None) -> list[ToolCallRequest]:
        """Parse tool calls from the OpenAI response object."""
        if not raw_tool_calls:
            return []
        result: list[ToolCallRequest] = []
        for tc in raw_tool_calls:
            try:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                result.append(
                    ToolCallRequest(
                        id=tc.id or str(uuid.uuid4()),
                        name=tc.function.name,
                        arguments=args,
                    )
                )
            except Exception:
                logger.error("Failed to parse tool call: %s", tc, exc_info=True)
        return result

    # -- call_llm (non-streaming) -------------------------------------------

    async def call_llm(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
    ) -> LLMResponse:
        client = self._build_client(api_key, base_url)
        api_messages = self._to_api_messages(messages)

        kwargs: dict[str, Any] = {"model": model, "messages": api_messages}
        if tools_info:
            kwargs["tools"] = tools_info

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        usage = response.usage
        return LLMResponse(
            content=msg.content or "",
            tool_calls=self._parse_tool_calls(msg.tool_calls),
            raw=response,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    # -- call_llm_stream (streaming) ----------------------------------------

    async def call_llm_stream(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
        client: AsyncOpenAI | None = None,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        if client is None:
            client = self._build_client(api_key, base_url)
        api_messages = self._to_api_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools_info:
            kwargs["tools"] = tools_info

        stream = await client.chat.completions.create(**kwargs)

        content_buf = ""
        # Accumulate tool call deltas keyed by index.
        tc_buffers: dict[int, dict[str, Any]] = {}
        input_tokens: int = 0
        output_tokens: int = 0

        async for chunk in stream:
            # Usage arrives in the final chunk (choices may be empty).
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # -- text token --
            if delta.content:
                content_buf += delta.content
                yield delta.content

            # -- tool call deltas --
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_buffers:
                        tc_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    buf = tc_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["arguments"] += tc_delta.function.arguments

        # -- build final tool calls from accumulated buffers --
        tool_calls: list[ToolCallRequest] = []
        for _idx in sorted(tc_buffers):
            buf = tc_buffers[_idx]
            try:
                args = json.loads(buf["arguments"]) if buf["arguments"] else {}
            except json.JSONDecodeError:
                logger.error("Invalid JSON in tool call args: %s", buf["arguments"])
                args = {}
            tool_calls.append(
                ToolCallRequest(
                    id=buf["id"] or str(uuid.uuid4()),
                    name=buf["name"],
                    arguments=args,
                )
            )

        yield LLMResponse(
            content=content_buf,
            tool_calls=tool_calls,
            raw=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
