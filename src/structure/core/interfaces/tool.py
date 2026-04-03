"""
Base Tool Class System

Provides a unified tool interface with automatic JSON Schema generation.
All tools run through the same ``execute()`` / ``__call__()`` protocol.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import time
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, Field

from structure.core.interfaces.protocols import ToolProtocol

T = TypeVar("T", bound="BaseTool")


class ToolControlFlow(Exception):
    """Base class for control-flow exceptions raised inside tool ``execute()``.

    Subclasses are used as signals (not real errors): ``BaseTool.__call__``
    re-raises them so they bypass the ``on_error`` handler and propagate to
    the caller (e.g. ``tool_handler``) that knows how to act on them.

    Current subclasses:
      - ``WaitingForUserInput`` — pause execution, ask the user a question.
    """


logger = logging.getLogger(__name__)


@dataclass
class ToolMetadata:
    """Tool metadata"""

    name: str  # Tool name (unique identifiera
    display_name: str  # Display name
    description: str  # Tool description
    version: str = "1.0.0"  # Version number
    author: str = "Structure"  # Author
    tags: list[str] = field(default_factory=list)  # Tags
    category: str = "general"  # Category
    enabled: bool = True  # Whether enabled
    timeout: int = 30  # Timeout in seconds
    always_load: bool = (
        False  # Always include this tool regardless of XML tag selection
    )


class ToolInputSchema(BaseModel):
    """Base class for tool input parameters (auto-generate schema using Pydantic)"""

    pass


class ToolOutputSchema(BaseModel):
    """Base class for tool output results"""

    success: bool = Field(description="Whether execution was successful")
    message: str | None = Field(default=None, description="Result message")
    data: dict[str, Any] | None = Field(default=None, description="Return data")
    error: str | None = Field(default=None, description="Error information")


class BaseTool(ABC, ToolProtocol):
    """
    Base Tool Class

    All tools should inherit from this base class and implement the required methods.

    Example:
        ```python
        class SearchTool(BaseTool):
            METADATA = ToolMetadata(
                name="search",
                display_name="Search Tool",
                description="Search content in knowledge base",
            )

            class InputSchema(ToolInputSchema):
                query: str = Field(description="Search query")
                limit: int = Field(default=10, description="Maximum number of results")

            async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
                # Implement search logic
                results = await search_knowledge_base(input_data.query, input_data.limit)
                return ToolOutputSchema(
                    success=True,
                    message=f"Found {len(results)} results",
                    data={"results": results}
                )
        ```
    """

    # Tool metadata (subclass must define)
    METADATA: ClassVar[ToolMetadata]

    # Input parameter schema (subclass must define)
    InputSchema: ClassVar[type[ToolInputSchema]] = ToolInputSchema

    # Output result schema (subclass can customize)
    OutputSchema: ClassVar[type[ToolOutputSchema]] = ToolOutputSchema

    def __init__(self):
        """Initialize tool"""
        self._validate_metadata()

    @classmethod
    def _validate_metadata(cls):
        """Validate that tool metadata is complete"""
        if not hasattr(cls, "METADATA"):
            raise ValueError(f"Tool {cls.__name__} must define METADATA")

    @abstractmethod
    async def execute(self, input_data: Any) -> Any:
        """
        Core method to execute the tool

        Args:
            input_data: Tool input parameters (validated)

        Returns:
            ToolOutputSchema: Tool execution result

        Raises:
            Exception: Any exception during execution
        """
        pass

    async def validate_input(self, raw_input: dict[str, Any]) -> ToolInputSchema:
        """
        Validate and transform input parameters

        Args:
            raw_input: Raw input dictionary

        Returns:
            ToolInputSchema: Validated input object

        Raises:
            ValidationError: Input parameters do not conform to schema
        """
        # Clean up empty strings and parse JSON strings
        import json

        cleaned_input = {}
        for key, value in raw_input.items():
            if value == "" or value is None:
                # Empty string or None - skip it, let Pydantic use field defaults
                continue
            if isinstance(value, str):
                # Try to parse JSON strings for complex types (dict/list)
                stripped = value.strip()
                if stripped and stripped[0] in ("{", "["):
                    try:
                        cleaned_input[key] = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError) as e:
                        # Try lenient JSON parsing (quote unquoted values)
                        try:
                            # Attempt to fix common JSON errors
                            import re

                            # Add quotes to unquoted string values
                            # Pattern: :word (not already quoted, not a number/bool/null)
                            fixed = re.sub(
                                r":(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*([,}\]])",
                                r':"\2"\3',
                                stripped,
                            )
                            cleaned_input[key] = json.loads(fixed)
                        except (json.JSONDecodeError, ValueError):
                            # Still failed - log warning and keep as string
                            import logging

                            logger = logging.getLogger(__name__)
                            logger.warning(
                                f"Failed to parse JSON for parameter '{key}': {e}\n"
                                f"Value: {value[:200]}"
                            )
                            cleaned_input[key] = value
                else:
                    cleaned_input[key] = value
            else:
                cleaned_input[key] = value

        return self.InputSchema(**cleaned_input)

    def format_output(self, output: ToolOutputSchema) -> dict[str, Any]:
        """
        Format output result as dictionary

        Args:
            output: Tool output object

        Returns:
            dict: Formatted output dictionary
        """
        return output.model_dump()

    @classmethod
    def get_json_schema(cls) -> dict[str, Any]:
        """
        Get tool JSON Schema (for Agent)

        Returns:
            dict: OpenAI function calling format JSON Schema
        """
        from structure.frameworks.tool_calling.models import (
            OpenAIFunction,
            OpenAIFunctionParameters,
            OpenAITool,
        )

        metadata = cls.METADATA
        input_schema = cls.InputSchema.model_json_schema()

        return OpenAITool(
            function=OpenAIFunction(
                name=metadata.name,
                description=metadata.description,
                parameters=OpenAIFunctionParameters(
                    properties=input_schema.get("properties", {}),
                    required=input_schema.get("required", []),
                ),
            )
        ).model_dump(exclude_none=True)

    @classmethod
    def get_langchain_schema(cls) -> dict[str, Any]:
        """
        Get schema in LangChain tool format

        Returns:
            dict: LangChain Tool definition
        """
        metadata = cls.METADATA

        return {
            "name": metadata.name,
            "description": metadata.description,
            "args_schema": cls.InputSchema,
        }

    @classmethod
    def get_metadata(cls) -> ToolMetadata:
        """Get tool metadata"""
        return cls.METADATA

    async def before_execute(self, input_data: ToolInputSchema) -> None:
        """
        Pre-execution hook method (optional)

        Args:
            input_data: Validated input parameters
        """
        pass

    async def after_execute(
        self, input_data: ToolInputSchema, output: ToolOutputSchema
    ) -> None:
        """
        Post-execution hook method (optional)

        Args:
            input_data: Input parameters
            output: Execution result
        """
        pass

    async def on_error(
        self,
        input_data: ToolInputSchema | None,  # noqa: ARG002
        error: Exception,
    ) -> ToolOutputSchema:
        """
        Error handling hook (optional)

        Args:
            input_data: Input parameters
            error: Captured exception

        Returns:
            ToolOutputSchema: Error result
        """
        return self.OutputSchema(
            success=False, message="Tool execution failed", error=str(error)
        )

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """
        Unified entry point for tool execution.

        Handles input validation, lifecycle hooks, core execution,
        error handling, and output formatting.

        Args:
            **kwargs: Tool parameters

        Returns:
            dict: Formatted execution result
        """
        tool_name = self.METADATA.name
        logger.info("Tool call started: %s | args: %s", tool_name, kwargs)
        start_time = time.monotonic()

        import asyncio as _asyncio

        timeout_s = getattr(self.METADATA, "timeout", 60) or 60

        input_data = None
        try:
            input_data = await self.validate_input(kwargs)
            await self.before_execute(input_data)
            output = await _asyncio.wait_for(
                self.execute(input_data), timeout=timeout_s
            )
            await self.after_execute(input_data, output)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            result = self.format_output(output)
            logger.info(
                "Tool call succeeded: %s | %.1fms | success=%s",
                tool_name,
                elapsed_ms,
                result.get("success"),
            )
            return result

        except ToolControlFlow:
            raise  # Control-flow signals (e.g. WaitingForUserInput) bypass on_error

        except TimeoutError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Tool call timed out: %s | %.1fms | timeout=%ss",
                tool_name,
                elapsed_ms,
                timeout_s,
            )
            error_output = await self.on_error(
                input_data,
                TimeoutError(f"Tool '{tool_name}' timed out after {timeout_s}s"),
            )
            return self.format_output(error_output)

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Tool call failed: %s | %.1fms | error: %s",
                tool_name,
                elapsed_ms,
                e,
                exc_info=True,
            )
            error_output = await self.on_error(input_data, e)
            return self.format_output(error_output)

    def __repr__(self) -> str:
        """String representation"""
        return f"<{self.__class__.__name__}(name='{self.METADATA.name}')>"


class InnerTool(BaseTool, ABC):
    """
    Inner Tool - Developer-defined tools implemented in code

    InnerTools are built-in tools that execute logic directly. They serve as
    the actual execution backends that ExternalTools can delegate to.

    All code-defined tools (server tools, browser tools, etc.) should inherit
    from this class instead of BaseTool directly.

    Example:
        ```python
        class MyServerTool(InnerTool):
            METADATA = ToolMetadata(
                name="my_tool",
                display_name="My Tool",
                description="A built-in tool",
            )

            class InputSchema(ToolInputSchema):
                query: str = Field(description="Search query")

            async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
                return ToolOutputSchema(success=True, data={"result": "ok"})
        ```
    """

    tool_type: ClassVar[str] = "inner"
