"""
Central routing logic for tool execution.

The ExecutionRouter determines how each tool should be executed based on
its metadata and delegates to the appropriate executor.
"""

import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from aiwen.schemas.tools.execution import (
    ExecutionContext,
    ToolExecutionRequest,
    ToolResult,
)
from aiwen.services.agent.tools.execution_mode import (
    ToolExecutionMode,
    ToolMetadata,
    get_tool_metadata,
)

if TYPE_CHECKING:
    from aiwen.services.agent.tools.async_executor import AsyncExecutor
    from aiwen.services.agent.tools.client_executor import ClientExecutor
    from aiwen.services.agent.tools.sandbox_executor import SandboxExecutor

logger = logging.getLogger(__name__)


class ExecutionRouter:
    """
    Routes tool execution to the appropriate executor based on tool metadata.

    The router checks the tool's registered execution mode and delegates to:
    - SERVER: Direct in-process execution
    - SANDBOX: Docker container execution via SandboxExecutor
    - CLIENT: Browser execution via ClientExecutor (SSE + HTTP callback)
    """

    def __init__(
        self,
        sandbox_executor: "SandboxExecutor | None" = None,
        client_executor: "ClientExecutor | None" = None,
        async_executor: "AsyncExecutor | None" = None,
    ):
        """
        Initialize the router with optional executors.

        Args:
            sandbox_executor: Executor for sandbox (Docker) tools
            client_executor: Executor for client (browser) tools
            async_executor: Executor for async (Celery) tools
        """
        self._sandbox_executor = sandbox_executor
        self._client_executor = client_executor
        self._async_executor = async_executor
        self._tool_functions: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}

    def register_tool_function(
        self,
        tool_name: str,
        func: Callable[..., Coroutine[Any, Any, Any]],
    ) -> None:
        """Register a tool function for server-side execution."""
        self._tool_functions[tool_name] = func

    def register_tools_from_list(self, tools: list[Any]) -> None:
        """Register multiple tools from a list (e.g., LangChain tools)."""
        for tool in tools:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if name:
                # For LangChain tools, get the coroutine function
                func = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
                if func is None and callable(tool):
                    func = tool
                if func:
                    self._tool_functions[name] = func

    @property
    def sandbox_executor(self) -> "SandboxExecutor":
        """Get sandbox executor, raising if not configured."""
        if self._sandbox_executor is None:
            from aiwen.services.agent.tools.sandbox_executor import SandboxExecutor

            self._sandbox_executor = SandboxExecutor()
        return self._sandbox_executor

    @property
    def client_executor(self) -> "ClientExecutor":
        """Get client executor, raising if not configured."""
        if self._client_executor is None:
            from aiwen.services.agent.tools.client_executor import ClientExecutor

            self._client_executor = ClientExecutor()
        return self._client_executor

    @property
    def async_executor(self) -> "AsyncExecutor":
        """Get async executor, raising if not configured."""
        if self._async_executor is None:
            from aiwen.services.agent.tools.async_executor import AsyncExecutor

            self._async_executor = AsyncExecutor()
        return self._async_executor

    async def execute(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        """
        Execute a tool using the appropriate executor.

        Args:
            tool_name: Name of the tool to execute
            tool_id: Unique ID for this invocation
            arguments: Tool arguments
            context: Execution context with run_id, workspace_id, etc.

        Returns:
            ToolResult with execution outcome
        """
        metadata = get_tool_metadata(tool_name)

        logger.info(
            f"Routing tool '{tool_name}' (id={tool_id}) "
            f"to {metadata.execution_mode.value} executor"
        )

        match metadata.execution_mode:
            case ToolExecutionMode.SANDBOX:
                try:
                    return await self.sandbox_executor.execute(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        arguments=arguments,
                        metadata=metadata,
                        context=context,
                    )
                except Exception as e:
                    logger.exception(f"Sandbox execution failed for '{tool_name}': {e}")
                    return ToolResult(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        success=False,
                        error_message=f"Sandbox execution failed: {e}",
                        execution_time_ms=0,
                        execution_mode=ToolExecutionMode.SANDBOX.value,
                    )

            case ToolExecutionMode.CLIENT:
                try:
                    return await self.client_executor.execute(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        arguments=arguments,
                        metadata=metadata,
                        context=context,
                    )
                except Exception as e:
                    logger.exception(f"Client execution failed for '{tool_name}': {e}")
                    return ToolResult(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        success=False,
                        error_message=f"Client execution failed: {e}",
                        execution_time_ms=0,
                        execution_mode=ToolExecutionMode.CLIENT.value,
                    )

            case ToolExecutionMode.ASYNC:
                try:
                    return await self.async_executor.execute(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        arguments=arguments,
                        metadata=metadata,
                        context=context,
                    )
                except Exception as e:
                    logger.exception(f"Async execution failed for '{tool_name}': {e}")
                    return ToolResult(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        success=False,
                        error_message=f"Async execution failed: {e}",
                        execution_time_ms=0,
                        execution_mode=ToolExecutionMode.ASYNC.value,
                    )

            case _:
                # Default to server execution
                return await self._execute_server(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    arguments=arguments,
                    metadata=metadata,
                )

    async def _execute_server(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        metadata: ToolMetadata,
    ) -> ToolResult:
        """
        Execute a tool directly in the server process.

        This is the default execution mode for most tools.
        """
        start_time = time.perf_counter()

        tool_func = self._tool_functions.get(tool_name)
        if tool_func is None:
            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=False,
                error_message=f"Tool '{tool_name}' not registered for server execution",
                execution_time_ms=0,
                execution_mode=ToolExecutionMode.SERVER.value,
            )

        try:
            result = await tool_func(**arguments)
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=True,
                result=result,
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.SERVER.value,
            )

        except Exception as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            logger.exception(f"Tool '{tool_name}' execution failed: {e}")

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=False,
                error_message=str(e),
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.SERVER.value,
            )


# Singleton instance for convenience
_default_router: ExecutionRouter | None = None


def get_execution_router() -> ExecutionRouter:
    """Get the default execution router instance."""
    global _default_router
    if _default_router is None:
        _default_router = ExecutionRouter()
    return _default_router
