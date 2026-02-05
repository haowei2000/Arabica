"""
Tool execution mode definitions and decorators.

This module provides the foundation for executing tools in different environments:
- SERVER: Default in-process execution
- SANDBOX: Isolated Docker container execution
- CLIENT: Browser-side execution via WebSocket/SSE
"""

from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Global registry for tool metadata
_tool_metadata_registry: dict[str, "ToolMetadata"] = {}


class ToolExecutionMode(str, Enum):
    """Execution environment for a tool."""

    SERVER = "server"  # Default, in-process execution
    SANDBOX = "sandbox"  # Docker container isolation
    CLIENT = "client"  # Browser via WebSocket/SSE
    ASYNC = "async"  # Background task via Celery


@dataclass
class ResourceLimits:
    """Resource constraints for sandbox execution."""

    memory: str = "256m"  # Docker memory limit (e.g., "256m", "1g")
    cpu_period: int = 100000  # CPU period in microseconds
    cpu_quota: int = 50000  # CPU quota (50% of one core by default)
    network_disabled: bool = True  # Disable network access
    read_only_rootfs: bool = True  # Read-only root filesystem
    pids_limit: int = 100  # Maximum number of processes


@dataclass
class ToolMetadata:
    """Metadata defining how a tool should be executed."""

    execution_mode: ToolExecutionMode = ToolExecutionMode.SERVER
    timeout_seconds: int = 30

    # Sandbox-specific options
    sandbox_image: str | None = None
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    sandbox_workdir: str = "/workspace"

    # Client-specific options
    client_handler: str | None = None  # Frontend handler name (e.g., "filePicker")
    client_config: dict[str, Any] = field(default_factory=dict)

    # Async-specific options (Celery background tasks)
    celery_queue: str = "default"  # Celery queue name
    celery_priority: int = 5  # Task priority (0-9, lower = higher priority)
    progress_enabled: bool = True  # Whether to emit progress events
    retry_on_failure: bool = True  # Auto-retry on failure
    max_retries: int = 3  # Maximum retry attempts


def get_tool_metadata(tool_name: str) -> ToolMetadata:
    """Get metadata for a tool by name."""
    return _tool_metadata_registry.get(tool_name, ToolMetadata())


def register_tool_metadata(tool_name: str, metadata: ToolMetadata) -> None:
    """Register metadata for a tool."""
    _tool_metadata_registry[tool_name] = metadata


def list_tools_by_mode(mode: ToolExecutionMode) -> list[str]:
    """List all tool names registered with a specific execution mode."""
    return [
        name
        for name, meta in _tool_metadata_registry.items()
        if meta.execution_mode == mode
    ]


def sandbox_tool(
    image: str = "python:3.12-slim",
    memory: str = "256m",
    timeout: int = 60,
    network: bool = False,
    cpu_percent: int = 50,
) -> Callable[[F], F]:
    """
    Decorator to mark a tool for sandbox (Docker) execution.

    Args:
        image: Docker image to use for execution
        memory: Memory limit (e.g., "256m", "1g")
        timeout: Execution timeout in seconds
        network: Whether to allow network access
        cpu_percent: CPU usage limit as percentage of one core

    Example:
        @tool("execute_python")
        @sandbox_tool(image="python:3.12-slim", memory="512m", timeout=120)
        async def execute_python(code: str) -> dict:
            '''Execute Python code safely in sandbox.'''
            pass
    """

    def decorator(func: F) -> F:
        tool_name = getattr(func, "name", func.__name__)

        resource_limits = ResourceLimits(
            memory=memory,
            cpu_quota=cpu_percent * 1000,  # Convert percentage to quota
            network_disabled=not network,
        )

        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SANDBOX,
            timeout_seconds=timeout,
            sandbox_image=image,
            resource_limits=resource_limits,
        )

        register_tool_metadata(tool_name, metadata)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # The actual execution is handled by SandboxExecutor
            # This wrapper exists for metadata attachment
            return await func(*args, **kwargs)

        # Preserve LangChain tool attributes
        for attr in ("name", "description", "args_schema"):
            if hasattr(func, attr):
                setattr(wrapper, attr, getattr(func, attr))

        return wrapper  # type: ignore

    return decorator


