"""Celery tasks for syncing resource changes to Context + WorkspaceContext tables.

When a knowledge base, skill, tool, or memory is created or updated, these tasks:
1. Upsert a representative entry in the Context table.
2. Generate an embedding for the Context row and store it in the Context table.
3. Update all WorkspaceContext rows that reference the resource via meta fields.

All vector embeddings live exclusively in the Context table — subsystem models
(Skill, Tool, Knowledge, WorkspaceContext) do not store embeddings.
"""

import json
import logging
import re
from uuid import UUID, uuid4

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.knowledge_tasks import run_async

logger = logging.getLogger(__name__)

# Default embedding settings — must match the dimension supported by Context model.
_DEFAULT_PROVIDER = "tongyi"
_DEFAULT_MODEL = "text-embedding-v3"
_DEFAULT_DIMENSION = 1024


# ─── helpers ──────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert a human-readable name to a safe path segment (lowercase, underscores)."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug or "unnamed"


async def _invalidate_workspace_caches(ws_ids: list[str]) -> None:
    """Set Redis dirty-flags for the given workspace IDs so in-memory caches refresh."""
    if not ws_ids:
        return
    try:
        import redis.asyncio as redis_async

        from aiwen.config.factory import get_settings

        cfg = get_settings().redis
        auth = f":{cfg.password}@" if cfg.password else ""
        r = redis_async.from_url(
            f"redis://{auth}{cfg.host}:{cfg.port}/{cfg.db}",
            decode_responses=True,
        )
        async with r.pipeline(transaction=False) as pipe:
            for ws_id in ws_ids:
                pipe.setex(f"workspace_context_dirty:{ws_id}", 600, "1")
            await pipe.execute()
        await r.aclose()
    except Exception as exc:
        logger.warning(f"_invalidate_workspace_caches: failed to set dirty flags: {exc}")


async def _get_user_workspace_ids(session, user_id: str) -> list[str]:
    """Return all active workspace IDs owned by *user_id*."""
    from sqlalchemy import select

    from aiwen.core.enums.workspaces import WorkspaceStatus
    from aiwen.models.workspaces.workspace import Workspace

    result = await session.execute(
        select(Workspace).where(
            Workspace.owner_id == UUID(user_id),
            Workspace.status == WorkspaceStatus.ACTIVE,
            Workspace.is_deleted.is_(False),
        )
    )
    return [str(ws.id) for ws in result.scalars().all()]


async def _sync_path_to_workspaces(
    workspace_ids: list[str],
    *,
    path: str,
    glance: str,
    overview: str | None,
    detail: str | None,
    tags: list[str],
    meta: dict,
    created_by: str | None = None,
) -> list[str]:
    """Write a WorkspaceContext entry at *path* for each workspace; return dirty IDs."""
    from aiwen.extensions.database import get_session
    from aiwen.services.workspace_context.workspace_context_service import (
        WorkspaceContextService,
    )

    dirty: list[str] = []
    for ws_id in workspace_ids:
        try:
            async with get_session("aiwen") as session:
                svc = WorkspaceContextService(session, ws_id)
                kwargs: dict = {}
                if created_by:
                    kwargs["created_by"] = created_by
                await svc.set(
                    path=path,
                    glance=glance,
                    overview=overview,
                    detail=detail,
                    tags=tags,
                    meta=meta,
                    **kwargs,
                )
            dirty.append(ws_id)
        except Exception as exc:
            logger.warning(f"_sync_path_to_workspaces: failed for workspace {ws_id} path={path}: {exc}")
    return dirty

async def _upsert_context(session, *, user_id: str, context_type: str, source_id: str,
                           name: str, glance: str | None, summary: str | None,
                           content: str | None, tags: list[str] | None = None,
                           meta: dict | None = None):
    """Create or update a Context row identified by (source_id, context_type, user_id).

    Returns the Context instance (not yet committed).
    """
    from sqlalchemy import select

    from aiwen.models.context.context import Context

    merged_meta = {**(meta or {}), "name": name}

    stmt = select(Context).where(
        Context.source_id == UUID(source_id),
        Context.context_type == context_type,
        Context.user_id == UUID(user_id),
    )
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if ctx:
        ctx.glance = glance
        ctx.summary = summary
        ctx.content = content or ctx.content
        if tags is not None:
            ctx.tags = tags
        ctx.meta = {**(ctx.meta or {}), **merged_meta}
        # Clear the embedding so it gets regenerated below
        ctx.embedding_384 = None
        ctx.embedding_768 = None
        ctx.embedding_1024 = None
        ctx.embedding_1536 = None
    else:
        ctx = Context(
            id=uuid4(),
            user_id=UUID(user_id),
            context_type=context_type,
            source_id=UUID(source_id),
            glance=glance,
            summary=summary,
            content=content or "",
            tags=tags or [],
            meta=merged_meta,
        )
        session.add(ctx)

    return ctx


