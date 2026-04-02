"""Celery tasks: sync Tool (external and inner) to the Context table."""

import json
import logging

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.context_sync._base import (
    _fetch_embedding_service,
    _generate_embedding,
    _store_embedding,
    _upsert_context,
)
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.celery_worker.tasks.workspace_context_sync import (
    _get_user_workspace_ids,
    _invalidate_workspace_caches,
    _sync_path_to_workspaces,
    _update_workspace_contexts,
)
from structure.core.enums import ContextType
from structure.utils.context import slugify as _slugify

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
        from structure.extensions.database import get_session
        from structure.models.context.tools.tool import Tool

        # ── 1. Read tool and upsert global Context row ────────────────────
        async with get_session("structure") as session:
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

            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type=ContextType.TOOL,
                source_id=tool_id,
                glance=glance,
                content=schema_str,
                path=f"/tools/{_slugify(tool_name)}",
                tags=["tool"] + tool_tags,  # noqa: RUF005
                meta={"tool_id": tool_id, "tool_code": tool_name},
            )

            await _update_workspace_contexts(
                session,
                meta_key="tool_id",
                resource_id=tool_id,
                glance=glance,
                content=schema_str,
            )

            workspace_ids = await _get_user_workspace_ids(session, user_id)
            await session.flush()
            ctx_id = str(ctx.id)
            tool_description = tool.description
            embed_text = " ".join(filter(None, [display_name, tool_description, schema_str]))
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        # ── 2. Sync WorkspaceContext at tools/{name} for each workspace ───
        path = f"/tools/{_slugify(tool_name)}"
        dirty_ids = await _sync_path_to_workspaces(
            workspace_ids,
            path=path,
            glance=glance,
            overview=tool_description,
            detail=schema_str,
            tags=["tool"] + tool_tags,  # noqa: RUF005
            meta={"tool_id": tool_id, "tool_code": tool_name},
            created_by=user_id,
        )
        await _invalidate_workspace_caches(dirty_ids)

        logger.info(
            f"sync_tool: upserted Context + synced to {len(dirty_ids)} workspace(s) "
            f"at '{path}' for tool {tool_id}"
        )

        # ── 3. Generate embedding only when content changed ────────────────
        if needs_embedding and embed_text.strip():
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_tool: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_tool: content unchanged, skipped re-embedding {ctx_id}")

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
                    Context.context_type == ContextType.TOOL,
                    Context.source_id == UUID(tool_id),
                )
            )
            ctx_ids = [str(row) for row in ctx_result.scalars().all()]
            emb_svc = await _fetch_embedding_service(session)

        # ── 1. Sync WorkspaceContext paths ────────────────────────────────
        dirty_ids = await _sync_path_to_workspaces(
            all_workspace_ids,
            path=f"/tools/{_slugify(tool_name)}",
            glance=glance,
            overview=tool.description,
            detail=schema_str,
            tags=["tool"] + tool_tags,  # noqa: RUF005
            meta={"tool_id": tool_id, "tool_code": tool_name},
            created_by=None,
        )
        await _invalidate_workspace_caches(dirty_ids)

        # ── 2. Generate embedding and store for all per-user Context rows ─
        embed_text = " ".join(filter(None, [display_name, tool.description, schema_str]))
        if embed_text.strip() and ctx_ids:
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
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

        from structure.core.enums.workspaces import WorkspaceStatus
        from structure.extensions.database import get_session
        from structure.models.context.tools.tool import Tool
        from structure.models.workspaces.workspace import Workspace

        async with get_session("structure") as session:
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

                    async with get_session("structure") as session:
                        await _update_workspace_contexts(
                            session,
                            meta_key="tool_id",
                            resource_id=tool_id,
                            glance=glance,
                            content=schema_str,
                        )
                        await session.commit()

                    tool_tags = list(tool.tags or [])
                    dirty_ids = await _sync_path_to_workspaces(
                        all_workspace_ids,
                        path=f"/tools/{_slugify(tool_name)}",
                        glance=glance,
                        overview=tool.description,
                        detail=schema_str,
                        tags=["tool"] + tool_tags,  # noqa: RUF005
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
