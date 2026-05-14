"""Celery task: sync Knowledge to the Context table."""

import logging

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.context_sync._base import (
    _upsert_context_at_path,
)
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.celery_worker.tasks.workspace_context_sync import (
    _get_user_workspace_ids,
    _invalidate_workspace_caches,
)
from structure.core.enums import ContextType
from structure.plugins.structurers.knowledge_structure import KnowledgeStructurer

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
        from uuid import UUID

        from structure.extensions.database import get_session
        from structure.models.context.context import Context
        from structure.models.context.knowledge.knowledge import Knowledge

        # ── 1. Read knowledge and upsert global Context row ───────────────
        async with get_session("structure") as session:
            from sqlalchemy import delete, select
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
            current_paths = [core.path for core in context_cores if core.path]
            if current_paths:
                await session.execute(
                    delete(Context).where(
                        Context.source_id == UUID(knowledge_id),
                        Context.context_type == ContextType.KNOWLEDGE,
                        Context.path.not_in(current_paths),
                    )
                )
            for core in context_cores:
                await _upsert_context_at_path(
                    session,
                    user_id=user_id,
                    context_type=ContextType.KNOWLEDGE,
                    source_id=knowledge_id,
                    path=core.path,
                    glance=core.glance,
                    content=core.content,
                    tags=["knowledge"],
                    meta={"knowledge_id": knowledge_id},
                )
            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            await session.commit()

        await _invalidate_workspace_caches(workspace_ids)

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_knowledge failed for {knowledge_id}: {e}")
        self.retry(exc=e)
