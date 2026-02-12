"""
Base Tool Class System

Provides a unified tool interface with automatic JSON Schema generation.
All tools run through the same ``execute()`` / ``__call__()`` protocol.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound="BaseTool")

logger = logging.getLogger(__name__)

@dataclass
class ResourceLimits:
    """Resource limits configuration (for container execution)"""

    memory: str = "256m"  # Memory limit (e.g., "256m", "1g")
    cpu_quota: int = 50000  # CPU quota (microseconds)
    cpu_period: int = 100000  # CPU period (microseconds)
    network_enabled: bool = False  # Whether to allow network access
    read_only_rootfs: bool = True  # Read-only root filesystem
    pids_limit: int = 100  # Maximum number of processes

@dataclass
class HTTPConfig:
    """HTTP tool configuration"""

    method: str = "POST"  # HTTP method
    url: str = ""  # API endpoint URL
    headers: dict[str, str] = field(default_factory=dict)  # Request headers
    timeout: int = 30  # Timeout in seconds
    retry_times: int = 3  # Number of retries
    verify_ssl: bool = True  # Whether to verify SSL

@dataclass
class CeleryConfig:
    """Celery async task configuration"""

    queue: str = "default"  # Queue name
    priority: int = 5  # Priority (0-9, lower number = higher priority)
    progress_enabled: bool = True  # Whether to enable progress reporting
    retry_on_failure: bool = True  # Whether to retry on failure
    max_retries: int = 3  # Maximum retry attempts
    countdown: int = 0  # Delayed execution (seconds)

@dataclass
class ContainerConfig:
    """Container execution configuration"""

    image: str = "python:3.12-slim"  # Docker image
    workdir: str = "/workspace"  # Working directory
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    environment: dict[str, str] = field(default_factory=dict)  # Environment variables
    volumes: dict[str, str] = field(default_factory=dict)  # Volume mounts

@dataclass
class ClientConfig:
    """Client execution configuration"""

    handler_name: str = ""  # Frontend handler name
    config: dict[str, Any] = field(default_factory=dict)  # Additional configuration
    require_user_approval: bool = False  # Whether user approval is required

@dataclass
class ToolMetadata:
    """Tool metadata"""

    name: str  # Tool name (unique identifier)
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

class BaseTool(ABC):
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
        return self.InputSchema(**raw_input)

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
        input_data = None
        try:
            input_data = await self.validate_input(kwargs)
            await self.before_execute(input_data)
            output = await self.execute(input_data)
            await self.after_execute(input_data, output)
            return self.format_output(output)

        except Exception as e:
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

class ExternalTool(BaseTool):
    """
    External Tool - User-designed tools that delegate execution to an InnerTool

    ExternalTools do not contain execution logic themselves — instead, they
    delegate to a registered InnerTool via parameter mapping.

    Class-level attributes (set by DynamicToolLoader when creating subclasses):
        inner_tool_name: Name of the InnerTool to delegate to
        parameter_mapping: Maps external field names to inner tool field names
        extra_params: Static parameters always passed to the inner tool
    """

    tool_type: ClassVar[str] = "external"

    # Name of the InnerTool to delegate to (must be registered in ToolRegistry)
    inner_tool_name: ClassVar[str] = ""

    # Maps {external_param_name: inner_param_name}
    parameter_mapping: ClassVar[dict[str, str]] = {}

    # Static params always passed to the inner tool (e.g., code, url, headers)
    extra_params: ClassVar[dict[str, Any]] = {}

    async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        """
        Execute by delegating to the configured InnerTool
        """
        # Lazy import to avoid circular dependency
        from aiwen.registries import ToolRegistry

        inner_tool = ToolRegistry.get_tool_instance(self.inner_tool_name)
        if not inner_tool:
            return ToolOutputSchema(
                success=False,
                error=f"Inner tool '{self.inner_tool_name}' not found in registry",
            )

        # Build mapped parameters from input_data
        input_dict = input_data.model_dump()
        mapped_params: dict[str, Any] = {}

        if self.parameter_mapping:
            for ext_key, inner_key in self.parameter_mapping.items():
                if ext_key in input_dict:
                    mapped_params[inner_key] = input_dict[ext_key]
        else:
            # No explicit mapping — pass all input fields as 'input_data' dict
            mapped_params["input_data"] = input_dict

        # Merge in static extra params (e.g., code string, url, etc.)
        mapped_params.update(self.extra_params)

        try:
            result = await inner_tool(**mapped_params)
            return ToolOutputSchema(
                success=result.get("success", False),
                message=result.get("message"),
                data=result.get("data"),
                error=result.get("error"),
            )
        except Exception as e:
            logger.error(
                f"ExternalTool '{self.METADATA.name}' delegation to "
                f"'{self.inner_tool_name}' failed: {e}",
                exc_info=True,
            )
            return ToolOutputSchema(
                success=False,
                error=f"Delegation to inner tool failed: {e!s}",
            )
