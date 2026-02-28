"""Prompt-calling strategy — tool descriptions in the system prompt.

The LLM receives tool information as part of the system prompt text and
outputs tool calls using ``<tool_call>`` XML markers.  This works with
any LLM, including those that don't support native function calling.
"""

from collections.abc import AsyncGenerator
import json
import logging
import re
from typing import Any
import uuid

from openai import AsyncOpenAI

from aiwen.frameworks.tool_calling.models import (
    ChatMessage,
    LLMResponse,
    ToolCallRequest,
)
from aiwen.frameworks.tool_calling.prompt_template import build_tools_system_prompt
from aiwen.frameworks.tool_calling.strategy import ToolCallingStrategy

logger = logging.getLogger(__name__)

# Regex to extract <tool_call>...</tool_call> blocks from text.
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL,
)


def _parse_tool_calls_from_text(text: str) -> list[ToolCallRequest]:
    """Extract ``ToolCallRequest`` instances from XML-tagged text."""
    calls: list[ToolCallRequest] = []
    for match in _TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
            calls.append(
                ToolCallRequest(
                    id=data.get("id", str(uuid.uuid4())),
                    name=data["name"],
                    arguments=data.get("arguments", {}),
                )
            )
        except (json.JSONDecodeError, KeyError):
            logger.error("Failed to parse tool_call block: %s", raw)
    return calls


def _strip_tool_call_blocks(text: str) -> str:
    """Remove ``<tool_call>...</tool_call>`` blocks from text."""
    return _TOOL_CALL_RE.sub("", text).strip()


class PromptCallingStrategy(ToolCallingStrategy):
    """Embeds tool descriptions in the system prompt and parses XML output."""

    # -- format_tools --------------------------------------------------------

    def format_tools(self, tool_classes: list[type]) -> str:
        """Return a text block describing all tools for the system prompt."""
        if not tool_classes:
            return ""
        return build_tools_system_prompt(tool_classes)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        """Create an AsyncOpenAI client. Call once and reuse to avoid overhead."""
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _inject_tools_prompt(
        messages: list[ChatMessage], tools_prompt: str
    ) -> list[dict[str, Any]]:
        """Build API message dicts, injecting tools_prompt into the system message."""
        result: list[dict[str, Any]] = []
        system_injected = False
        for msg in messages:
            d = msg.to_openai_dict()
            if msg.role == "system" and not system_injected and tools_prompt:
                d["content"] = (d.get("content") or "") + "\n\n" + tools_prompt
                system_injected = True
            result.append(d)

        # If there was no system message, prepend one with the tools prompt.
        if not system_injected and tools_prompt:
            result.insert(0, {"role": "system", "content": tools_prompt})

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
        api_messages = self._inject_tools_prompt(messages, tools_info or "")

        response = await client.chat.completions.create(
            model=model, messages=api_messages
        )
        choice = response.choices[0]
        raw_content = choice.message.content or ""

        tool_calls = _parse_tool_calls_from_text(raw_content)
        clean_content = _strip_tool_call_blocks(raw_content) if tool_calls else raw_content

        return LLMResponse(
            content=clean_content,
            tool_calls=tool_calls,
            raw=response,
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
        api_messages = self._inject_tools_prompt(messages, tools_info or "")

        stream = await client.chat.completions.create(
            model=model,
            messages=api_messages,
            stream=True,
        )

        full_content = ""
        # Buffer for detecting <tool_call> tags at the end of the stream.
        # We hold back text once we see a potential opening '<'.
        hold_buf = ""
        # Whether we are inside a tool_call block region.
        in_tool_block = False

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            token = delta.content or ""
            if not token:
                continue

            full_content += token

            # Accumulate while we might be inside a tool_call tag.
            hold_buf += token

            if not in_tool_block:
                # Check if the buffer might be the start of a <tool_call> tag.
                tag_start = "<tool_call>"
                idx = hold_buf.find("<")
                if idx == -1:
                    # No '<' at all — safe to flush everything.
                    yield hold_buf
                    hold_buf = ""
                elif tag_start.startswith(hold_buf[idx:]):
                    # Partial match — keep buffering.
                    # Flush everything before the '<'.
                    if idx > 0:
                        yield hold_buf[:idx]
                        hold_buf = hold_buf[idx:]
                elif "<tool_call>" in hold_buf:
                    # Full opening tag found.
                    before, _, after = hold_buf.partition("<tool_call>")
                    if before:
                        yield before
                    hold_buf = "<tool_call>" + after
                    in_tool_block = True
                else:
                    # '<' was a false positive — flush everything.
                    yield hold_buf
                    hold_buf = ""
            else:
                # Inside a tool_call block, keep buffering until </tool_call>.
                if "</tool_call>" in hold_buf:
                    # The block is complete.  There might be more text after.
                    end_idx = hold_buf.find("</tool_call>") + len("</tool_call>")
                    remainder = hold_buf[end_idx:]
                    hold_buf = remainder
                    in_tool_block = False
                    # Don't yield the tool_call block itself — it's not user-facing text.
                    # Check if the remainder starts another tool_call block.
                    if "<tool_call>" in hold_buf:
                        before, _, after = hold_buf.partition("<tool_call>")
                        if before.strip():
                            yield before
                        hold_buf = "<tool_call>" + after
                        in_tool_block = True

        # Flush any remaining buffered text that is not a tool_call block.
        if hold_buf:
            # Check if there are tool_call blocks in the remaining buffer.
            remaining_clean = _strip_tool_call_blocks(hold_buf)
            if remaining_clean:
                yield remaining_clean

        # Parse tool calls from the full output.
        tool_calls = _parse_tool_calls_from_text(full_content)
        clean_content = _strip_tool_call_blocks(full_content) if tool_calls else full_content

        yield LLMResponse(
            content=clean_content,
            tool_calls=tool_calls,
            raw=None,
        )