def client_tool(
    handler: str,
    timeout: int = 120,
    config: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Decorator to mark a tool for client (browser) execution.

    Args:
        handler: Frontend handler name (e.g., "filePicker", "cameraCapture")
        timeout: Time to wait for client response in seconds
        config: Additional configuration passed to the frontend handler

    Example:
        @tool("select_file")
        @client_tool(handler="filePicker", timeout=120)
        async def select_file(
            allowed_extensions: list[str] = [".pdf", ".txt"],
            multiple: bool = False,
        ) -> dict:
            '''Open file picker in user's browser.'''
            pass
    """

    def decorator(func: F) -> F:
        tool_name = getattr(func, "name", func.__name__)

        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.CLIENT,
            timeout_seconds=timeout,
            client_handler=handler,
            client_config=config or {},
        )

        register_tool_metadata(tool_name, metadata)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # The actual execution is handled by ClientExecutor
            # This wrapper exists for metadata attachment
            return await func(*args, **kwargs)

        # Preserve LangChain tool attributes
        for attr in ("name", "description", "args_schema"):
            if hasattr(func, attr):
                setattr(wrapper, attr, getattr(func, attr))

        return wrapper  # type: ignore

    return decorator


def server_tool(timeout: int = 30) -> Callable[[F], F]:
    """
    Decorator to explicitly mark a tool for server execution.

    This is optional since SERVER is the default mode, but can be used
    for explicit documentation or to set a custom timeout.

    Args:
        timeout: Execution timeout in seconds

    Example:
        @tool("database_query")
        @server_tool(timeout=60)
        async def database_query(sql: str) -> dict:
            '''Execute a database query.'''
            pass
    """

    def decorator(func: F) -> F:
        tool_name = getattr(func, "name", func.__name__)

        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SERVER,
            timeout_seconds=timeout,
        )

        register_tool_metadata(tool_name, metadata)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        # Preserve LangChain tool attributes
        for attr in ("name", "description", "args_schema"):
            if hasattr(func, attr):
                setattr(wrapper, attr, getattr(func, attr))

        return wrapper  # type: ignore

    return decorator


def async_tool(
    queue: str = "default",
    timeout: int = 3600,
    priority: int = 5,
    progress: bool = True,
    retry: bool = True,
    max_retries: int = 3,
) -> Callable[[F], F]:
    """
    Decorator to mark a tool for async (Celery) execution.

    Use this for long-running tasks that should run in the background.
    Progress updates are sent via SSE events.

    Args:
        queue: Celery queue name (e.g., "default", "high_priority", "slow")
        timeout: Maximum execution time in seconds (default: 1 hour)
        priority: Task priority 0-9, lower = higher priority
        progress: Whether to emit progress events during execution
        retry: Whether to retry on failure
        max_retries: Maximum retry attempts

    Example:
        @tool("generate_report")
        @async_tool(queue="slow", timeout=1800, progress=True)
        async def generate_report(report_type: str, date_range: dict) -> dict:
            '''Generate a detailed report. May take several minutes.'''
            pass
    """

    def decorator(func: F) -> F:
        tool_name = getattr(func, "name", func.__name__)

        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.ASYNC,
            timeout_seconds=timeout,
            celery_queue=queue,
            celery_priority=priority,
            progress_enabled=progress,
            retry_on_failure=retry,
            max_retries=max_retries,
        )

        register_tool_metadata(tool_name, metadata)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # The actual execution is handled by AsyncExecutor
            # This wrapper exists for metadata attachment
            return await func(*args, **kwargs)

        # Preserve LangChain tool attributes
        for attr in ("name", "description", "args_schema"):
            if hasattr(func, attr):
                setattr(wrapper, attr, getattr(func, attr))

        return wrapper  # type: ignore

    return decorator
