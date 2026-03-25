"""Celery tasks: sync memory Context rows and delete resource contexts."""

import logging

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.knowledge_tasks import run_async
from aiwen.celery_worker.tasks.workspace_context_sync import (
    _invalidate_workspace_caches,
    _update_workspace_contexts,
)
from aiwen.celery_worker.tasks.context_sync._base import (
    _generate_embedding,
    _store_embedding,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="context_sync.sync_memory",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_memory_to_contexts(self, memory_id: str, user_id: str):
    """Embed a memory Context row and sync WorkspaceContext entries.

    Memories are already stored in the Context table; this task generates the
    embedding and propagates changes to any WorkspaceContext references.
    """
    async def _execute():
        from aiwen.core.enums import ContextType
        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context

        # ── 1. Read memory, clear old embedding, sync WorkspaceContext ────
        async with get_session("aiwen") as session:
            mem = await session.get(Context, memory_id)
            if not mem or mem.context_type != ContextType.SHORT_MEMORY:
                logger.warning(f"sync_memory: memory {memory_id} not found")
                return

            glance = mem.glance or (mem.content[:80] if mem.content else "Memory")

            # Only clear and regenerate if no embedding exists yet
            needs_embedding = mem.embedding_1024 is None
            if needs_embedding:
                mem.embedding_384 = None
                mem.embedding_768 = None
                mem.embedding_1024 = None
                mem.embedding_1536 = None

            count = await _update_workspace_contexts(
                session,
                meta_key="memory_id",
                resource_id=memory_id,
                glance=glance,
                content=mem.content,
            )

            embed_text = " ".join(filter(None, [mem.glance, mem.content]))
            await session.commit()

        logger.info(
            f"sync_memory: updated {count} WorkspaceContext(s) for memory {memory_id}"
        )

        # ── 2. Generate embedding only when missing ────────────────────────
        if needs_embedding and embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, memory_id, vector, field)
                await session.commit()
            logger.info(f"sync_memory: embedded Context {memory_id}")
        elif not needs_embedding:
            logger.debug(f"sync_memory: embedding already present, skipped {memory_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_memory failed for {memory_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.delete_resource_contexts",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def delete_resource_contexts(self, resource_id: str, context_type: str, meta_key: str):
    """Delete Context entries (user-scoped and workspace-scoped) for a removed resource.

    Args:
        resource_id: UUID string of the deleted skill / tool / knowledge.
        context_type: Value stored in Context.context_type (e.g. "SKILL", "tool", "knowledge").
        meta_key: JSON meta field used to locate workspace-scoped Context rows (e.g. "skill_id").
    """
    async def _execute():
        from sqlalchemy import select
        from uuid import UUID

        from aiwen.core.enums.context import ContextScope
        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context

        async with get_session("aiwen") as session:
            # Delete user-scoped Context rows
            ctx_result = await session.execute(
                select(Context).where(
                    Context.source_id == UUID(resource_id),
                    Context.context_type == context_type,
                    Context.scope == ContextScope.USER,
                )
            )
            ctx_rows = ctx_result.scalars().all()
            for ctx in ctx_rows:
                await session.delete(ctx)

            # Delete workspace-scoped Context rows that reference this resource
            wc_result = await session.execute(
                select(Context).where(
                    Context.scope == ContextScope.WORKSPACE,
                    Context.meta[meta_key].astext == resource_id,
                )
            )
            wc_rows = wc_result.scalars().all()
            dirty_ids = list({str(row.meta.get("workspace_id")) for row in wc_rows if row.meta})
            for row in wc_rows:
                await session.delete(row)

            await session.commit()

        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"delete_resource_contexts: removed {len(ctx_rows)} user Context row(s) and "
            f"{len(wc_rows)} workspace Context row(s) for {context_type}/{resource_id}"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"delete_resource_contexts failed for {resource_id}: {e}")
        self.retry(exc=e)
