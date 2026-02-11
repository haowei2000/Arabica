"""
Celery tasks for async tool execution.

These tasks run long-running tools in the background with:
- Progress tracking
- Timeout handling
- Retry support
- Result persistence
"""

import logging
from typing import Any

from aiwen.services.context.tools.async_executor import (
    AsyncTaskStatus,
    set_task_result,
    update_task_progress,
)
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="aiwen.celery_worker.tasks.tool_tasks.execute_async_tool",
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def execute_async_tool(
    self,
    tool_name: str,
    tool_id: str,
    arguments: dict[str, Any],
    context: dict[str, Any] | None = None,
    timeout: int = 3600,
    progress_enabled: bool = True,
) -> dict[str, Any]:
    """
    Execute an async tool in Celery worker.

    Args:
        self: Celery task instance (for retry/progress)
        tool_name: Name of the tool to execute
        tool_id: Original tool invocation ID
        arguments: Tool arguments
        context: Execution context (run_id, workspace_id, etc.)
        timeout: Execution timeout in seconds
        progress_enabled: Whether to emit progress updates

    Returns:
        Tool execution result
    """
    import asyncio

    task_id = self.request.id

    logger.info(f"Starting async tool execution: {tool_name} (task_id={task_id})")

    # Update status to running
    if progress_enabled:
        # Run async update in sync context
        asyncio.get_event_loop().run_until_complete(
            _update_progress_async(task_id, 0, "Starting execution...")
        )

    try:
        # Get the tool function
        tool_func = _get_tool_function(tool_name)

        if tool_func is None:
            raise ValueError(f"Tool not found: {tool_name}")

        # Execute the tool
        if asyncio.iscoroutinefunction(tool_func):
            # Async tool
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    _execute_with_progress(
                        task_id=task_id,
                        tool_func=tool_func,
                        arguments=arguments,
                        progress_enabled=progress_enabled,
                    )
                )
            finally:
                loop.close()
        else:
            # Sync tool
            result = tool_func(**arguments)

        # Store successful result
        set_task_result(
            task_id=task_id,
            success=True,
            result=result,
        )

        logger.info(
            f"Async tool completed successfully: {tool_name} (task_id={task_id})"
        )

        return {
            "success": True,
            "result": result,
            "tool_name": tool_name,
            "task_id": task_id,
        }

    except Exception as e:
        logger.exception(f"Async tool failed: {tool_name} (task_id={task_id}): {e}")

        # Store failure result
        set_task_result(
            task_id=task_id,
            success=False,
            error_message=str(e),
        )

        # Optionally retry
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=2**self.request.retries)

        return {
            "success": False,
            "error": str(e),
            "tool_name": tool_name,
            "task_id": task_id,
        }


async def _update_progress_async(task_id: str, progress: int, message: str) -> None:
    """Async wrapper for progress update."""
    await update_task_progress(task_id, progress, message)


async def _execute_with_progress(
    task_id: str,
    tool_func: Any,
    arguments: dict[str, Any],
    progress_enabled: bool,
) -> Any:
    """Execute tool with progress tracking."""
    if progress_enabled:
        await update_task_progress(task_id, 10, "Executing tool...")

    # Check if tool supports progress callback
    if "progress_callback" in arguments or _supports_progress(tool_func):

        async def progress_callback(progress: int, message: str):
            if progress_enabled:
                # Scale progress to 10-90 range (10% start, 90% end)
                scaled = 10 + int(progress * 0.8)
                await update_task_progress(task_id, scaled, message)

        arguments = {**arguments, "progress_callback": progress_callback}

    result = await tool_func(**arguments)

    if progress_enabled:
        await update_task_progress(task_id, 100, "Completed")

    return result


def _get_tool_function(tool_name: str) -> Any:
    """Get the actual tool function by name."""
    from aiwen.services.context.tools.execution_router import get_execution_router

    router = get_execution_router()
    return router._tool_functions.get(tool_name)


def _supports_progress(func: Any) -> bool:
    """Check if function accepts progress_callback parameter."""
    import inspect

    try:
        sig = inspect.signature(func)
        return "progress_callback" in sig.parameters
    except (ValueError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Example async tools with progress support
# ═══════════════════════════════════════════════════════════════════════════════


async def example_long_running_tool(
    data: dict[str, Any],
    iterations: int = 10,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """
    Example of a long-running tool with progress support.

    This shows the pattern for implementing async tools that report progress.
    """
    import asyncio

    results = []

    for i in range(iterations):
        # Simulate work
        await asyncio.sleep(1)

        # Report progress
        if progress_callback:
            await progress_callback(
                int((i + 1) / iterations * 100),
                f"Processing iteration {i + 1}/{iterations}",
            )

        results.append({"iteration": i + 1, "processed": True})

    return {
        "total_iterations": iterations,
        "results": results,
        "status": "completed",
    }