async def _update_workspace_contexts(session, *, meta_key: str, resource_id: str,
                                      glance: str | None, summary: str | None,
                                      content: str | None = None):
    """Update all WorkspaceContext rows whose meta[meta_key] == resource_id."""
    from sqlalchemy import select

    from aiwen.models.context.workspace_context import WorkspaceContext

    stmt = select(WorkspaceContext).where(
        WorkspaceContext.is_deleted.is_(False),
        WorkspaceContext.meta[meta_key].astext == resource_id,
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    for row in rows:
        row.glance = glance
        row.summary = summary
        if content is not None:
            row.content = content

    return len(rows)


def _generate_embedding(text: str, *,
                         provider: str = _DEFAULT_PROVIDER,
                         model: str = _DEFAULT_MODEL,
                         dimension: int = _DEFAULT_DIMENSION) -> tuple[list[float], str]:
    """Embed text synchronously; returns (vector, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    """
    from aiwen.services.context.knowledge.embeddings import EmbeddingService

    svc = EmbeddingService(provider=provider, model=model, dimension=dimension)
    vector = svc.embed_text(text)
    field = svc.get_embedding_field_name()
    return vector, field


async def _store_embedding(session, ctx_id: str, vector: list[float], field: str):
    """Write the embedding vector to an existing Context row."""
    from aiwen.models.context.context import Context

    ctx = await session.get(Context, UUID(ctx_id))
    if ctx is not None:
        setattr(ctx, field, vector)


# ─── tasks ────────────────────────────────────────────────────────────────────

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
            kb = await session.get(Knowledge, knowledge_id)
            if not kb:
                logger.warning(f"sync_knowledge: knowledge {knowledge_id} not found")
                return

            glance = (kb.description[:80] if kb.description else None) or kb.name
            kb_name = kb.name
            kb_description = kb.description
            kb_tags = list(kb.tags or []) if hasattr(kb, "tags") else []

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="knowledge",
                source_id=knowledge_id,
                name=kb_name,
                glance=glance,
                summary=kb_description,
                content=kb_description,
                tags=["knowledge"] + kb_tags,
                meta={"knowledge_id": knowledge_id},
            )

            await _update_workspace_contexts(
                session,
                meta_key="knowledge_id",
                resource_id=knowledge_id,
                glance=glance,
                summary=kb_description,
                content=kb_description,
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            ctx_id = str(ctx.id)
            embed_text = " ".join(filter(None, [kb_name, kb_description]))
            await session.commit()

        # ── 2. Sync WorkspaceContext at knowledge/{name} for each workspace ─
        path = f"knowledge/{_slugify(kb_name)}"
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=path,
            glance=glance,
            overview=kb_description,
            detail=kb_description,
            tags=["knowledge"] + kb_tags,
            meta={"knowledge_id": knowledge_id},
            created_by=user_id,
        )
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"sync_knowledge: upserted Context + synced to {len(dirty_ids)} workspace(s) "
            f"at '{path}' for knowledge {knowledge_id}"
        )

        # ── 3. Generate embedding (outside session, blocking HTTP) ─────────
        if embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_knowledge: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_knowledge failed for {knowledge_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_skill",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_skill_to_contexts(self, skill_id: str, user_id: str):
    """Upsert Skill into Context table, embed, and sync to all user workspaces."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.skill import Skill

        # ── 1. Read skill and upsert global Context row ───────────────────
        async with get_session("aiwen") as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                logger.warning(f"sync_skill: skill {skill_id} not found")
                return

            glance = skill.glance or (skill.description[:80] if skill.description else skill.name)

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="SKILL",
                source_id=skill_id,
                name=skill.name,
                glance=glance,
                summary=skill.summary,
                content=skill.content,
                tags=["skills"] + (skill.tags or []),
                meta={"skill_id": skill_id},
            )

            count = await _update_workspace_contexts(
                session,
                meta_key="skill_id",
                resource_id=skill_id,
                glance=glance,
                summary=skill.summary,
                content=skill.content,
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            ctx_id = str(ctx.id)
            skill_name = skill.name
            skill_summary = skill.summary
            skill_content = skill.content
            skill_tags = list(skill.tags or [])
            embed_text = skill_content or glance or ""
            await session.commit()

        # ── 2. Upsert WorkspaceContext at skills/{name} for each workspace ─
        path = f"skills/{_slugify(skill_name)}"
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=path,
            glance=glance,
            overview=skill_summary,
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

        # ── 3. Generate embedding (outside session, blocking HTTP) ─────────
        if embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_skill: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_skill failed for {skill_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_tool",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_tool_to_contexts(self, tool_id: str, user_id: str):
    """Upsert Tool into Context table, embed, and sync to all user workspaces."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.tools.tool import Tool

        # ── 1. Read tool and upsert global Context row ────────────────────
        async with get_session("aiwen") as session:
            tool = await session.get(Tool, tool_id)
            if not tool:
                logger.warning(f"sync_tool: tool {tool_id} not found")
                return

            tool_name = tool.tool_code or tool.name
            display_name = tool.display_name or tool.name
            glance = f"{display_name} — {tool.description[:60]}" if tool.description else display_name
            tool_tags = list(tool.tags or [])

            # Store the full OpenAI function-calling schema so read_context results
            # can be directly parsed by _extract_context_tool_schemas without needing
            # to reconstruct the description from scattered fields.
            input_schema = tool.input_schema or {}
            full_schema = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool.description or display_name,
                    "parameters": input_schema,
                },
            }
            schema_str = json.dumps(full_schema, ensure_ascii=False)

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="tool",
                source_id=tool_id,
                name=display_name,
                glance=glance,
                summary=tool.description,
                content=schema_str,
                tags=["tool"] + tool_tags,
                meta={"tool_id": tool_id, "tool_code": tool_name},
            )

            await _update_workspace_contexts(
                session,
                meta_key="tool_id",
                resource_id=tool_id,
                glance=glance,
                summary=tool.description,
                content=schema_str,
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            ctx_id = str(ctx.id)
            tool_description = tool.description
            embed_text = " ".join(filter(None, [display_name, tool_description, schema_str]))
            await session.commit()

        # ── 2. Sync WorkspaceContext at tools/{name} for each workspace ───
        path = f"tools/{_slugify(tool_name)}"
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=path,
            glance=glance,
            overview=tool_description,
            detail=schema_str,
            tags=["tool"] + tool_tags,
            meta={"tool_id": tool_id, "tool_code": tool_name},
            created_by=user_id,
        )
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"sync_tool: upserted Context + synced to {len(dirty_ids)} workspace(s) "
            f"at '{path}' for tool {tool_id}"
        )

        # ── 3. Generate embedding (outside session, blocking HTTP) ─────────
        if embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_tool: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_tool failed for {tool_id}: {e}")
        self.retry(exc=e)



