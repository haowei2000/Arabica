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
    _upsert_context_at_path,
)
from aiwen.core.enums import ContextType
from aiwen.utils.context import slugify as _slugify

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="context_sync.sync_skill",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_skill_to_contexts(self, skill_id: str, user_id: str, content: str | None = None):
    """Upsert Skill into Context table using SkillStructurer, embed, and sync workspaces.

    Stores one Context row per ContextCore produced by SkillStructurer:
    - Level 1: overview entry with file-tree listing (path = skills/{name})
    - Level 2+3: per-file section chunks (path = skills/{name}/file/section)
    """
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.skill import Skill
        from aiwen.plugins.structurers.skill_structure import SkillStructurer

        # ── 1. Load skill and run structurer ─────────────────────────────
        async with get_session("aiwen") as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                logger.warning(f"sync_skill: skill {skill_id} not found")
                return

            # Inject caller-supplied content into skill object so the
            # structurer sees it when building text-based (non-folder) skills.
            if content is not None:
                skill.content = content  # transient attribute — not committed

            glance = (skill.description[:80] if skill.description else None) or skill.name
            skill_name = skill.name
            skill_tags = list(skill.tags or [])
            skill_path = f"skills/{_slugify(skill_name)}"

            try:
                context_cores = SkillStructurer().structure(skill)
            except Exception as exc:
                logger.warning(
                    f"sync_skill: SkillStructurer failed for {skill_id} ({exc}), "
                    "falling back to overview-only"
                )
                # Minimal fallback: single overview entry
                from aiwen.schemas.context.context_schema import ContextCore
                context_cores = [ContextCore(glance=glance, content=content or "", path=skill_path)]

            ctx_ids_need_embed: list[tuple[str, str]] = []  # (ctx_id, embed_text)

            for core in context_cores:
                path = core.path or skill_path
                ctx, needs_embedding = await _upsert_context_at_path(
                    session,
                    user_id=user_id,
                    context_type=ContextType.SKILL,
                    source_id=skill_id,
                    path=path,
                    glance=core.glance or glance,
                    content=core.content,
                    tags=["skills"] + skill_tags,
                    meta={"skill_id": skill_id},
                )
                await session.flush()
                if needs_embedding:
                    embed_text = " ".join(filter(None, [core.glance, core.content]))
                    ctx_ids_need_embed.append((str(ctx.id), embed_text))

            # Use first (overview) entry for workspace sync
            overview_content = context_cores[0].content if context_cores else ""

            await _update_workspace_contexts(
                session,
                meta_key="skill_id",
                resource_id=skill_id,
                glance=glance,
                content=overview_content,
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.commit()

        # ── 2. Sync WorkspaceContext at skills/{name} for each workspace ─
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=skill_path,
            glance=glance,
            detail=overview_content,
            tags=["skills"] + skill_tags,
            meta={"skill_id": skill_id},
            created_by=user_id,
        )
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"sync_skill: stored {len(context_cores)} context chunk(s) for skill {skill_id}, "
            f"synced to {len(dirty_ids)} workspace(s) at '{skill_path}'"
        )

        # ── 3. Generate embeddings for changed entries ────────────────────
        for ctx_id, embed_text in ctx_ids_need_embed:
            if embed_text.strip():
                vector, field = _generate_embedding(embed_text)
                async with get_session("aiwen") as session:
                    await _store_embedding(session, ctx_id, vector, field)
                    await session.commit()

        if ctx_ids_need_embed:
            logger.info(f"sync_skill: embedded {len(ctx_ids_need_embed)} chunk(s) for {skill_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_skill failed for {skill_id}: {e}")
        self.retry(exc=e)
