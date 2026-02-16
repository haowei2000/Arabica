"""Abstract base class for tool calling strategies.

A ``ToolCallingStrategy`` encapsulates how tool schemas are presented to
the LLM and how tool calls are parsed from its response.  Two concrete
implementations are provided:

* ``FunctionCallingStrategy`` – uses the OpenAI ``tools`` API parameter.
* ``PromptCallingStrategy`` – embeds tool descriptions in the system
  prompt and parses ``<tool_call>`` XML blocks from text output.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from aiwen.frameworks.tool_calling.models import ChatMessage, LLMResponse


class ToolCallingStrategy(ABC):
    """Strategy interface for tool calling."""

    @abstractmethod
    def format_tools(self, tool_classes: list[type]) -> Any:
        """Convert tool classes into a format suitable for this strategy.

        Args:
            tool_classes: List of ``BaseTool`` subclasses.

        Returns:
            Strategy-specific tool information (e.g. list of JSON schemas
            or a text description block).
        """

    @abstractmethod
    async def call_llm(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
    ) -> LLMResponse:
        """Call the LLM and return a unified response.

        Args:
            messages: Conversation messages.
            tools_info: Output of :meth:`format_tools`.
            model: Model name.
            api_key: API key for authentication.
            base_url: Base URL for the API endpoint.

        Returns:
            Unified ``LLMResponse``.
        """

    @abstractmethod
    async def call_llm_stream(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        """Stream LLM response, yielding text chunks and a final LLMResponse.

        Yields:
            ``str`` for incremental text tokens.
            A final ``LLMResponse`` as the last yielded value, containing
            the complete content and any parsed tool calls.

        Args:
            messages: Conversation messages.
            tools_info: Output of :meth:`format_tools`.
            model: Model name.
            api_key: API key for authentication.
            base_url: Base URL for the API endpoint.
        """
        # async generator must contain at least one yield
        yield  # type: ignore[misc]
