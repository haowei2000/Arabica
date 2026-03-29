"""Run lifecycle event handlers.

Handles run-related events like cancellation cleanup.
"""

import logging
from uuid import UUID

from structure.models.events.event import Event
from structure.services.executor.runtime import ExecutorInstanceManager

logger = logging.getLogger(__name__)


async def handle_run_cancellation(event: Event, runtime: ExecutorInstanceManager):
    """Handle run.cancelled events - cleanup after run cancellation.

    Args:
        event: The run.cancelled event
        runtime: Executor runtime manager
    """
    try:
        if not event.run_id:
            logger.warning("Received run.cancelled without run_id, skipping")
            return

        run_id = event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))

        logger.info(f"Processing run cancellation for {run_id}")

        # Release executor resources if still attached
        if runtime.exists(run_id):
            runtime.release(run_id)
            logger.info(f"Released executor resources for cancelled run {run_id}")

        # Additional cleanup tasks
        # TODO: Cancel pending tool calls, cleanup temp files, etc.

    except Exception as e:
        logger.error(f"handle_run_cancellation error: {e}", exc_info=True)
