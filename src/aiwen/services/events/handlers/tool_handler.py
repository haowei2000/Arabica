"""Tool execution handler.

Handles tool.call events - executes tools and publishes results.
This decouples tool execution from the executor, allowing:
- Centralized tool execution monitoring
- Independent tool scaling
- Tool execution retries without re-running LLM
"""

import logging
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.events import EventType
from aiwen.models.app import App
from aiwen.models.events.event import Event
from aiwen.models.runs.run import Run
from aiwen.registries.tool_service import RegistryToolCaller
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.services.runs.run_state_machine import RunStateMachine

logger = logging.getLogger(__name__)


async def handle_tool_call(
    event: Event,
    db: AsyncSession,
    event_publisher: EventPublisher,
    state_machine: RunStateMachine,
):
    """Execute a tool and publish result/error event.

    Args:
        event: The tool.call event
        db: Database session
        event_publisher: Event publisher for results
        state_machine: Run state machine
    """
    try:
        if not event.run_id:
            logger.warning("Received tool.call without run_id, skipping")
            return

        run_id = event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))
        payload = event.payload or {}

        tool_name = payload.get("tool_name")
        tool_id = payload.get("tool_id")
        arguments = payload.get("arguments", {})

        if not tool_name:
            logger.error(f"tool.call event missing tool_name for run {run_id}")
            return

        logger.info(f"Executing tool '{tool_name}' for run {run_id}")

        # Get the run to check for approval requirements
        run_result = await db.execute(select(Run).where(Run.id == str(run_id)))
        run = run_result.scalar_one_or_none()
        if not run:
            logger.error(f"Run {run_id} not found for tool execution")
            return

        # Check if tool requires approval (HITL)
        if await _tool_requires_approval(db, run, tool_name):
            logger.info(f"Tool '{tool_name}' requires approval, pausing run {run_id}")

            # Pause run and request approval
            await state_machine.pause_for_tool(
                run_id,
                waiting_for={
                    "type": "tool_approval",
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "arguments": arguments,
                    "executor_code": event.executor_code,
                },
                auto_commit=True,
            )

            # Publish tool.pending event for frontend
            await event_publisher.publish(
                event_type=EventType.TOOL_PENDING,
                workspace_id=str(run.workspace_id),
                run_id=str(run_id),
                payload={
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "arguments": arguments,
                },
                auto_commit=True,
            )
            return

        # Execute the tool
        start_time = time.time()
        try:
            tool_caller = RegistryToolCaller()
            result = await tool_caller.call(tool_name, arguments)
            elapsed_ms = int((time.time() - start_time) * 1000)

            # Publish tool.result event
            await event_publisher.publish(
                event_type=EventType.TOOL_RESULT,
                workspace_id=str(run.workspace_id),
                run_id=str(run_id),
                payload={
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "result": result,
                    "execution_time_ms": elapsed_ms,
                },
                auto_commit=True,
            )

            logger.info(f"Tool '{tool_name}' completed successfully in {elapsed_ms}ms for run {run_id}")

        except Exception as tool_error:
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_msg = str(tool_error)

            # Publish tool.error event
            await event_publisher.publish(
                event_type=EventType.TOOL_ERROR,
                workspace_id=str(run.workspace_id),
                run_id=str(run_id),
                payload={
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "error_message": error_msg,
                    "execution_time_ms": elapsed_ms,
                },
                auto_commit=True,
            )

            logger.error(f"Tool '{tool_name}' failed for run {run_id}: {error_msg}")

    except Exception as e:
        logger.error(f"handle_tool_call error: {e}", exc_info=True)


async def _tool_requires_approval(db: AsyncSession, run: Run, tool_name: str) -> bool:
    """Check if a tool requires HITL approval based on app config."""
    try:
        if not run.app_id:
            return False

        app_result = await db.execute(select(App).where(App.id == run.app_id))
        app = app_result.scalar_one_or_none()

        if not app or not app.config:
            return False

        # Check if tool is in approval_tools list
        approval_tools = app.config.get("approval_tools", [])
        return tool_name in approval_tools

    except Exception as e:
        logger.warning(f"Failed to check tool approval requirement: {e}")
        return False
