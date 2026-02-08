"""
Base Tool Class System

Provides a unified tool interface with support for multiple execution modes
and automatic JSON Schema generation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound="BaseTool")


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
    async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:
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

    async def before_execute(self, input_data: ToolInputSchema) -> None:
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

    async def after_execute(
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