@celery_app.task(
    bind=True,
    name="context_sync.sync_inner_tool",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_inner_tool_to_contexts(self, tool_id: str):
    """Sync a single inner tool: generate embeddings for all per-user Context rows
    and upsert WorkspaceContext at tools/{name} for every active workspace.

    Called from ToolRegistry Phase 5 after the Context rows are committed.
    Inner tools have no owner (user_id=NULL in the tool table) so this task
    fans out across all users and workspaces.
    """
    async def _execute():
        from sqlalchemy import select

        from aiwen.core.enums.workspaces import WorkspaceStatus
        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context
        from aiwen.models.context.tools.tool import Tool
        from aiwen.models.workspaces.workspace import Workspace

        async with get_session("aiwen") as session:
            tool = await session.get(Tool, tool_id)
            if not tool:
                logger.warning(f"sync_inner_tool: tool {tool_id} not found")
                return

            tool_name = tool.tool_code or tool.name
            display_name = tool.display_name or tool.name
            glance = f"{display_name} — {(tool.description or '')[:60]}"
            input_schema = tool.input_schema or {}
            full_schema = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool.description or display_name,
                    "parameters": input_schema,
                },
            }
            schema_str = json.dumps(full_schema, ensure_ascii=False)
            tool_tags = list(tool.tags or [])

            # All active workspaces (inner tools are global)
            ws_result = await session.execute(
                select(Workspace.id).where(
                    Workspace.status == WorkspaceStatus.ACTIVE,
                    Workspace.is_deleted.is_(False),
                )
            )
            all_workspace_ids = [str(row) for row in ws_result.scalars().all()]

            # All Context rows for this inner tool (one per user, created by Phase 5)
            ctx_result = await session.execute(
                select(Context.id).where(
                    Context.context_type == "tool",
                    Context.source_id == UUID(tool_id),
                )
            )
            ctx_ids = [str(row) for row in ctx_result.scalars().all()]

        # ── 1. Sync WorkspaceContext paths ────────────────────────────────
        dirty_ids = await _sync_path_to_workspaces(
            all_workspace_ids,
            path=f"tools/{_slugify(tool_name)}",
            glance=glance,
            overview=tool.description,
            detail=schema_str,
            tags=["tool"] + tool_tags,
            meta={"tool_id": tool_id, "tool_code": tool_name},
            created_by=None,
        )
        await _invalidate_workspace_caches(dirty_ids)

        # ── 2. Generate embedding and store for all per-user Context rows ─
        embed_text = " ".join(filter(None, [display_name, tool.description, schema_str]))
        if embed_text.strip() and ctx_ids:
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                for ctx_id in ctx_ids:
                    await _store_embedding(session, ctx_id, vector, field)
                await session.commit()

        logger.info(
            f"sync_inner_tool: tool={tool_name} synced {len(dirty_ids)} workspace(s), "
            f"embedded {len(ctx_ids)} context row(s)"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_inner_tool failed for {tool_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.delete_resource_contexts",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def delete_resource_contexts(self, resource_id: str, context_type: str, meta_key: str):
    """Delete Context and WorkspaceContext entries for a removed resource.

    Args:
        resource_id: UUID string of the deleted skill / tool / knowledge.
        context_type: Value stored in Context.context_type (e.g. "SKILL", "tool", "knowledge").
        meta_key: JSON meta field used to locate WorkspaceContext rows (e.g. "skill_id").
    """
    async def _execute():
        from sqlalchemy import delete, select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context
        from aiwen.models.context.workspace_context import WorkspaceContext

        async with get_session("aiwen") as session:
            # Delete global Context rows
            ctx_result = await session.execute(
                select(Context).where(
                    Context.source_id == UUID(resource_id),
                    Context.context_type == context_type,
                )
            )
            ctx_rows = ctx_result.scalars().all()
            for ctx in ctx_rows:
                await session.delete(ctx)

            # Soft-delete WorkspaceContext rows that reference this resource
            wc_result = await session.execute(
                select(WorkspaceContext).where(
                    WorkspaceContext.is_deleted.is_(False),
                    WorkspaceContext.meta[meta_key].astext == resource_id,
                )
            )
            wc_rows = wc_result.scalars().all()
            for row in wc_rows:
                row.is_deleted = True

            await session.commit()

        dirty_ids = list({str(row.workspace_id) for row in wc_rows})
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"delete_resource_contexts: removed {len(ctx_rows)} Context row(s) and "
            f"soft-deleted {len(wc_rows)} WorkspaceContext row(s) for {context_type}/{resource_id}"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"delete_resource_contexts failed for {resource_id}: {e}")
        self.retry(exc=e)


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

            glance = mem.glance or (mem.summary[:80] if mem.summary else None) or (
                mem.content[:80] if mem.content else "Memory"
            )

            # Clear stale embedding so it gets regenerated
            mem.embedding_384 = None
            mem.embedding_768 = None
            mem.embedding_1024 = None
            mem.embedding_1536 = None

            count = await _update_workspace_contexts(
                session,
                meta_key="memory_id",
                resource_id=memory_id,
                glance=glance,
                summary=mem.summary,
                content=mem.content,
            )

            embed_text = " ".join(filter(None, [mem.glance, mem.content]))
            await session.commit()

        logger.info(
            f"sync_memory: cleared embedding + updated {count} WorkspaceContext(s) "
            f"for memory {memory_id}"
        )

        # ── 2. Generate embedding (outside session, blocking HTTP) ─────────
        if embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, memory_id, vector, field)
                await session.commit()
            logger.info(f"sync_memory: embedded Context {memory_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_memory failed for {memory_id}: {e}")
        self.retry(exc=e)


