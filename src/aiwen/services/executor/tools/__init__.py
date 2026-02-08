"""Agent tools with execution mode support.

This module provides:
- Browser automation tools (BROWSER_TOOLS)
- Server tools for context queries and file operations (SERVER_TOOLS)
- Execution mode system for sandbox/client/server tool execution
- Decorators for marking tools with execution modes
"""

from .async_executor import AsyncExecutor, get_async_executor
from .browser_tools import BROWSER_TOOLS
from .client_executor import ClientExecutor, get_client_executor
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
    "CONTEXT_TOOLS",
    "FILE_TOOLS",
    # Server tools
    "SERVER_TOOLS",
    "UTILITY_TOOLS",
    "AsyncExecutor",
    "ClientExecutor",
    # Router
    "ExecutionRouter",
    "ResourceLimits",
    # Executors
    "SandboxExecutor",
    # Execution modes
    "ToolExecutionMode",
    "ToolMetadata",
    "async_tool",
    "client_tool",
    "get_async_executor",
    "get_client_executor",
    "get_execution_router",
    # Metadata functions
    "get_tool_metadata",
    "list_tools_by_mode",
    "register_tool_metadata",
    # Decorators
    "sandbox_tool",
    "server_tool",
]
