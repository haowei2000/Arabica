"""Helpers for propagating resource changes into workspace-scoped Context entries.

Each resource (tool, skill, knowledge, memory) that belongs to a user is mirrored
as a ``Context`` row with ``scope=WORKSPACE`` and ``source_id=workspace_id`` for
every workspace owned by that user.  These helpers handle the write path.
"""

from __future__ import annotations

import logging
from uuid import UUID

from structure.utils.context import context_path_variants, normalize_context_path

logger = logging.getLogger(__name__)


class WorkspaceCacheInvalidationError(RuntimeError):
    """Raised when cache dirty flags cannot be persisted for retryable tasks."""


def _path_variants(path: str) -> list[str]:
    """Return canonical and legacy path forms for compatibility lookups."""
    return context_path_variants(path)


async def _lock_workspace_contexts(session, workspace_ids: list[UUID]) -> None:
    """Acquire transaction-scoped per-workspace locks when running on PostgreSQL."""
    if not workspace_ids:
        return

    try:
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
    except Exception:
        dialect_name = ""

    if dialect_name != "postgresql":
        return

    from sqlalchemy import text

    for workspace_id in sorted({str(ws_id) for ws_id in workspace_ids}):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"workspace_context:{workspace_id}"},
        )


async def _invalidate_workspace_caches(
    ws_ids: list[str],
    *,
    raise_on_error: bool = False,
) -> list[str]:
    """Set Redis dirty-flags for the given workspace IDs so in-memory caches refresh."""
    unique_ids = sorted({str(ws_id) for ws_id in ws_ids if ws_id})
    if not unique_ids:
        return []

    from structure.utils.workspace_context_cache import (
        invalidate_workspace_context_cache,
    )

    for ws_id in unique_ids:
        invalidate_workspace_context_cache(ws_id)

    redis_client = None
    try:
        import redis.asyncio as redis_async

        from structure.config.factory import get_settings

        cfg = get_settings().redis
        auth = f":{cfg.password}@" if cfg.password else ""
        redis_client = redis_async.from_url(
            f"redis://{auth}{cfg.host}:{cfg.port}/{cfg.db}",
            decode_responses=True,
        )
        async with redis_client.pipeline(transaction=False) as pipe:
            for ws_id in unique_ids:
                pipe.setex(f"workspace_context_dirty:{ws_id}", 600, "1")
            await pipe.execute()
        return []
    except Exception as exc:
        logger.warning(
            "_invalidate_workspace_caches: failed to set dirty flags: %s", exc
        )
        if raise_on_error:
            raise WorkspaceCacheInvalidationError(
                f"Failed to invalidate workspace context caches: {unique_ids}"
            ) from exc
        return unique_ids
    finally:
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                logger.debug(
                    "_invalidate_workspace_caches: failed to close redis client",
                    exc_info=True,
                )


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
    overview: str | None = None,  # noqa: ARG001
    detail: str | None = None,
    tags: list[str] | None = None,
    meta: dict | None = None,
    created_by: str | None = None,
) -> list[str]:
    """Upsert a workspace-scoped Context entry at *path* for each workspace.

    Uses one transaction for the whole batch and transaction-scoped advisory
    locks per workspace to avoid reinit/sync interleaving. Returns affected
    workspace IDs so retry paths can still refresh caches after idempotent writes.
    """
    from sqlalchemy import select

    from structure.core.enums.context import ContextScope, ContextType
    from structure.core.enums.workspaces import WorkspaceStatus
    from structure.extensions.database import get_session
    from structure.models.context.context import Context
    from structure.models.workspaces.workspace import Workspace

    workspace_uuids: list[UUID] = []
    for ws_id in sorted({str(ws_id) for ws_id in workspace_ids if ws_id}):
        try:
            workspace_uuids.append(UUID(ws_id))
        except ValueError:
            logger.warning("_sync_path_to_workspaces: invalid workspace id %s", ws_id)

    if not workspace_uuids:
        return []

    normalized_path = normalize_context_path(path) or "/"

    async with get_session("structure") as session:
        workspace_result = await session.execute(
            select(Workspace.id, Workspace.owner_id).where(
                Workspace.id.in_(workspace_uuids),
                Workspace.status == WorkspaceStatus.ACTIVE,
                Workspace.is_deleted.is_(False),
            )
        )
        owner_by_workspace = dict(workspace_result.all())

        missing_ids = sorted(
            str(ws_id) for ws_id in workspace_uuids if ws_id not in owner_by_workspace
        )
        for ws_id in missing_ids:
            logger.warning("_sync_path_to_workspaces: workspace %s not found", ws_id)

        active_workspace_ids = sorted(owner_by_workspace, key=str)
        if not active_workspace_ids:
            return []

        await _lock_workspace_contexts(session, active_workspace_ids)

        ctx_result = await session.execute(
            select(Context).where(
                Context.source_id.in_(active_workspace_ids),
                Context.scope == ContextScope.WORKSPACE,
                Context.path.in_(_path_variants(normalized_path)),
            )
        )
        contexts = list(ctx_result.scalars().all())
        contexts.sort(key=lambda ctx: 0 if ctx.path == normalized_path else 1)

        existing_by_workspace: dict[UUID, Context] = {}
        for ctx in contexts:
            if ctx.source_id and ctx.source_id not in existing_by_workspace:
                existing_by_workspace[ctx.source_id] = ctx

        for workspace_uuid in active_workspace_ids:
            ws_id = str(workspace_uuid)
            user_uuid = (
                UUID(created_by) if created_by else owner_by_workspace[workspace_uuid]
            )
            next_meta = {**(meta or {}), "workspace_id": ws_id}
            next_tags = list(tags or [])
            next_content = detail or ""
            ctx = existing_by_workspace.get(workspace_uuid)

            if ctx is None:
                ctx = Context(
                    user_id=user_uuid,
                    source_id=workspace_uuid,
                    scope=ContextScope.WORKSPACE,
                    context_type=ContextType.WORKSPACE,
                    path=normalized_path,
                    glance=glance,
                    content=next_content,
                    tags=next_tags,
                    meta=next_meta,
                )
                session.add(ctx)
                continue

            content_to_store = detail if detail is not None else ctx.content
            ctx.user_id = user_uuid
            ctx.path = normalized_path
            ctx.glance = glance
            ctx.content = content_to_store
            ctx.tags = next_tags
            ctx.meta = next_meta

        await session.flush()
        return [str(workspace_id) for workspace_id in active_workspace_ids]


async def _update_workspace_contexts(
    session,
    *,
    meta_key: str,
    resource_id: str,
    glance: str | None,
    content: str | None = None,
) -> tuple[int, list[str]]:
    """Update workspace-scoped Context rows whose meta[meta_key] == resource_id."""
    from sqlalchemy import select

    from structure.core.enums.context import ContextScope
    from structure.models.context.context import Context

    stmt = select(Context).where(
        Context.scope == ContextScope.WORKSPACE,
        Context.meta[meta_key].astext == resource_id,
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    workspace_ids = [row.source_id for row in rows if row.source_id is not None]

    await _lock_workspace_contexts(session, workspace_ids)

    for row in rows:
        content_to_store = content if content is not None else row.content
        row.glance = glance
        row.content = content_to_store

    return len(rows), sorted({str(workspace_id) for workspace_id in workspace_ids})
