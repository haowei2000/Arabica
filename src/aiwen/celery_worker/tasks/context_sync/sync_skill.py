"""Celery task: sync Skill to the Context table."""

import logging

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.knowledge_tasks import run_async
from aiwen.celery_worker.tasks.workspace_context_sync import (
    _get_user_workspace_ids,
    _invalidate_workspace_caches,
    _sync_path_to_workspaces,
    _update_workspace_contexts,
)
from aiwen.celery_worker.tasks.context_sync._base import (
    _generate_embedding,
    _store_embedding,
    _upsert_context,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="context_sync.sync_skill",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_skill_to_contexts(self, skill_id: str, user_id: str, content: str | None = None):
    """Upsert Skill into Context table, embed, and sync to all user workspaces.

    ``content`` is the Markdown body of the skill.  When ``None`` (e.g. on a
    manual re-trigger), the content already stored in the Context row is kept
    and the embedding is regenerated only if missing.
    """
    async def _execute():
        from sqlalchemy import select
        from uuid import UUID

        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context
        from aiwen.models.context.skill import Skill

        # ── 1. Read skill and upsert global Context row ───────────────────
        async with get_session("aiwen") as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                logger.warning(f"sync_skill: skill {skill_id} not found")
                return

            glance = (skill.description[:80] if skill.description else None) or skill.name

            # When content is not provided, read existing Context content so
            # _upsert_context can do a proper change-detection comparison.
            effective_content = content
            if effective_content is None:
                existing = (await session.execute(
                    select(Context).where(
                        Context.source_id == UUID(skill_id),
                        Context.context_type == "SKILL",
                        Context.user_id == UUID(user_id),
                    )
                )).scalar_one_or_none()
                if existing:
                    effective_content = existing.content

            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type="SKILL",
                source_id=skill_id,
                glance=glance,
                content=effective_content,
                tags=["skills"] + (skill.tags or []),
                meta={"skill_id": skill_id},
            )

            # Force re-embed when the embedding was never generated (e.g. first
            # Celery run after ContextSyncer created the row without embedding).
            if not needs_embedding and ctx.embedding_1024 is None:
                needs_embedding = True

            count = await _update_workspace_contexts(
                session,
                meta_key="skill_id",
                resource_id=skill_id,
                glance=glance,
                content=effective_content or "",
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            ctx_id = str(ctx.id)
            skill_name = skill.name
            skill_content = effective_content or ""
            skill_tags = list(skill.tags or [])
            embed_text = skill_content or glance or ""
            await session.commit()

        # ── 2. Upsert WorkspaceContext at skills/{name} for each workspace ─
        path = f"skills/{_slugify(skill_name)}"
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=path,
            glance=glance,
            detail=skill_content,
            tags=["skills"] + skill_tags,
            meta={"skill_id": skill_id},
            created_by=user_id,
        )
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"sync_skill: upserted Context + updated {count} WorkspaceContext row(s) "
            f"+ synced to {len(dirty_ids)} workspace(s) at '{path}' for skill {skill_id}"
        )

        # ── 3. Generate embedding only when content changed ────────────────
        if needs_embedding and embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_skill: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_skill: content unchanged, skipped re-embedding {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_skill failed for {skill_id}: {e}")
        self.retry(exc=e)
