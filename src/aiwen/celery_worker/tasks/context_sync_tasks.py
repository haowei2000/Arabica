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
from uuid import UUID, uuid4

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.document_tasks import run_async

logger = logging.getLogger(__name__)

# Default embedding settings — must match the dimension supported by Context model.
_DEFAULT_PROVIDER = "tongyi"
_DEFAULT_MODEL = "text-embedding-v3"
_DEFAULT_DIMENSION = 1024


# ─── helpers ──────────────────────────────────────────────────────────────────

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
    """Upsert Knowledge into Context table, embed, and update WorkspaceContext entries."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.knowledge.knowledge import Knowledge

        # ── 1. Read knowledge and upsert Context row ──────────────────────
        async with get_session("aiwen") as session:
            kb = await session.get(Knowledge, knowledge_id)
            if not kb:
                logger.warning(f"sync_knowledge: knowledge {knowledge_id} not found")
                return

            glance = (kb.description[:80] if kb.description else None) or kb.name

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="knowledge",
                source_id=knowledge_id,
                name=kb.name,
                glance=glance,
                summary=kb.description,
                content=kb.description,
                tags=["knowledge"],
                meta={"knowledge_id": knowledge_id},
            )

            count = await _update_workspace_contexts(
                session,
                meta_key="knowledge_id",
                resource_id=knowledge_id,
                glance=glance,
                summary=kb.description,
            )

            await session.flush()
            ctx_id = str(ctx.id)
            embed_text = " ".join(filter(None, [kb.name, kb.description]))
            await session.commit()

        logger.info(
            f"sync_knowledge: upserted Context + updated {count} WorkspaceContext(s) "
            f"for knowledge {knowledge_id}"
        )

        # ── 2. Generate embedding (outside session, blocking HTTP) ─────────
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
    """Upsert Skill into Context table, embed, and update WorkspaceContext entries."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.skill import Skill

        # ── 1. Read skill and upsert Context row ──────────────────────────
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

            await session.flush()
            ctx_id = str(ctx.id)
            embed_text = skill.content or glance or ""
            await session.commit()

        logger.info(
            f"sync_skill: upserted Context + updated {count} WorkspaceContext(s) "
            f"for skill {skill_id}"
        )

        # ── 2. Generate embedding (outside session, blocking HTTP) ─────────
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
    """Upsert Tool into Context table, embed, and update WorkspaceContext entries."""
    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.context.tools.tool import Tool

        # ── 1. Read tool and upsert Context row ───────────────────────────
        async with get_session("aiwen") as session:
            tool = await session.get(Tool, tool_id)
            if not tool:
                logger.warning(f"sync_tool: tool {tool_id} not found")
                return

            glance = f"{tool.display_name or tool.name} — {tool.description[:60] if tool.description else ''}"
            overview = {
                "name": tool.name,
                "display_name": tool.display_name,
                "description": tool.description,
                "tool_type": tool.tool_type,
                "tags": tool.tags or [],
            }
            overview_str = json.dumps(overview, ensure_ascii=False)
            schema_str = json.dumps(tool.input_schema, ensure_ascii=False) if tool.input_schema else ""

            ctx = await _upsert_context(
                session,
                user_id=user_id,
                context_type="tool",
                source_id=tool_id,
                name=tool.display_name or tool.name,
                glance=glance,
                summary=overview_str,
                content=schema_str,
                tags=["tool"] + (tool.tags or []),
                meta={"tool_id": tool_id, "tool_code": tool.tool_code},
            )

            count = await _update_workspace_contexts(
                session,
                meta_key="tool_id",
                resource_id=tool_id,
                glance=glance,
                summary=overview_str,
            )

            await session.flush()
            ctx_id = str(ctx.id)
            # Embed name + description + schema for rich tool retrieval
            embed_text = " ".join(filter(None, [
                tool.display_name or tool.name,
                tool.description,
                schema_str,
            ]))
            await session.commit()

        logger.info(
            f"sync_tool: upserted Context + updated {count} WorkspaceContext(s) "
            f"for tool {tool_id}"
        )

        # ── 2. Generate embedding (outside session, blocking HTTP) ─────────
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
            if not mem or mem.context_type != ContextType.USER_MEMORY:
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
