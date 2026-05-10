"""Celery tasks for event archive/GC operations."""

from __future__ import annotations

import logging
from typing import Any

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.knowledge_tasks import run_async

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="events.archive_run_memory",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def archive_run_memory(
    self,
    run_id: str,
    user_id: str,
    options: dict[str, Any] | None = None,
):
    """Archive active event memory for one run."""

    async def _execute():
        from structure.extensions.database import get_session
        from structure.services.events.event_archive import EventArchiveService

        opts = options or {}
        async with get_session("structure") as session:
            service = EventArchiveService(session)
            result = await service.archive_run_memory(
                run_id,
                user_id=user_id,
                keep_last=opts.get("keep_last"),
                include_pinned=opts.get("include_pinned", False),
                event_types=opts.get("event_types"),
                strategy=opts.get("strategy", "event_count_ttl"),
                strategy_config=opts.get("strategy_config"),
                dry_run=opts.get("dry_run", False),
                reason=opts.get("reason", "event_gc_task"),
                max_events_per_archive_context=opts.get(
                    "max_events_per_archive_context",
                    500,
                ),
                max_chars_per_archive_context=opts.get(
                    "max_chars_per_archive_context",
                    200_000,
                ),
                bulk_update_chunk_size=opts.get("bulk_update_chunk_size", 1000),
            )
            if not result.dry_run:
                await session.commit()
            return result.as_dict()

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error("archive_run_memory failed for %s: %s", run_id, exc)
        self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="events.archive_workspace_memory",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def archive_workspace_memory(
    self,
    workspace_id: str,
    user_id: str,
    options: dict[str, Any] | None = None,
):
    """Archive active event memory for a workspace."""

    async def _execute():
        from structure.extensions.database import get_session
        from structure.services.events.event_archive import EventArchiveService

        opts = options or {}
        async with get_session("structure") as session:
            service = EventArchiveService(session)
            result = await service.archive_workspace_memory(
                workspace_id,
                user_id=user_id,
                keep_last=opts.get("keep_last"),
                include_pinned=opts.get("include_pinned", False),
                include_run_events=opts.get("include_run_events", True),
                event_types=opts.get("event_types"),
                strategy=opts.get("strategy", "event_count_ttl"),
                strategy_config=opts.get("strategy_config"),
                dry_run=opts.get("dry_run", False),
                reason=opts.get("reason", "event_gc_task"),
                max_events_per_archive_context=opts.get(
                    "max_events_per_archive_context",
                    500,
                ),
                max_chars_per_archive_context=opts.get(
                    "max_chars_per_archive_context",
                    200_000,
                ),
                bulk_update_chunk_size=opts.get("bulk_update_chunk_size", 1000),
            )
            if not result.dry_run:
                await session.commit()
            return result.as_dict()

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error("archive_workspace_memory failed for %s: %s", workspace_id, exc)
        self.retry(exc=exc)
