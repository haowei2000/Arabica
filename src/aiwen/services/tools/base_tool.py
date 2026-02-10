"""
Base Tool Class System

Provides a unified tool interface with support for multiple execution modes
and automatic JSON Schema generation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, TypeVar

import logging

from pydantic import BaseModel, Field

T = TypeVar("T", bound="BaseTool")

logger = logging.getLogger(__name__)


class ToolExecutionMode(str, Enum):
    """Tool execution modes"""

    HTTP = "http"  # HTTP API call
    SERVER_RUN = "server_run"  # Server-side direct execution
    CLIENT_RUN = "client_run"  # Client-side execution
    CONTAINER_RUN = "container_run"  # Container isolated execution
    CELERY_RUN = "celery_run"  # Celery async task


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
    execution_mode: ToolExecutionMode = ToolExecutionMode.SERVER_RUN
    timeout: int = 30  # Timeout in seconds

    # Execution mode specific configurations
    http_config: HTTPConfig | None = None
    celery_config: CeleryConfig | None = None
    container_config: ContainerConfig | None = None
    client_config: ClientConfig | None = None


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
                execution_mode=ToolExecutionMode.SERVER_RUN
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

        metadata = cls.METADATA

        # Validate required configuration based on execution mode
        if metadata.execution_mode == ToolExecutionMode.HTTP:
            if not metadata.http_config or not metadata.http_config.url:
                raise ValueError(f"HTTP tool {cls.__name__} must provide http_config with url")

        elif metadata.execution_mode == ToolExecutionMode.CONTAINER_RUN:
            if not metadata.container_config or not metadata.container_config.image:
                raise ValueError(f"Container tool {cls.__name__} must provide container_config")

        elif metadata.execution_mode == ToolExecutionMode.CLIENT_RUN:
            if not metadata.client_config or not metadata.client_config.handler_name:
                raise ValueError(f"Client tool {cls.__name__} must provide client_config")

        elif metadata.execution_mode == ToolExecutionMode.CELERY_RUN:
            if not metadata.celery_config:
                metadata.celery_config = CeleryConfig()

    @abstractmethod
    async def execute(self, input_data:Any) -> Any:
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
            dict: OpenAI function calling 格式的 JSON Schema
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

    @classmethod
    def get_execution_mode(cls) -> ToolExecutionMode:
        """Get tool execution mode"""
        return cls.METADATA.execution_mode

    async def before_execute(self, input_data: ToolInputSchema) -> None:  # noqa: B027
        """
        Pre-execution hook method (optional)

        Can be used for:
        - Permission check
        - Resource pre-allocation
        - Logging
        - Parameter preprocessing

        Args:
            input_data: Validated input parameters
        """
        pass

    async def after_execute(  # noqa: B027
        self, input_data: ToolInputSchema, output: ToolOutputSchema
    ) -> None:
        """
        Post-execution hook method (optional)

        Can be used for:
        - Clean up resources
        - 记录日志
        - Send notifications
        - Update statistics

        Args:
            input_data: Input parameters
            output: Execution result
        """
        pass

    async def on_error(
        self, input_data: ToolInputSchema, error: Exception
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
        Make tool instance callable

        This is the unified entry point for tool execution, which handles:
        1. Input validation
        2. 执行前钩子
        3. Core execution
        4. 执行后钩子
        5. Error handling
        6. Output formatting

        Args:
            **kwargs: Tool parameters

        Returns:
            dict: 格式化后的Execution result
        """
        try:
            # 1. 验证输入
            input_data = await self.validate_input(kwargs)

            # 2. 执行前钩子
            await self.before_execute(input_data)

            # 3. Core execution
            output = await self.execute(input_data)

            # 4. 执行后钩子
            await self.after_execute(input_data, output)

            # 5. 格式化输出
            return self.format_output(output)

        except Exception as e:
            # Error handling
            error_output = await self.on_error(
                input_data if "input_data" in locals() else None, e  # type: ignore
            )
            return self.format_output(error_output)

    def __repr__(self) -> str:
        """String representation"""
        return (
            f"<{self.__class__.__name__}("
            f"name='{self.METADATA.name}', "
            f"mode={self.METADATA.execution_mode.value})>"
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
                execution_mode=ToolExecutionMode.SERVER_RUN,
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

        The returned dict describes how to create an ExternalTool that
        delegates execution to this InnerTool. It contains:
        - id: Template identifier (same as the InnerTool name)
        - name/description: Human-readable info
        - inner_tool_name: The InnerTool to delegate to
        - template: A pre-filled UserToolCreate body

        The template's ``input_schema`` mirrors the InnerTool's InputSchema
        so the ExternalTool's parameters map 1:1 to the InnerTool's parameters.

        Subclasses can override this method to provide more specific templates
        (e.g., with example values, curated descriptions, etc.).

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
            "execution_mode": metadata.execution_mode.value,
            "inner_tool_name": metadata.name,
            "category": metadata.category,
            "tags": metadata.tags,
            "template": {
                "name": f"my_{metadata.name}",
                "display_name": f"My {metadata.display_name}",
                "description": metadata.description,
                "execution_mode": metadata.execution_mode.value,
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

    ExternalTools are created dynamically from user definitions stored in the
    database (via DynamicToolLoader). They do not contain execution logic
    themselves — instead, they delegate to a registered InnerTool.

    Class-level attributes (set by DynamicToolLoader when creating subclasses):
        inner_tool_name: Name of the InnerTool to delegate to (looked up in ToolRegistry)
        parameter_mapping: Maps external field names to inner tool field names
        extra_params: Static parameters always passed to the inner tool (e.g., code string)

    Example:
        ```python
        class UserWeatherTool(ExternalTool):
            METADATA = ToolMetadata(name="user_weather", ...)
            inner_tool_name = "http_request"
            parameter_mapping = {"city": "body.city"}
            extra_params = {"url": "https://api.weather.com", "method": "GET"}
        ```
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

        1. Look up the inner tool from the registry
        2. Map external parameters to inner tool parameters
        3. Merge in extra static parameters
        4. Call the inner tool and return its result
        """
        # Lazy import to avoid circular dependency
        from aiwen.services.tools.tool_registry import ToolRegistry

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
            # inner_tool.__call__ returns a dict (format_output result)
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
                error=f"Delegation to inner tool failed: {str(e)}",
            )
