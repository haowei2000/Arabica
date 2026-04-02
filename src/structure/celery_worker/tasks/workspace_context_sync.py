"""Helpers for propagating resource changes into workspace-scoped Context entries.

Each resource (tool, skill, knowledge, memory) that belongs to a user is mirrored
as a ``Context`` row with ``scope=WORKSPACE`` and ``source_id=workspace_id`` for
every workspace owned by that user.  These helpers handle the write path.
"""

from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


async def _invalidate_workspace_caches(ws_ids: list[str]) -> None:
    """Set Redis dirty-flags for the given workspace IDs so in-memory caches refresh."""
    if not ws_ids:
        return
    try:
        import redis.asyncio as redis_async

        from structure.config.factory import get_settings

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
        logger.warning("_invalidate_workspace_caches: failed to set dirty flags: %s", exc)


async def _get_user_workspace_ids(session, user_id: str) -> list[str]:
    """Return all active workspace IDs owned by *user_id*."""
    from sqlalchemy import select

    from structure.core.enums.workspaces import WorkspaceStatus
    from structure.models.workspaces.workspace import Workspace

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
    overview: str | None,  # noqa: ARG001
    detail: str | None,
    tags: list[str],
    meta: dict,
    created_by: str | None = None,
) -> list[str]:
    """Upsert a workspace-scoped Context entry at *path* for each workspace.

    Uses ``scope=WORKSPACE`` and ``source_id=workspace_id`` to identify entries.
    Returns the list of workspace IDs that were actually written (dirty IDs).
    """
    from sqlalchemy import select

    from structure.core.enums.context import ContextScope, ContextType
    from structure.extensions.database import get_session
    from structure.models.context.context import Context
    from structure.models.workspaces.workspace import Workspace

    dirty: list[str] = []
    for ws_id in workspace_ids:
        try:
            workspace_uuid = UUID(ws_id)
            async with get_session("structure") as session:
                # Resolve user_id: prefer created_by, fall back to workspace owner
                if created_by:
                    user_uuid = UUID(created_by)
                else:
                    owner = await session.execute(
                        select(Workspace.owner_id).where(Workspace.id == workspace_uuid)
                    )
                    user_uuid = owner.scalar_one_or_none()
                    if user_uuid is None:
                        logger.warning(
                            "_sync_path_to_workspaces: workspace %s not found", ws_id
                        )
                        continue

                result = await session.execute(
                    select(Context).where(
                        Context.source_id == workspace_uuid,
                        Context.scope == ContextScope.WORKSPACE,
                        Context.path == path,
                    )
                )
                ctx = result.scalar_one_or_none()

                if ctx is None:
                    ctx = Context(
                        user_id=user_uuid,
                        source_id=workspace_uuid,
                        scope=ContextScope.WORKSPACE,
                        context_type=ContextType.WORKSPACE,
                        path=path,
                        glance=glance,
                        content=detail or "",
                        tags=tags,
                        meta={**meta, "workspace_id": ws_id},
                    )
                    session.add(ctx)
                else:
                    ctx.glance = glance
                    ctx.content = detail or ctx.content
                    ctx.tags = tags
                    ctx.meta = {**meta, "workspace_id": ws_id}

                await session.commit()
            dirty.append(ws_id)
        except Exception as exc:
            logger.warning(
                "_sync_path_to_workspaces: failed for workspace %s path=%s: %s",
                ws_id,
                path,
                exc,
            )
    return dirty


async def _update_workspace_contexts(
    session,
    *,
    meta_key: str,
    resource_id: str,
    glance: str | None,
    content: str | None = None,
) -> int:
    """Update all workspace-scoped Context rows whose meta[meta_key] == resource_id."""
    from sqlalchemy import select

    from structure.core.enums.context import ContextScope
    from structure.models.context.context import Context

    stmt = select(Context).where(
        Context.scope == ContextScope.WORKSPACE,
        Context.meta[meta_key].astext == resource_id,
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    for row in rows:
        row.glance = glance
        if content is not None:
            row.content = content

    return len(rows)
