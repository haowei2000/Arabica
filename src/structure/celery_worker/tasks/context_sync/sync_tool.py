"""Celery tasks: sync Tool (external and inner) to the Context table."""

import logging

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.context_sync._base import (
    _fetch_embedding_service,
    _generate_embedding,
    _store_embedding,
    _upsert_context_at_path,
)
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.celery_worker.tasks.workspace_context_sync import (
    _get_user_workspace_ids,
    _invalidate_workspace_caches,
    _sync_path_to_workspaces,
)
from structure.core.enums import ContextType
from structure.services.context.tool_context import (
    build_tool_context_entries,
    build_tool_index_entry,
)

logger = logging.getLogger(__name__)


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
        from uuid import UUID

        from structure.extensions.database import get_session
        from structure.models.context.tools.tool import Tool

        # ── 1. Read tool and upsert global Context row ────────────────────
        async with get_session("structure") as session:
            tool = await session.get(Tool, tool_id)
            if not tool:
                logger.warning(f"sync_tool: tool {tool_id} not found")
                return

            from sqlalchemy import or_, select

            from structure.services.context.context_crud import ContextCRUD

            entries = build_tool_context_entries(tool)
            embed_jobs: list[tuple[str, str]] = []
            user_uuid = UUID(user_id)

            for entry in entries:
                ctx, needs_embedding = await _upsert_context_at_path(
                    session,
                    user_id=user_id,
                    context_type=ContextType.TOOL,
                    source_id=tool_id,
                    path=entry.path,
                    glance=entry.glance,
                    content=entry.content,
                    tags=entry.tags,
                    meta=entry.meta,
                )
                if entry.embed and needs_embedding and entry.content.strip():
                    embed_jobs.append((str(ctx.id), entry.content))

            tools_result = await session.execute(
                select(Tool).where(
                    Tool.enabled.is_(True),
                    or_(Tool.user_id == user_uuid, Tool.tool_type == "inner"),
                )
            )
            index_entry = build_tool_index_entry(list(tools_result.scalars().all()))
            index_ctx = await ContextCRUD(session).upsert_by_path(
                user_id=user_id,
                path=index_entry.path,
                data={
                    "context_type": ContextType.TOOL,
                    "glance": index_entry.glance,
                    "content": index_entry.content,
                    "tags": index_entry.tags,
                    "meta": index_entry.meta,
                },
            )
            if index_entry.content.strip():
                embed_jobs.append((str(index_ctx.id), index_entry.content))
            workspace_ids = await _get_user_workspace_ids(session, user_id)
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        # ── 2. Sync structured WorkspaceContext entries for each workspace ───
        dirty_set: set[str] = set()
        for entry in [*entries, index_entry]:
            dirty_ids = await _sync_path_to_workspaces(
                workspace_ids,
                path=entry.path,
                glance=entry.glance,
                overview=None,
                detail=entry.content,
                tags=entry.tags,
                meta=entry.meta,
                created_by=user_id,
            )
            dirty_set.update(dirty_ids)
        await _invalidate_workspace_caches(list(dirty_set))

        logger.info(
            f"sync_tool: upserted structured Context + synced to "
            f"{len(dirty_set)} workspace(s) for tool {tool_id}"
        )

        # ── 3. Generate embedding only when content changed ────────────────
        for ctx_id, embed_text in embed_jobs:
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
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
        from uuid import UUID

        from sqlalchemy import select

        from structure.core.enums.workspaces import WorkspaceStatus
        from structure.extensions.database import get_session
        from structure.models.context.context import Context
        from structure.models.context.tools.tool import Tool
        from structure.models.workspaces.workspace import Workspace

        async with get_session("structure") as session:
            tool = await session.get(Tool, tool_id)
            if not tool:
                logger.warning(f"sync_inner_tool: tool {tool_id} not found")
                return

            tool_name = tool.name
            display_name = tool.display_name or tool.name
            entries = build_tool_context_entries(tool)

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
                    Context.context_type == ContextType.TOOL,
                    Context.source_id == UUID(tool_id),
                )
            )
            ctx_ids = [str(row) for row in ctx_result.scalars().all()]
            emb_svc = await _fetch_embedding_service(session)
            tools_result = await session.execute(
                select(Tool).where(Tool.enabled.is_(True))
            )
            index_entry = build_tool_index_entry(list(tools_result.scalars().all()))

        # ── 1. Sync WorkspaceContext paths ────────────────────────────────
        dirty_set: set[str] = set()
        for entry in [*entries, index_entry]:
            dirty_ids = await _sync_path_to_workspaces(
                all_workspace_ids,
                path=entry.path,
                glance=entry.glance,
                overview=None,
                detail=entry.content,
                tags=entry.tags,
                meta=entry.meta,
                created_by=None,
            )
            dirty_set.update(dirty_ids)
        await _invalidate_workspace_caches(list(dirty_set))

        # ── 2. Generate embedding and store for all per-user Context rows ─
        embed_text = " ".join(
            filter(
                None,
                [
                    display_name,
                    tool.description,
                    *(e.content for e in entries if e.embed),
                ],
            )
        )
        if embed_text.strip() and ctx_ids:
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
                for ctx_id in ctx_ids:
                    await _store_embedding(session, ctx_id, vector, field)
                await session.commit()

        logger.info(
            f"sync_inner_tool: tool={tool_name} synced {len(dirty_set)} workspace(s), "
            f"embedded {len(ctx_ids)} context row(s)"
        )

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_inner_tool failed for {tool_id}: {e}")
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

        from structure.extensions.database import get_session
        from structure.models.context.tools.tool import Tool

        async with get_session("structure") as session:
            result = await session.execute(select(Tool).where(Tool.enabled.is_(True)))
            tools = result.scalars().all()

        dispatched = 0
        skipped = 0
        for tool in tools:
            tool_id = str(tool.id)
            if tool.tool_type in {"external", "mcp"} and tool.user_id:
                sync_tool_to_contexts.delay(tool_id, str(tool.user_id))
                dispatched += 1
            elif tool.tool_type == "inner":
                sync_inner_tool_to_contexts.delay(tool_id)
                dispatched += 1
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
