"""Agent tools with execution mode support.

This module provides:
- Browser automation tools (BROWSER_TOOLS)
- Server tools for context queries and file operations (SERVER_TOOLS)
- Execution mode system for sandbox/client/server tool execution
- Decorators for marking tools with execution modes
"""

from .browser_tools import BROWSER_TOOLS
from .client_executor import ClientExecutor, get_client_executor
from .async_executor import AsyncExecutor, get_async_executor
from .execution_mode import (
    ResourceLimits,
    ToolExecutionMode,
    ToolMetadata,
    async_tool,
    client_tool,
    get_tool_metadata,
    list_tools_by_mode,
    register_tool_metadata,
    sandbox_tool,
    server_tool,
)
from .execution_router import ExecutionRouter, get_execution_router
from .sandbox_executor import SandboxExecutor
from .server_tools import (
    CONTEXT_TOOLS,
    FILE_TOOLS,
    SERVER_TOOLS,
    UTILITY_TOOLS,
)

__all__ = [
    # Browser tools
    "BROWSER_TOOLS",
    # Server tools
    "SERVER_TOOLS",
    "CONTEXT_TOOLS",
    "FILE_TOOLS",
    "UTILITY_TOOLS",
    # Execution modes
    "ToolExecutionMode",
    "ToolMetadata",
    "ResourceLimits",
    # Decorators
    "sandbox_tool",
    "client_tool",
    "server_tool",
    "async_tool",
    # Metadata functions
    "get_tool_metadata",
    "register_tool_metadata",
    "list_tools_by_mode",
    # Router
    "ExecutionRouter",
    "get_execution_router",
    # Executors
    "SandboxExecutor",
    "ClientExecutor",
    "get_client_executor",
    "AsyncExecutor",
    "get_async_executor",
]
