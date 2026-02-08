"""
Async executor for long-running background tasks via Celery.

This executor handles tools that may take minutes to complete:
- Submits tasks to Celery queue
- Tracks progress via Redis
- Emits progress events via SSE
- Supports retry and timeout policies
"""

from datetime import datetime
import json
import logging
import time
from typing import Any
from uuid import uuid4

import redis.asyncio as redis_async

from aiwen.schemas.tools.execution import ExecutionContext, ToolResult
from aiwen.services.executor.tools.execution_mode import (
    ToolExecutionMode,
    ToolMetadata,
)

logger = logging.getLogger(__name__)

# Redis key patterns for async task tracking
ASYNC_TASK_KEY = "async_tool:task:{task_id}"
ASYNC_TASK_PROGRESS_KEY = "async_tool:progress:{task_id}"
ASYNC_TASK_RESULT_KEY = "async_tool:result:{task_id}"
ASYNC_TASK_CHANNEL = "async_tool:channel:{task_id}"


class AsyncTaskStatus:
    """Status constants for async tasks."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class AsyncExecutor:
    """
    Execute tools as background Celery tasks.

    Flow:
    1. Submit task to Celery queue
    2. Return immediately with task_id
    3. Celery worker executes the tool
    4. Progress updates sent via Redis pub/sub
    5. Agent can poll or wait for completion

    For agent integration, there are two modes:
    - Fire-and-forget: Return task_id, agent continues
    - Wait-for-result: Block until task completes (with timeout)
    """

    def __init__(self, redis_client: redis_async.Redis | None = None):
        """
        Initialize the async executor.

        Args:
            redis_client: Redis client for task tracking
        """
        self._redis = redis_client

    async def _get_redis(self) -> redis_async.Redis:
        """Get Redis client."""
        if self._redis is None:
            from aiwen.middleware.cache_middleware import get_redis_client

            self._redis = get_redis_client(is_async=True)
        return self._redis

    async def execute(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        metadata: ToolMetadata,
        context: ExecutionContext,
        wait_for_result: bool = False,
        poll_interval: float = 1.0,
    ) -> ToolResult:
        """
        Execute a tool as a background Celery task.

        Args:
            tool_name: Name of the tool
            tool_id: Unique invocation ID
            arguments: Tool arguments
            metadata: Tool execution metadata
            context: Execution context
            wait_for_result: Whether to wait for completion
            poll_interval: Polling interval when waiting (seconds)

        Returns:
            ToolResult with task_id (immediate) or final result (if waiting)
        """
        start_time = time.perf_counter()
        redis = await self._get_redis()

        try:
            # Generate Celery task ID
            celery_task_id = f"tool_{tool_id}_{uuid4().hex[:8]}"

            # Store task metadata
            task_info = {
                "task_id": celery_task_id,
                "tool_id": tool_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "context": {
                    "run_id": str(context.run_id),
                    "workspace_id": str(context.workspace_id),
                    "user_id": str(context.user_id) if context.user_id else None,
                },
                "status": AsyncTaskStatus.PENDING,
                "created_at": datetime.utcnow().isoformat(),
                "queue": metadata.celery_queue,
                "timeout": metadata.timeout_seconds,
                "progress": 0,
                "progress_message": "Task queued",
            }

            task_key = ASYNC_TASK_KEY.format(task_id=celery_task_id)
            await redis.setex(
                task_key,
                metadata.timeout_seconds + 3600,  # Keep for 1 hour after timeout
                json.dumps(task_info),
            )

            # Submit to Celery
            await self._submit_celery_task(
                celery_task_id=celery_task_id,
                tool_name=tool_name,
                tool_id=tool_id,
                arguments=arguments,
                metadata=metadata,
                context=context,
            )

            logger.info(
                f"Submitted async task '{tool_name}' to queue '{metadata.celery_queue}' "
                f"(task_id={celery_task_id})"
            )

            # If not waiting, return immediately with task_id
            if not wait_for_result:
                execution_time_ms = int((time.perf_counter() - start_time) * 1000)
                return ToolResult(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    success=True,
                    result={
                        "status": "submitted",
                        "task_id": celery_task_id,
                        "message": "Task submitted to background queue",
                        "queue": metadata.celery_queue,
                    },
                    execution_time_ms=execution_time_ms,
                    execution_mode=ToolExecutionMode.ASYNC.value,
                )

            # Wait for result
            return await self._wait_for_result(
                celery_task_id=celery_task_id,
                tool_name=tool_name,
                tool_id=tool_id,
                timeout=metadata.timeout_seconds,
                poll_interval=poll_interval,
                start_time=start_time,
            )

        except Exception as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            logger.exception(f"Async execution error for tool '{tool_name}': {e}")

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=False,
                error_message=f"Async execution error: {e}",
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.ASYNC.value,
            )

    async def _submit_celery_task(
        self,
        celery_task_id: str,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        metadata: ToolMetadata,
        context: ExecutionContext,
    ) -> None:
        """Submit task to Celery queue."""
        from aiwen.celery_worker.celery_app import celery_app

        # Use send_task for dynamic task routing
        celery_app.send_task(
            "aiwen.celery_worker.tasks.tool_tasks.execute_async_tool",
            args=[tool_name, tool_id, arguments],
            kwargs={
                "context": {
                    "run_id": str(context.run_id),
                    "workspace_id": str(context.workspace_id),
                    "user_id": str(context.user_id) if context.user_id else None,
                },
                "timeout": metadata.timeout_seconds,
                "progress_enabled": metadata.progress_enabled,
            },
            task_id=celery_task_id,
            queue=metadata.celery_queue,
            priority=metadata.celery_priority,
            retry=metadata.retry_on_failure,
            retry_policy={
                "max_retries": metadata.max_retries,
                "interval_start": 1,
                "interval_step": 2,
                "interval_max": 30,
            },
        )

    async def _wait_for_result(
        self,
        celery_task_id: str,
        tool_name: str,
        tool_id: str,
        timeout: int,
        poll_interval: float,
        start_time: float,
    ) -> ToolResult:
        """Wait for async task to complete."""
        import asyncio

        redis = await self._get_redis()
        result_key = ASYNC_TASK_RESULT_KEY.format(task_id=celery_task_id)
        task_key = ASYNC_TASK_KEY.format(task_id=celery_task_id)

        deadline = time.time() + timeout

        while time.time() < deadline:
            # Check for result
            result_data = await redis.get(result_key)
            if result_data:
                result = json.loads(result_data)
                execution_time_ms = int((time.perf_counter() - start_time) * 1000)

                return ToolResult(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    success=result.get("success", False),
                    result=result.get("result"),
                    error_message=result.get("error_message"),
                    execution_time_ms=execution_time_ms,
                    execution_mode=ToolExecutionMode.ASYNC.value,
                )

            # Check task status for early failure detection
            task_data = await redis.get(task_key)
            if task_data:
                task_info = json.loads(task_data)
                if task_info.get("status") == AsyncTaskStatus.FAILED:
                    execution_time_ms = int((time.perf_counter() - start_time) * 1000)
                    return ToolResult(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        success=False,
                        error_message=task_info.get("error", "Task failed"),
                        execution_time_ms=execution_time_ms,
                        execution_mode=ToolExecutionMode.ASYNC.value,
                    )

            await asyncio.sleep(poll_interval)

        # Timeout
        execution_time_ms = int((time.perf_counter() - start_time) * 1000)
        return ToolResult(
            tool_name=tool_name,
            tool_id=tool_id,
            success=False,
            error_message=f"Task timed out after {timeout} seconds",
            execution_time_ms=execution_time_ms,
            execution_mode=ToolExecutionMode.ASYNC.value,
        )

    async def get_task_status(self, celery_task_id: str) -> dict[str, Any] | None:
        """
        Get the current status of an async task.

        Args:
            celery_task_id: The Celery task ID

        Returns:
            Task info dict or None if not found
        """
        redis = await self._get_redis()
        task_key = ASYNC_TASK_KEY.format(task_id=celery_task_id)
        data = await redis.get(task_key)

        if data:
            return json.loads(data)
        return None

    async def get_task_progress(self, celery_task_id: str) -> dict[str, Any] | None:
        """
        Get the current progress of an async task.

        Args:
            celery_task_id: The Celery task ID

        Returns:
            Progress info dict or None
        """
        redis = await self._get_redis()
        progress_key = ASYNC_TASK_PROGRESS_KEY.format(task_id=celery_task_id)
        data = await redis.get(progress_key)

        if data:
            return json.loads(data)
        return None

    async def cancel_task(self, celery_task_id: str) -> bool:
        """
        Cancel a running async task.

        Args:
            celery_task_id: The Celery task ID

        Returns:
            True if cancellation was requested successfully
        """
        from aiwen.celery_worker.celery_app import celery_app

        try:
            celery_app.control.revoke(celery_task_id, terminate=True)

            # Update task status
            redis = await self._get_redis()
            task_key = ASYNC_TASK_KEY.format(task_id=celery_task_id)
            task_data = await redis.get(task_key)

            if task_data:
                task_info = json.loads(task_data)
                task_info["status"] = AsyncTaskStatus.CANCELLED
                task_info["cancelled_at"] = datetime.utcnow().isoformat()
                await redis.set(task_key, json.dumps(task_info))

            logger.info(f"Cancelled async task: {celery_task_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel task {celery_task_id}: {e}")
            return False


# Helper functions for Celery task to update progress
async def update_task_progress(
    task_id: str,
    progress: int,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Update progress for an async task (called from Celery worker).

    Args:
        task_id: The Celery task ID
        progress: Progress percentage (0-100)
        message: Human-readable progress message
        extra: Additional progress data
    """
    from aiwen.middleware.cache_middleware import get_redis_client

    redis = get_redis_client(is_async=False)  # Sync Redis for Celery

    progress_data = {
        "progress": progress,
        "message": message,
        "updated_at": datetime.utcnow().isoformat(),
        **(extra or {}),
    }

    # Update progress key
    progress_key = ASYNC_TASK_PROGRESS_KEY.format(task_id=task_id)
    redis.setex(progress_key, 3600, json.dumps(progress_data))

    # Also update task info
    task_key = ASYNC_TASK_KEY.format(task_id=task_id)
    task_data = redis.get(task_key)
    if task_data:
        task_info = json.loads(task_data)
        task_info["progress"] = progress
        task_info["progress_message"] = message
        task_info["status"] = AsyncTaskStatus.RUNNING
        redis.set(task_key, json.dumps(task_info))

    # Publish progress event for SSE
    channel = ASYNC_TASK_CHANNEL.format(task_id=task_id)
    redis.publish(channel, json.dumps(progress_data))


