"""Celery tasks: sync memory Context rows and delete resource contexts."""

import logging

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.context_sync._base import (
    _fetch_embedding_service,
    _generate_embedding,
    _store_embedding,
)
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.celery_worker.tasks.workspace_context_sync import (
    _invalidate_workspace_caches,
    _update_workspace_contexts,
)
from structure.utils.context import build_context_path

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="context_sync.sync_memory",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_memory_to_contexts(self, memory_id: str, user_id: str):  # noqa: ARG001
    """Embed a memory Context row and sync WorkspaceContext entries.

    Memories are already stored in the Context table; this task generates the
    embedding and propagates changes to any WorkspaceContext references.
    """

    async def _execute():
        from structure.core.enums import ContextType
        from structure.extensions.database import get_session
        from structure.models.context.context import Context

        # ── 1. Read memory, clear old embedding, sync WorkspaceContext ────
        async with get_session("structure") as session:
            mem = await session.get(Context, memory_id)
            if not mem or mem.context_type != ContextType.SHORT_MEMORY:
                logger.warning(f"sync_memory: memory {memory_id} not found")
                return

            glance = mem.glance or (mem.content[:80] if mem.content else "Memory")

            # Back-fill path for rows created before path was set
            if not mem.path:
                if mem.glance:
                    mem.path = build_context_path("memory", mem.glance[:50])
                elif mem.source_id:
                    mem.path = build_context_path(
                        "memory", f"run-{str(mem.source_id)[:8]}"
                    )
                else:
                    mem.path = build_context_path("memory", str(mem.id)[:8])

            # Only clear and regenerate if no embedding exists yet
            needs_embedding = mem.embedding_1024 is None
            if needs_embedding:
                mem.embedding_384 = None
                mem.embedding_768 = None
                mem.embedding_1024 = None
                mem.embedding_1536 = None

            count, dirty_ids = await _update_workspace_contexts(
                session,
                meta_key="memory_id",
                resource_id=memory_id,
                glance=glance,
                content=mem.content,
            )

            embed_text = " ".join(filter(None, [mem.glance, mem.content]))
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        logger.info(
            f"sync_memory: updated {count} WorkspaceContext(s) for memory {memory_id}"
        )
        await _invalidate_workspace_caches(dirty_ids, raise_on_error=True)

        # ── 2. Generate embedding only when missing ────────────────────────
        if needs_embedding and embed_text.strip():
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
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
        from uuid import UUID

        from sqlalchemy import delete, select

        from structure.core.enums.context import ContextScope
        from structure.extensions.database import get_session
        from structure.models.context.context import Context

        async with get_session("structure") as session:
            # Collect workspace IDs before deleting workspace-scoped rows
            wc_result = await session.execute(
                select(Context.meta).where(
                    Context.scope == ContextScope.WORKSPACE,
                    Context.meta[meta_key].astext == resource_id,
                )
            )
            dirty_ids = list(
                {str(row[0].get("workspace_id")) for row in wc_result.all() if row[0]}
            )

            # Bulk delete user-scoped Context rows matched by source_id + type
            user_del = await session.execute(
                delete(Context).where(
                    Context.source_id == UUID(resource_id),
                    Context.context_type == context_type,
                    Context.scope == ContextScope.USER,
                )
            )
            ctx_count = user_del.rowcount

            # Also delete user-scoped Context rows matched by meta key (e.g. document
            # chunks that store knowledge_id in meta but have document_id as source_id)
            meta_user_del = await session.execute(
                delete(Context).where(
                    Context.scope == ContextScope.USER,
                    Context.meta[meta_key].astext == resource_id,
                )
            )
            ctx_count += meta_user_del.rowcount

            # Bulk delete workspace-scoped Context rows
            ws_del = await session.execute(
                delete(Context).where(
                    Context.scope == ContextScope.WORKSPACE,
                    Context.meta[meta_key].astext == resource_id,
                )
            )
            wc_count = ws_del.rowcount

            await _invalidate_workspace_caches(dirty_ids, raise_on_error=True)
            await session.commit()

        logger.info(
            f"delete_resource_contexts: removed {ctx_count} user Context row(s) and "
            f"{wc_count} workspace Context row(s) for {context_type}/{resource_id}"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"delete_resource_contexts failed for {resource_id}: {e}")
        self.retry(exc=e)
