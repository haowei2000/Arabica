"""
Base Tool Class System

Provides a unified tool interface with automatic JSON Schema generation.
All tools run through the same ``execute()`` / ``__call__()`` protocol.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, Field

from aiwen.core.interfaces.protocols import ToolProtocol

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
    author: str = "Aiwen"  # Author
    tags: list[str] = field(default_factory=list)  # Tags
    category: str = "general"  # Category
    enabled: bool = True  # Whether enabled
    timeout: int = 30  # Timeout in seconds

class ToolInputSchema(BaseModel):
    """Base class for tool input parameters (auto-generate schema using Pydantic)"""

    pass

class ToolOutputSchema(BaseModel):
    """Base class for tool output results"""

    success: bool = Field(description="Whether execution was successful")
    message: str | None = Field(default=None, description="Result message")
    data: dict[str, Any] | None = Field(default=None, description="Return data")
    error: str | None = Field(default=None, description="Error information")

class BaseTool(ABC,ToolProtocol):
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
            if value == '' or value is None:
                # Empty string or None - skip it, let Pydantic use field defaults
                continue
            elif isinstance(value, str):
                # Try to parse JSON strings for complex types (dict/list)
                stripped = value.strip()
                if stripped and stripped[0] in ('{', '['):
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
                                r':(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*([,}\]])',
                                r':"\2"\3',
                                stripped
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
        metadata = cls.METADATA
        input_schema = cls.InputSchema.model_json_schema()

        # Convert to OpenAI function calling format
        return {
            "type": "function",
            "function": {
                "name": metadata.name,
                "description": metadata.description,
                "parameters": {
                    "type": "object",
                    "properties": input_schema.get("properties", {}),
                    "required": input_schema.get("required", []),
                },
            },
        }

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

    async def before_execute(self, input_data: ToolInputSchema) -> None:  # noqa: B027
        """
        Pre-execution hook method (optional)

        Args:
            input_data: Validated input parameters
        """
        pass

    async def after_execute(  # noqa: B027
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
        self, input_data: ToolInputSchema | None, error: Exception
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

        input_data = None
        try:
            input_data = await self.validate_input(kwargs)
            await self.before_execute(input_data)
            output = await self.execute(input_data)
            await self.after_execute(input_data, output)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            result = self.format_output(output)
            logger.info(
                "Tool call succeeded: %s | %.1fms | success=%s",
                tool_name, elapsed_ms, result.get("success"),
            )
            return result

        except ToolControlFlow:
            raise  # Control-flow signals (e.g. WaitingForUserInput) bypass on_error

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Tool call failed: %s | %.1fms | error: %s",
                tool_name, elapsed_ms, e,
                exc_info=True,
            )
            error_output = await self.on_error(input_data, e)
            return self.format_output(error_output)

    def __repr__(self) -> str:
        """String representation"""
        return (
            f"<{self.__class__.__name__}("
            f"name='{self.METADATA.name}')>"
        )

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

    @classmethod
    def to_template(cls) -> dict[str, Any]:
        """
        Generate a tool creation template from this InnerTool.

        Returns:
            dict with template data (can be wrapped in ToolTemplate schema)
        """
        metadata = cls.METADATA
        schema = cls.InputSchema.model_json_schema()

        # Build parameter_mapping: 1:1 from each field to itself
        properties = schema.get("properties", {})
        parameter_mapping = {field: field for field in properties}

        return {
            "id": f"inner_{metadata.name}",
            "name": metadata.display_name,
            "description": (
                f"Create an external tool that delegates to the "
                f"built-in '{metadata.display_name}' tool"
            ),
            "inner_tool_name": metadata.name,
            "category": metadata.category,
            "tags": metadata.tags,
            "template": {
                "name": f"my_{metadata.name}",
                "display_name": f"My {metadata.display_name}",
                "description": metadata.description,
                "inner_tool_name": metadata.name,
                "parameter_mapping": parameter_mapping,
                "category": metadata.category,
                "tags": metadata.tags,
                "timeout": metadata.timeout,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": schema.get("required", []),
                },
            },
        }

@dataclass
class ChainStep:
    """A single step in a tool execution chain.

    Each step calls a registered tool with mapped parameters.
    For the first step, parameters come from the ExternalTool's input.
    For subsequent steps, parameters come from the previous step's
    ``data`` output dict.

    Attributes:
        tool_name: Name of the registered tool to call.
        parameter_mapping: ``{source_key: target_param}``.
            Supports dotted paths for nested access (e.g. ``"result.items"``).
            If empty, the entire input/output dict is passed as-is.
        extra_params: Static parameters merged into each call.
    """

    tool_name: str
    parameter_mapping: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, Any] = field(default_factory=dict)


def _resolve_key(data: dict[str, Any], dotted_key: str) -> Any:
    """Resolve a dotted key path against a dict.

    Supports nested dicts and integer list indices::

        _resolve_key({"a": {"b": [1, 2, 3]}}, "a.b.1")  # => 2

    Returns ``None`` if any segment is missing.
    """
    current: Any = data
    for segment in dotted_key.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _map_params(
    source: dict[str, Any],
    mapping: dict[str, str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build target params from *source* using *mapping*.

    If *mapping* is empty, ``source`` is returned directly (pass-through).
    Dotted source keys are resolved via :func:`_resolve_key`.
    *extra* static params are merged last (highest priority).
    """
    if not mapping:
        params = dict(source)
    else:
        params: dict[str, Any] = {}
        for src_key, dst_key in mapping.items():
            value = _resolve_key(source, src_key)
            if value is not None:
                params[dst_key] = value
    if extra:
        params.update(extra)
    return params


_EXPR_VAR_RE = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")