def set_task_result(
    task_id: str,
    success: bool,
    result: Any = None,
    error_message: str | None = None,
) -> None:
    """
    Set the final result for an async task (called from Celery worker).

    Args:
        task_id: The Celery task ID
        success: Whether the task succeeded
        result: Task result data
        error_message: Error message if failed
    """
    from aiwen.middleware.cache_middleware import get_redis_client

    redis = get_redis_client(is_async=False)

    result_data = {
        "success": success,
        "result": result,
        "error_message": error_message,
        "completed_at": datetime.utcnow().isoformat(),
    }

    # Store result
    result_key = ASYNC_TASK_RESULT_KEY.format(task_id=task_id)
    redis.setex(result_key, 3600, json.dumps(result_data))

    # Update task status
    task_key = ASYNC_TASK_KEY.format(task_id=task_id)
    task_data = redis.get(task_key)
    if task_data:
        task_info = json.loads(task_data)
        task_info["status"] = (
            AsyncTaskStatus.COMPLETED if success else AsyncTaskStatus.FAILED
        )
        task_info["completed_at"] = datetime.utcnow().isoformat()
        if error_message:
            task_info["error"] = error_message
        redis.set(task_key, json.dumps(task_info))

    # Publish completion event
    channel = ASYNC_TASK_CHANNEL.format(task_id=task_id)
    redis.publish(channel, json.dumps({"status": "completed", **result_data}))


# Singleton instance
_async_executor: AsyncExecutor | None = None


def get_async_executor() -> AsyncExecutor:
    """Get the singleton async executor."""
    global _async_executor
    if _async_executor is None:
        _async_executor = AsyncExecutor()
    return _async_executor
