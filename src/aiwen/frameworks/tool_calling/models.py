"""Data models for the custom tool calling architecture.

Provides simple, JSON-serializable dataclasses that replace LangChain's
``AIMessage`` / ``ToolMessage`` types in the executor layer.
"""

from dataclasses import dataclass, field
from typing import Any


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
        raw: Raw API response object for debugging.
    """

    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    raw: Any = None


@dataclass
class ChatMessage:
    """Simple message type replacing LangChain's AIMessage/ToolMessage.

    Attributes:
        role: One of ``"system"``, ``"user"``, ``"assistant"``, ``"tool"``.
        content: Text content of the message.
        tool_calls: Parsed tool calls (only for ``role="assistant"``).
        tool_call_id: The tool call ID this message responds to
            (only for ``role="tool"``).
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCallRequest] | None = None
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
            tool_call_id=data.get("tool_call_id"),
        )

    def to_openai_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API message format.

        For ``role="assistant"`` with tool calls, produces the nested
        ``tool_calls`` structure expected by the API.  For ``role="tool"``,
        produces a tool-result message keyed by ``tool_call_id``.
        """
        if self.role == "assistant" and self.tool_calls:
            return {
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
        if self.role == "tool":
            return {
                "role": "tool",
                "content": self.content,
                "tool_call_id": self.tool_call_id or "",
            }
        return {"role": self.role, "content": self.content}
