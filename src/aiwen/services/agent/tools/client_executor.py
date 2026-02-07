"""
Client executor for running tools in the user's browser.

This executor sends requests to the client via SSE events and waits
for the client to submit results via HTTP callback.
"""

import asyncio
from datetime import datetime, timedelta
import json
import logging
import time
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async

from aiwen.schemas.events.event_payloads import EventType
from aiwen.schemas.tools.execution import (
    ExecutionContext,
    PendingToolExecution,
    ToolClientRequestPayload,
    ToolResult,
    ToolResultSubmission,
)
from aiwen.services.agent.tools.execution_mode import (
    ToolExecutionMode,
    ToolMetadata,
)

logger = logging.getLogger(__name__)

# Redis key patterns for client tool coordination
PENDING_TOOL_KEY = "tool:pending:{tool_id}"
TOOL_RESULT_KEY = "tool:result:{tool_id}"
TOOL_RESULT_CHANNEL = "tool:result:channel:{tool_id}"


class ClientExecutor:
    """
    Execute tools in the user's browser via SSE events.

    Flow:
    1. Publish TOOL_CLIENT_REQUEST event to SSE stream
    2. Store pending execution state in Redis
    3. Wait for client to submit result via HTTP POST
    4. Return result or timeout error
    """

    def __init__(self, redis_client: redis_async.Redis | None = None):
        """
        Initialize the client executor.

        Args:
            redis_client: Redis async client for coordination
        """
        self._redis = redis_client
        self._event_publisher: Any = None

    async def _get_redis(self) -> redis_async.Redis:
        """Get Redis client, creating if needed."""
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
    ) -> ToolResult:
        """
        Execute a tool in the client's browser.

        Args:
            tool_name: Name of the tool
            tool_id: Unique invocation ID
            arguments: Tool arguments
            metadata: Tool execution metadata
            context: Execution context

        Returns:
            ToolResult with execution outcome
        """
        start_time = time.perf_counter()
        redis = await self._get_redis()
        timeout = metadata.timeout_seconds

        try:
            # 1. Store pending execution state
            pending = PendingToolExecution(
                tool_id=tool_id,
                tool_name=tool_name,
                run_id=context.run_id,
                workspace_id=context.workspace_id,
                handler=metadata.client_handler or tool_name,
                arguments=arguments,
                timeout_seconds=timeout,
                expires_at=datetime.utcnow() + timedelta(seconds=timeout),
            )

            pending_key = PENDING_TOOL_KEY.format(tool_id=tool_id)
            await redis.setex(
                pending_key,
                timeout + 30,  # Extra buffer for cleanup
                pending.model_dump_json(),
            )

            # 2. Publish TOOL_CLIENT_REQUEST event to SSE stream
            await self._publish_client_request(
                tool_name=tool_name,
                tool_id=tool_id,
                handler=metadata.client_handler or tool_name,
                arguments=arguments,
                timeout_seconds=timeout,
                config=metadata.client_config,
                context=context,
            )

            logger.info(
                f"Waiting for client to execute tool '{tool_name}' "
                f"(id={tool_id}, timeout={timeout}s)"
            )

            # 3. Wait for client result
            result = await self._wait_for_result(tool_id, timeout)

            execution_time_ms = int((time.perf_counter() - start_time) * 1000)

            if result is None:
                return ToolResult(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    success=False,
                    error_message=f"Client did not respond within {timeout} seconds",
                    execution_time_ms=execution_time_ms,
                    execution_mode=ToolExecutionMode.CLIENT.value,
                )

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=result.success,
                result=result.result,
                error_message=result.error_message,
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.CLIENT.value,
            )

        except Exception as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            logger.exception(f"Client execution error for tool '{tool_name}': {e}")

            return ToolResult(
                tool_name=tool_name,
                tool_id=tool_id,
                success=False,
                error_message=f"Client execution error: {e}",
                execution_time_ms=execution_time_ms,
                execution_mode=ToolExecutionMode.CLIENT.value,
            )

        finally:
            # Cleanup pending state
            await self._cleanup(tool_id)

    async def _publish_client_request(
        self,
        tool_name: str,
        tool_id: str,
        handler: str,
        arguments: dict[str, Any],
        timeout_seconds: int,
        config: dict[str, Any],
        context: ExecutionContext,
    ) -> None:
        """Publish TOOL_CLIENT_REQUEST event to SSE stream."""
        redis = await self._get_redis()

        payload = ToolClientRequestPayload(
            tool_name=tool_name,
            tool_id=tool_id,
            handler=handler,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            config=config,
        )

        # Publish to run's event stream
        event_data = {
            "id": tool_id,
            "event_type": EventType.TOOL_CLIENT_REQUEST.value,
            "workspace_id": str(context.workspace_id),
            "run_id": str(context.run_id),
            "payload": json.dumps(payload.model_dump()),
            "created_at": datetime.utcnow().isoformat(),
        }

        run_stream = f"run:{context.run_id}:events"
        await redis.xadd(
            name=run_stream,
            fields=event_data,
            maxlen=1000,
            approximate=True,
        )

        logger.debug(
            f"Published TOOL_CLIENT_REQUEST for tool {tool_id} to {run_stream}"
        )

    async def _wait_for_result(
        self,
        tool_id: str,
        timeout: int,
    ) -> ToolResultSubmission | None:
        """Wait for client to submit tool result."""
        redis = await self._get_redis()
        result_key = TOOL_RESULT_KEY.format(tool_id=tool_id)
        channel_name = TOOL_RESULT_CHANNEL.format(tool_id=tool_id)

        # Use pub/sub for immediate notification
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name)

        try:
            # Poll with pub/sub for faster response
            deadline = time.time() + timeout

            while time.time() < deadline:
                # Check if result was already submitted
                result_data = await redis.get(result_key)
                if result_data:
                    return ToolResultSubmission.model_validate_json(result_data)

                # Wait for pub/sub notification
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=min(1.0, deadline - time.time()),
                    )

                    if message and message["type"] == "message":
                        # Result notification received, fetch the result
                        result_data = await redis.get(result_key)
                        if result_data:
                            return ToolResultSubmission.model_validate_json(result_data)

                except TimeoutError:
                    # Continue polling
                    pass

            return None

        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.close()

    async def submit_result(
        self,
        tool_id: str,
        result: ToolResultSubmission,
    ) -> bool:
        """
        Submit a tool result from the client.

        This is called by the HTTP endpoint when the client submits a result.

        Args:
            tool_id: Tool invocation ID
            result: Result submitted by client

        Returns:
            True if submission was accepted, False if tool not found/expired
        """
        redis = await self._get_redis()

        # Check if pending execution exists
        pending_key = PENDING_TOOL_KEY.format(tool_id=tool_id)
        pending_data = await redis.get(pending_key)

        if not pending_data:
            logger.warning(f"Result submitted for unknown/expired tool: {tool_id}")
            return False

        # Store result
        result_key = TOOL_RESULT_KEY.format(tool_id=tool_id)
        await redis.setex(result_key, 60, result.model_dump_json())  # 60s TTL

        # Notify waiting executor
        channel_name = TOOL_RESULT_CHANNEL.format(tool_id=tool_id)
        await redis.publish(channel_name, "result_ready")

        logger.info(
            f"Client submitted result for tool {tool_id}: success={result.success}"
        )
        return True

    async def get_pending_execution(
        self,
        tool_id: str,
    ) -> PendingToolExecution | None:
        """Get pending execution info (for validation)."""
        redis = await self._get_redis()
        pending_key = PENDING_TOOL_KEY.format(tool_id=tool_id)
        data = await redis.get(pending_key)

        if data:
            return PendingToolExecution.model_validate_json(data)
        return None

    async def _cleanup(self, tool_id: str) -> None:
        """Cleanup Redis keys for a tool execution."""
        redis = await self._get_redis()

        keys = [
            PENDING_TOOL_KEY.format(tool_id=tool_id),
            TOOL_RESULT_KEY.format(tool_id=tool_id),
        ]

        await redis.delete(*keys)


# Singleton for use by HTTP endpoints
_client_executor: ClientExecutor | None = None


def get_client_executor() -> ClientExecutor:
    """Get the singleton client executor."""
    global _client_executor
    if _client_executor is None:
        _client_executor = ClientExecutor()
    return _client_executor
