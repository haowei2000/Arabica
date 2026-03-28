"""Celery task: sync Knowledge to the Context table."""

import logging

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.context_sync._base import (
    _upsert_context,
)
from aiwen.celery_worker.tasks.knowledge_tasks import run_async
from aiwen.core.enums import ContextType
from aiwen.plugins.structurers.knowledge_structure import KnowledgeStructurer

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="context_sync.sync_knowledge",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_knowledge_to_contexts(self, knowledge_id: str, user_id: str):
    """Upsert Knowledge into the Context table, embed, and sync to all user workspaces."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.knowledge.knowledge import Knowledge

        # ── 1. Read knowledge and upsert global Context row ───────────────
        async with get_session("aiwen") as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            stmt = (
                select(Knowledge)
                .where(Knowledge.id == knowledge_id)
                .options(selectinload(Knowledge.documents))
            )
            result = await session.execute(stmt)
            kb = result.scalar_one_or_none()
            if not kb:
                logger.warning(f"sync_knowledge: knowledge {knowledge_id} not found")
                return
            context_cores = KnowledgeStructurer().structure(kb)
            for core in context_cores:
                await _upsert_context(
                    session,
                    user_id=user_id,
                    context_type=ContextType.KNOWLEDGE,
                    source_id=knowledge_id,
                    path=core.path,
                    glance=core.glance,
                    content=core.content,
                    tags=[],
                )
            await session.flush()
            await session.commit()
    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_knowledge failed for {knowledge_id}: {e}")
        self.retry(exc=e)
