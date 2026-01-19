#!/usr/bin/env python3
"""
Task Producer - Celery Task Dispatcher

Dispatches tasks to Celery for async processing.
Maintains backward-compatible interface.
"""

import logging
from typing import Any
from uuid import UUID

from celery.result import AsyncResult

from aiwen.workers.tasks import process_agent_task

logger = logging.getLogger(__name__)


class TaskProducer:
    """
    Task producer using Celery for task dispatch.

    Usage:
        ```python
        from aiwen.workers.task_producer import TaskProducer

        producer = TaskProducer()

        # Publish task
        celery_task_id = await producer.publish_task(
            task_id=task.id,
            payload={"app_id": "...", "query": "Hello"}
        )
        ```
    """

    def __init__(self, redis_client: Any = None):
        """
        Initialize task producer.

        Args:
            redis_client: Kept for backward compatibility, not used with Celery
        """
        # redis_client is kept for backward compatibility but not used
        pass

    async def publish_task(
        self,
        task_id: UUID,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """
        Publish task via Celery.

        Args:
            task_id: Task ID
            payload: Task payload (must contain app_id)

        Returns:
            Celery task ID

        Raises:
            ValueError: If payload is missing app_id
            Exception: If task dispatch fails
        """
        try:
            payload = payload or {}
            app_id = payload.get("app_id")

            if not app_id:
                raise ValueError("Missing required field: app_id in payload")

            # Dispatch task via Celery
            result: AsyncResult = process_agent_task.apply_async(
                kwargs={
                    "task_id": str(task_id),
                    "app_id": str(app_id),
                    "payload": payload,
                },
                task_id=str(task_id),  # Use our task_id as Celery task_id
            )

            logger.info(f"Published task {task_id} via Celery (celery_id: {result.id})")
            return result.id

        except Exception as e:
            logger.error(f"Error publishing task {task_id}: {e}", exc_info=True)
            raise

    async def publish_scheduled_task(
        self,
        task_id: UUID,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Publish scheduled task via Celery.

        Args:
            task_id: Task ID
            payload: Task payload
            metadata: Schedule metadata (countdown, eta, etc.)

        Returns:
            Celery task ID
        """
        try:
            payload = payload or {}
            app_id = payload.get("app_id")

            if not app_id:
                raise ValueError("Missing required field: app_id in payload")

            # Extract scheduling options from metadata
            countdown = None
            eta = None
            if metadata:
                countdown = metadata.get("countdown")
                eta = metadata.get("eta")

            # Dispatch scheduled task via Celery
            result: AsyncResult = process_agent_task.apply_async(
                kwargs={
                    "task_id": str(task_id),
                    "app_id": str(app_id),
                    "payload": payload,
                },
                task_id=str(task_id),
                countdown=countdown,
                eta=eta,
            )

            logger.info(f"Published scheduled task {task_id} via Celery (celery_id: {result.id})")
            return result.id

        except Exception as e:
            logger.error(f"Error publishing scheduled task {task_id}: {e}", exc_info=True)
            raise

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """
        Get Celery task status.

        Args:
            task_id: Task ID (also used as Celery task ID)

        Returns:
            Task status dict
        """
        try:
            result = AsyncResult(task_id)
            return {
                "task_id": task_id,
                "status": result.status,
                "ready": result.ready(),
                "successful": result.successful() if result.ready() else None,
                "result": result.result if result.ready() else None,
            }
        except Exception as e:
            logger.error(f"Error getting task status {task_id}: {e}")
            return {"task_id": task_id, "status": "UNKNOWN", "error": str(e)}

    async def revoke_task(self, task_id: str, terminate: bool = True) -> bool:
        """
        Revoke/cancel a Celery task.

        Args:
            task_id: Task ID to cancel
            terminate: Whether to terminate running task

        Returns:
            True if revoke signal sent successfully
        """
        try:
            from aiwen.celery_app import celery_app

            celery_app.control.revoke(task_id, terminate=terminate)
            logger.info(f"Revoked task {task_id} (terminate={terminate})")
            return True
        except Exception as e:
            logger.error(f"Error revoking task {task_id}: {e}")
            return False


# Global singleton
_producer_instance: TaskProducer | None = None


def get_task_producer(redis_client: Any = None) -> TaskProducer:
    """
    Get task producer singleton.

    Args:
        redis_client: Kept for backward compatibility, not used

    Returns:
        TaskProducer instance
    """
    global _producer_instance

    if _producer_instance is None:
        _producer_instance = TaskProducer()

    return _producer_instance


# FastAPI dependency injection
async def get_producer_dependency(redis_client: Any = None) -> TaskProducer:
    """
    FastAPI dependency injection function.

    Args:
        redis_client: Kept for backward compatibility

    Returns:
        TaskProducer instance
    """
    return get_task_producer()
