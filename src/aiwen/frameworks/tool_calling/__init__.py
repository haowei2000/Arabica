"""Custom tool calling architecture (Function Calling + Prompt Calling).

Provides a strategy-based abstraction over how tools are presented to
the LLM and how tool calls are parsed from its responses.
"""

from aiwen.frameworks.tool_calling.function_calling import FunctionCallingStrategy
from aiwen.frameworks.tool_calling.models import (
    ChatMessage,
    LLMResponse,
    ToolCallRequest,
)
from aiwen.frameworks.tool_calling.prompt_calling import PromptCallingStrategy
from aiwen.frameworks.tool_calling.strategy import ToolCallingStrategy

__all__ = [
    "ChatMessage",
    "FunctionCallingStrategy",
    "LLMResponse",
    "PromptCallingStrategy",
    "ToolCallRequest",
    "ToolCallingStrategy",
]