# ─── workspace / run / event tasks ────────────────────────────────────────────

# Event types too noisy to include in a run's event log context
_NOISE_EVENT_TYPES = frozenset({"agent.token", "agent_token"})


@celery_app.task(
    bind=True,
    name="context_sync.sync_workspace",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_workspace_to_contexts(self, workspace_id: str, user_id: str):
    """Upsert a Workspace into the Context table and generate its embedding.

    Called when a workspace is created or its name/description changes.
    """
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.workspaces.workspace import Workspace

        async with get_session("aiwen") as session:
            ws = await session.get(Workspace, UUID(workspace_id))
            if not ws:
                logger.warning(f"sync_workspace: workspace {workspace_id} not found")
                return

            content = "\n".join(filter(None, [ws.name, ws.description]))

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="workspace",
                source_id=workspace_id,
                name=ws.name,
                glance=ws.name,
                summary=ws.description,
                content=content,
                tags=["workspace", str(ws.status)],
                meta={
                    "workspace_id": workspace_id,
                    "status": str(ws.status),
                    "run_count": ws.run_count,
                    "owner_id": str(ws.owner_id),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            await session.commit()

        logger.info(f"sync_workspace: upserted Context for workspace {workspace_id}")

        if content.strip():
            vector, field = _generate_embedding(content)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_workspace: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_workspace failed for {workspace_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_run",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_run_to_contexts(self, run_id: str, user_id: str):
    """Upsert a completed Run into the Context table as a conversation record.

    Only terminal runs (finished / failed / cancelled) are stored.
    Content = user input + agent output, making past conversations semantically searchable.
    """
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run

        async with get_session("aiwen") as session:
            run = await session.get(Run, UUID(run_id))
            if not run:
                logger.warning(f"sync_run: run {run_id} not found")
                return

            if run.status not in ("finished", "failed", "cancelled"):
                logger.info(
                    f"sync_run: run {run_id} is not terminal ({run.status}), skipping"
                )
                return

            input_msg = (run.input_data or {}).get("message", "")
            output_msg = (run.output_data or {}).get("message", "") or str(run.output_data or "")

            glance = input_msg[:80] if input_msg else f"Run {run_id[:8]}"
            summary = f"[{run.status}] {input_msg[:200]}"

            parts = []
            if input_msg:
                parts.append(f"User: {input_msg}")
            if output_msg:
                parts.append(f"Assistant: {output_msg}")
            content = "\n\n".join(parts) or f"Run {run_id} ({run.status})"

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="run",
                source_id=run_id,
                name=glance,
                glance=glance,
                summary=summary,
                content=content,
                tags=["run", run.status],
                meta={
                    "run_id": run_id,
                    "workspace_id": str(run.workspace_id),
                    "status": run.status,
                    "app_id": str(run.app_id) if run.app_id else None,
                    "trigger_type": str(run.trigger_type),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            await session.commit()

        logger.info(f"sync_run: upserted Context for run {run_id} ({run.status})")

        if content.strip():
            vector, field = _generate_embedding(content[:2000])
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_run: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_run failed for {run_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_run_events",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_run_events_to_context(self, run_id: str, user_id: str):
    """Aggregate a run's events into a single Context row as a structured timeline.

    Noise events (agent.token etc.) are filtered out.
    Each remaining event is formatted as: [seq] event_type: payload_preview
    The resulting text is embedded for semantic retrieval of past agent behaviour.
    """
    async def _execute():
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.events.event import Event

        async with get_session("aiwen") as session:
            stmt = (
                select(Event)
                .where(
                    Event.run_id == UUID(run_id),
                    Event.event_type.not_in(list(_NOISE_EVENT_TYPES)),
                )
                .order_by(Event.sequence.asc())
            )
            result = await session.execute(stmt)
            events = list(result.scalars().all())

            if not events:
                logger.info(f"sync_run_events: no events for run {run_id}")
                return

            lines = []
            for ev in events:
                text = ev.to_context()
                if text is not None:
                    lines.append(f"[{ev.sequence:03d}] {text}")

            content = "\n".join(lines)
            glance = f"{len(events)} events — run {run_id[:8]}"
            summary = f"{events[0].event_type} → {events[-1].event_type} ({len(events)} events)"
            workspace_id = str(events[0].workspace_id)

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="run_events",
                source_id=run_id,
                name=glance,
                glance=glance,
                summary=summary,
                content=content,
                tags=["events", "run"],
                meta={
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "event_count": len(events),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            await session.commit()

        logger.info(
            f"sync_run_events: aggregated {len(events)} events for run {run_id}"
        )

        embed_text = content[:2000]  # cap to avoid oversized embedding inputs
        if embed_text.strip():
            vector, field = _generate_embedding(embed_text)
            async with get_session("aiwen") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_run_events: embedded Context {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_run_events failed for {run_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.resync_all_tools",
    max_retries=1,
    queue="default",
)
def resync_all_tools_to_contexts(self):
    """Re-sync every enabled tool so its Context/WorkspaceContext content uses
    the current full OpenAI function-calling schema format.

    - ExternalTools: dispatched with their creator's user_id.
    - InnerTools (user_id=NULL): content is rebuilt and all WorkspaceContext
      rows are updated directly, without going through user workspace lookup.
    """
    async def _execute():
        from sqlalchemy import select

        from aiwen.core.enums.workspaces import WorkspaceStatus
        from aiwen.extensions.database import get_session
        from aiwen.models.context.tools.tool import Tool
        from aiwen.models.workspaces.workspace import Workspace

        async with get_session("aiwen") as session:
            result = await session.execute(
                select(Tool).where(Tool.enabled.is_(True))
            )
            tools = result.scalars().all()

            # Fetch all active workspace IDs once (used for inner tools)
            ws_result = await session.execute(
                select(Workspace.id).where(
                    Workspace.status == WorkspaceStatus.ACTIVE,
                    Workspace.is_deleted.is_(False),
                )
            )
            all_workspace_ids = [str(row) for row in ws_result.scalars().all()]

        dispatched = 0
        skipped = 0
        for tool in tools:
            tool_id = str(tool.id)
            if tool.tool_type == "external" and tool.user_id:
                sync_tool_to_contexts.delay(tool_id, str(tool.user_id))
                dispatched += 1
            elif tool.tool_type == "inner":
                # InnerTools have no owner; rebuild content directly for all
                # WorkspaceContext rows that already reference this tool.
                try:
                    tool_name = tool.tool_code or tool.name
                    display_name = tool.display_name or tool.name
                    input_schema = tool.input_schema or {}
                    full_schema = {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": tool.description or display_name,
                            "parameters": input_schema,
                        },
                    }
                    schema_str = json.dumps(full_schema, ensure_ascii=False)
                    glance = (
                        f"{display_name} — {tool.description[:60]}"
                        if tool.description
                        else display_name
                    )

                    async with get_session("aiwen") as session:
                        await _update_workspace_contexts(
                            session,
                            meta_key="tool_id",
                            resource_id=tool_id,
                            glance=glance,
                            summary=tool.description,
                            content=schema_str,
                        )
                        await session.commit()

                    tool_tags = list(tool.tags or [])
                    dirty_ids = await _sync_path_to_workspaces(
                        all_workspace_ids,
                        path=f"tools/{_slugify(tool_name)}",
                        glance=glance,
                        overview=tool.description,
                        detail=schema_str,
                        tags=["tool"] + tool_tags,
                        meta={"tool_id": tool_id, "tool_code": tool_name},
                        created_by=None,
                    )
                    await _invalidate_workspace_caches(dirty_ids)
                    dispatched += 1
                except Exception as exc:
                    logger.error(f"resync_all_tools: failed for inner tool {tool_id}: {exc}")
                    skipped += 1
            else:
                skipped += 1

        logger.info(
            f"resync_all_tools: dispatched={dispatched}, skipped={skipped}, "
            f"total_tools={len(tools)}"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"resync_all_tools failed: {e}")
        self.retry(exc=e)
