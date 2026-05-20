"""Data models for the custom tool calling architecture.

Provides default, JSON-serializable dataclasses that replace LangChain's
``AIMessage`` / ``ToolMessage`` types in the executor layer.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class OpenAIFunctionParameters(BaseModel):
    """JSON Schema object describing a function's parameters."""

    type: str = "object"
    properties: dict[str, Any] = {}
    required: list[str] = []

    model_config = {"extra": "allow"}


class OpenAIFunction(BaseModel):
    """The ``function`` block inside an OpenAI tool definition."""

    name: str
    description: str = ""
    parameters: OpenAIFunctionParameters = OpenAIFunctionParameters()
    strict: bool | None = None


class OpenAITool(BaseModel):
    """Full OpenAI tool definition passed in the ``tools`` parameter.

    Example::

        OpenAITool(
            type="function",
            function=OpenAIFunction(
                name="my_tool",
                description="Does something",
                parameters=OpenAIFunctionParameters(
                    properties={"x": {"type": "string"}},
                    required=["x"],
                ),
            ),
        )
    """

    type: str = "function"
    function: OpenAIFunction


@dataclass
class ToolCallRequest:
    """A single tool call parsed from LLM output."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Unified LLM response regardless of calling strategy.

    Attributes:
        content: Text content from the LLM.
        tool_calls: Parsed tool calls (empty list if none).
        reasoning_content: Provider-returned reasoning trace, when available.
        raw: Raw API response object for debugging.
        input_tokens: Number of prompt tokens consumed.
        output_tokens: Number of completion tokens generated.
    """

    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    reasoning_content: str | None = None
    raw: Any = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMReasoningChunk:
    """Streaming reasoning chunk emitted separately from user-visible text."""

    content: str


@dataclass
class ChatMessage:
    """Simple message type replacing LangChain's AIMessage/ToolMessage.

    Attributes:
        role: One of ``"system"``, ``"user"``, ``"assistant"``, ``"tool"``.
        content: Text content of the message.
        tool_calls: Parsed tool calls (only for ``role="assistant"``).
        reasoning_content: Assistant reasoning trace required by some
            thinking-mode providers when replaying tool-call turns.
        tool_call_id: The tool call ID this message responds to
            (only for ``role="tool"``).
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCallRequest] | None = None
    reasoning_content: str | None = None
    tool_call_id: str | None = None

    # -- serialization helpers ------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ]
        if self.reasoning_content is not None:
            d["reasoning_content"] = self.reasoning_content
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatMessage":
        """Reconstruct a ``ChatMessage`` from a dict."""
        tool_calls = None
        if "tool_calls" in data:
            tool_calls = [
                ToolCallRequest(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                )
                for tc in data["tool_calls"]
            ]
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            tool_calls=tool_calls,
            reasoning_content=data.get("reasoning_content"),
            tool_call_id=data.get("tool_call_id"),
        )

    def to_openai_dict(self) -> dict[str, Any]:
        """Convert to the OpenAI API message format.

        For ``role="assistant"`` with tool calls, produces the nested
        ``tool_calls`` structure expected by the API.  For ``role="tool"``,
        produces a tool-result message keyed by ``tool_call_id``.
        """
        if self.role == "assistant" and self.tool_calls:
            result = {
                "role": "assistant",
                "content": self.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": (
                                tc.arguments
                                if isinstance(tc.arguments, str)
                                else __import__("json").dumps(
                                    tc.arguments, ensure_ascii=False
                                )
                            ),
                        },
                    }
                    for tc in self.tool_calls
                ],
            }
            if self.reasoning_content is not None:
                result["reasoning_content"] = self.reasoning_content
            return result
        if self.role == "tool":
            return {
                "role": "tool",
                "content": self.content,
                "tool_call_id": self.tool_call_id or "",
            }
        result = {"role": self.role, "content": self.content}
        if self.role == "assistant" and self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        return result