def _is_expression_mapping(mapping: dict[str, str]) -> bool:
    """Detect whether mapping uses expression format ``{target: expression}``.

    Expression format values contain ``{{var}}`` placeholders.
    Legacy format values are plain target-param names without braces.
    """
    return any("{{" in str(v) for v in mapping.values())


def _resolve_expression_mapping(
    source: dict[str, Any],
    mapping: dict[str, str],
) -> dict[str, Any]:
    """Resolve expression-based mapping ``{target_param: expression}``.

    Supported expression forms:
    - ``"{{city}}"`` — pure variable reference, preserves original type
    - ``"prefix_{{city}}_suffix"`` — template interpolation, result is str
    - ``"static_value"`` — literal string with no ``{{}}`` markers
    """
    params: dict[str, Any] = {}
    for target_key, expression in mapping.items():
        expr_str = str(expression)

        # Pure variable reference — preserve original type
        pure_match = _EXPR_VAR_RE.fullmatch(expr_str)
        if pure_match:
            value = _resolve_key(source, pure_match.group(1))
            if value is not None:
                params[target_key] = value
            continue

        # Template with embedded variables — string interpolation
        if "{{" in expr_str:
            def _replace(m: re.Match) -> str:
                val = _resolve_key(source, m.group(1))
                return str(val) if val is not None else m.group(0)

            params[target_key] = _EXPR_VAR_RE.sub(_replace, expr_str)
            continue

        # Static literal — pass through as-is
        params[target_key] = expression

    return params


class ExternalTool(BaseTool):
    """
    External Tool - User-designed tools that delegate execution to other tools.

    All external tools use mapping-based delegation to registered InnerTools.

    Supports two execution modes (evaluated in priority order):

    1. **Chain execution** (pipeline): set ``chain`` — a list of
       :class:`ChainStep` executed sequentially.  The output ``data`` dict
       of step *N* becomes the input source for step *N+1*.
    2. **Single delegation**: set ``inner_tool_name`` + ``parameter_mapping``
       + ``extra_params`` to delegate to a registered InnerTool.

    Class-level attributes (set by DynamicToolLoader when creating subclasses):
        inner_tool_name: Name of the tool to delegate to (single mode).
        parameter_mapping: Maps external field names to tool param names.
        extra_params: Static parameters always passed to the tool.
        chain: Ordered list of :class:`ChainStep` for pipeline mode.
    """

    tool_type: ClassVar[str] = "external"

    # ── Single-delegation mode ──
    inner_tool_name: ClassVar[str] = ""
    parameter_mapping: ClassVar[dict[str, str]] = {}
    extra_params: ClassVar[dict[str, Any]] = {}

    # ── Chain / pipeline mode ──
    chain: ClassVar[list[ChainStep]] = []

    async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        if self.chain:
            return await self._execute_chain(input_data)
        if self.inner_tool_name:
            return await self._execute_single(input_data)
        return ToolOutputSchema(
            success=False,
            error="No execution target: set 'inner_tool_name' or 'chain'",
        )

    # ── Single delegation (backward-compatible) ──────────────────────

    async def _execute_single(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        from aiwen.registries.core import ToolRegistry

        tool_instance = ToolRegistry.get_tool_instance(self.inner_tool_name)
        if not tool_instance:
            return ToolOutputSchema(
                success=False,
                error=f"Tool '{self.inner_tool_name}' not found in registry",
            )

        input_dict = input_data.model_dump()

        # Expression mapping: {target_param: "{{input_var}}" | "static"}
        # Legacy mapping:     {src_param: target_param}
        if self.parameter_mapping and _is_expression_mapping(self.parameter_mapping):
            params = _resolve_expression_mapping(input_dict, self.parameter_mapping)
        else:
            params = _map_params(input_dict, self.parameter_mapping, self.extra_params)

        try:
            result = await tool_instance(**params)
            return ToolOutputSchema(
                success=result.get("success", False),
                message=result.get("message"),
                data=result.get("data"),
                error=result.get("error"),
            )
        except Exception as e:
            logger.error(
                f"ExternalTool '{self.METADATA.name}' → "
                f"'{self.inner_tool_name}' failed: {e}",
                exc_info=True,
            )
            return ToolOutputSchema(
                success=False, error=f"Delegation failed: {e!s}"
            )

    # ── Chain / pipeline execution ───────────────────────────────────

    async def _execute_chain(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        """Run each :class:`ChainStep` in order, piping ``data`` forward."""
        from aiwen.registries.core import ToolRegistry

        current_data: dict[str, Any] = input_data.model_dump()

        for idx, step in enumerate(self.chain):
            tool_instance = ToolRegistry.get_tool_instance(step.tool_name)
            if not tool_instance:
                return ToolOutputSchema(
                    success=False,
                    error=(
                        f"Chain step {idx} failed: tool "
                        f"'{step.tool_name}' not found in registry"
                    ),
                )

            params = _map_params(current_data, step.parameter_mapping, step.extra_params)

            try:
                result = await tool_instance(**params)
            except Exception as e:
                logger.error(
                    f"ExternalTool '{self.METADATA.name}' chain step {idx} "
                    f"('{step.tool_name}') raised: {e}",
                    exc_info=True,
                )
                return ToolOutputSchema(
                    success=False,
                    error=f"Chain step {idx} ('{step.tool_name}') error: {e!s}",
                )

            if not result.get("success"):
                return ToolOutputSchema(
                    success=False,
                    error=(
                        f"Chain step {idx} ('{step.tool_name}') failed: "
                        f"{result.get('error', 'unknown error')}"
                    ),
                )

            # The step's output data becomes the next step's input.
            # Fall back to the full result dict if 'data' is absent.
            current_data = result.get("data") or result

        return ToolOutputSchema(success=True, data=current_data)

