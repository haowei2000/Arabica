#!/usr/bin/env python3
"""
Celery Tasks - Agent task processing

Provides Celery tasks for processing agent requests asynchronously.
Events are published to Redis Streams for SSE consumption.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from aiwen.celery_app import celery_app, get_redis_url
from aiwen.config.factory import get_settings
from aiwen.extensions.database import get_session
from aiwen.services.agents.agent_registry import AgentRegistry
from aiwen.services.agents.app_factory import AppAgentFactory
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.utils.json_utils import dumps as json_dumps

logger = logging.getLogger(__name__)


async def _get_redis_client() -> redis_async.Redis:
    """Create async Redis client."""
    return redis_async.from_url(get_redis_url())


async def _publish_event(
    redis_client: redis_async.Redis,
    task_id: UUID,
    event_data: dict[str, Any],
) -> None:
    """
    Publish task event to Redis Stream.

    Args:
        redis_client: Redis async client
        task_id: Task ID
        event_data: Event data (contains event and data fields)
    """
    try:
        stream_name = f"agent:task:chat:{task_id}:events"
        fields = {
            "event": event_data.get("event", "chunk"),
            "payload": json_dumps(event_data.get("data", "")),
        }
        await redis_client.xadd(
            name=stream_name,
            fields=fields,
            maxlen=1000,
            approximate=True,
        )
        logger.debug(f"Published event '{fields['event']}' to stream {stream_name}")
    except Exception as e:
        logger.error(f"Error publishing event for task {task_id}: {e}")


async def _execute_agent_task(
    task_id: str,
    app_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute agent task asynchronously.

    Args:
        task_id: Task ID string
        app_id: Application ID string
        payload: Task payload

    Returns:
        Result data dict
    """
    task_uuid = UUID(task_id)
    redis_client = await _get_redis_client()

    try:
        async with get_session("aiwen") as db:
            task_crud = AgentTaskCRUD(db)
            app_crud = AppCRUD(db)
            template_crud = AgentTemplateCRUD(db)

            # Update task status to running
            await task_crud.update_agent_task_status(task_uuid, "running")

            try:
                # 1. Get app from database
                app = await app_crud.get_app_by_id(
                    UUID(app_id) if isinstance(app_id, str) else app_id
                )
                if not app:
                    raise ValueError(f"App with ID '{app_id}' not found")

                # 2. Get agent template
                if not app.agent_template_id:
                    raise ValueError("App has no associated agent template")

                template = await template_crud.get_template_by_id(app.agent_template_id)
                if not template:
                    raise ValueError(f"Agent template '{app.agent_template_id}' not found")

                # 3. Validate template registration
                logger.info(f"Checking if template '{template.template_code}' is registered...")
                if not AgentRegistry.is_registered(template.template_code):
                    available_templates = AgentRegistry.list()
                    raise ValueError(
                        f"Agent template '{template.template_code}' is not registered. "
                        f"Available: {available_templates}"
                    )

                # 4. Create agent instance
                factory = AppAgentFactory(
                    appid=str(app.id),
                    template_code=template.template_code,
                    app_config=app.config or {},
                )
                agent_instance = factory.create(payload)
                logger.info(f"Agent instance created for task {task_id}, type: {template.template_code}")

                # 5. Stream execution and collect results
                collected_chunks = []
                async for chunk in agent_instance.stream(payload):
                    collected_chunks.append(chunk)
                    await _publish_event(
                        redis_client,
                        task_uuid,
                        {"event": "chunk", "data": chunk},
                    )

                # 6. Prepare result
                full_result = "".join(collected_chunks) if collected_chunks else ""
                result_data = {"answer": full_result, "chunks_count": len(collected_chunks)}

                # 7. Update task status to success
                await task_crud.update_agent_task_status(task_uuid, "success", result=result_data)
                await _publish_event(
                    redis_client,
                    task_uuid,
                    {"event": "success", "data": json_dumps(result_data)},
                )

                logger.info(f"Task {task_id} completed with {len(collected_chunks)} chunks")
                return result_data

            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}", exc_info=True)
                error_msg = str(e)
                await task_crud.update_agent_task_status(task_uuid, "failed", error=error_msg)
                await _publish_event(
                    redis_client,
                    task_uuid,
                    {"event": "failed", "data": error_msg},
                )
                raise

    finally:
        await redis_client.aclose()


@celery_app.task(
    bind=True,
    name="aiwen.workers.tasks.process_agent_task",
    max_retries=0,
    acks_late=True,
)
def process_agent_task(
    self,
    task_id: str,
    app_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Celery task for processing agent requests.

    Args:
        self: Celery task instance
        task_id: Task ID string
        app_id: Application ID string
        payload: Task payload dict

    Returns:
        Result data dict
    """
    logger.info(f"Processing task {task_id} for app {app_id}")

    try:
        # Run async code in a new event loop
        result = asyncio.run(
            _execute_agent_task(task_id, app_id, payload)
        )
        return result

    except SoftTimeLimitExceeded:
        logger.error(f"Task {task_id} exceeded time limit")
        # Publish timeout event
        asyncio.run(_publish_timeout_event(task_id))
        raise

    except Exception as e:
        logger.error(f"Task {task_id} failed with error: {e}", exc_info=True)
        raise


async def _publish_timeout_event(task_id: str) -> None:
    """Publish timeout event when task exceeds time limit."""
    redis_client = await _get_redis_client()
    try:
        await _publish_event(
            redis_client,
            UUID(task_id),
            {"event": "failed", "data": "Task exceeded time limit"},
        )
    finally:
        await redis_client.aclose()
