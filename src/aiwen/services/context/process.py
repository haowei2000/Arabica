"""Workspace context processing — copy from Context, batch operations."""

from __future__ import annotations

from datetime import datetime
import logging
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.context import Context
from aiwen.models.context.workspace_context import WorkspaceContext

logger = logging.getLogger(__name__)


async def copy_contexts_to_workspace(
    db: AsyncSession,
    workspace_id: UUID,
    context_ids: list[UUID],
    *,
    created_by: UUID | None = None,
    path_prefix: str | None = None,
    expires_at: datetime | None = None,
    auto_commit: bool = False,
) -> list[WorkspaceContext]:
    """Copy one or more Context rows into a workspace's temporary context space.

    For each source ``Context``, a new ``WorkspaceContext`` row is created with
    the content, metadata, path and s3_key carried over.  Fields that only
    exist on ``Context`` (embeddings, importance, keywords, etc.) are packed
    into ``WorkspaceContext.meta["source"]`` so nothing is silently lost.

    Args:
        db: Active async database session.
        workspace_id: Target workspace to copy into.
        context_ids: IDs of the ``Context`` rows to copy.
        created_by: User performing the copy (stored on each new row).
        path_prefix: Optional prefix prepended to the source ``path``
            (e.g. ``"/imported"`` → ``"/imported/orig_path"``).
        expires_at: Optional expiration timestamp for the copied entries.
        auto_commit: Commit the transaction when done; otherwise only flush.

    Returns:
        List of newly created ``WorkspaceContext`` instances.
    """
    if not context_ids:
        return []

    # Fetch source contexts in one query
    stmt = select(Context).where(Context.id.in_(context_ids))
    result = await db.execute(stmt)
    contexts = list(result.scalars().all())

    if not contexts:
        logger.warning(
            "copy_contexts_to_workspace: none of the %d context_ids found",
            len(context_ids),
        )
        return []

    created: list[WorkspaceContext] = []

    for ctx in contexts:
        # Build the virtual path
        target_path = ctx.path
        if path_prefix and target_path:
            target_path = f"{path_prefix.rstrip('/')}/{target_path.lstrip('/')}"
        elif path_prefix:
            target_path = path_prefix

        # Derive a display name from the context
        name = _derive_name(ctx)

        # Preserve source metadata that doesn't map 1:1
        source_meta: dict = {
            "source_context_id": str(ctx.id),
            "source_user_id": str(ctx.user_id),
            "context_type": ctx.context_type,
        }
        if ctx.keywords:
            source_meta["keywords"] = ctx.keywords
        if ctx.importance:
            source_meta["importance"] = ctx.importance
        if ctx.summary:
            source_meta["summary"] = ctx.summary

        meta = dict(ctx.meta) if ctx.meta else {}
        meta["source"] = source_meta

        ws_ctx = WorkspaceContext(
            workspace_id=workspace_id,
            created_by=created_by,
            path=target_path,
            name=name,
            content_type=_guess_content_type(ctx),
            content=ctx.content,
            s3_key=ctx.s3_key,
            size_bytes=len(ctx.content.encode("utf-8")) if ctx.content else None,
            meta=meta,
            expires_at=expires_at,
        )
        db.add(ws_ctx)
        created.append(ws_ctx)

    if auto_commit:
        await db.commit()
    else:
        await db.flush()

    for ws_ctx in created:
        await db.refresh(ws_ctx)

    logger.info(
        "Copied %d/%d contexts into workspace %s",
        len(created),
        len(context_ids),
        workspace_id,
    )
    return created


async def copy_contexts_to_workspace_by_filter(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    *,
    context_type: str | None = None,
    source_id: UUID | None = None,
    created_by: UUID | None = None,
    path_prefix: str | None = None,
    expires_at: datetime | None = None,
    auto_commit: bool = False,
) -> list[WorkspaceContext]:
    """Copy all Context rows matching a filter into a workspace.

    Convenience wrapper around :func:`copy_contexts_to_workspace` that first
    queries ``Context`` by ``user_id`` and optional filters, then copies the
    matched rows.

    Args:
        db: Active async database session.
        workspace_id: Target workspace.
        user_id: Owner of the source contexts.
        context_type: Optional filter on ``context_type``.
        source_id: Optional filter on ``source_id``.
        created_by: User performing the copy.
        path_prefix: Optional path prefix for the copied entries.
        expires_at: Optional expiration timestamp.
        auto_commit: Commit when done.

    Returns:
        List of newly created ``WorkspaceContext`` instances.
    """
    conditions = [Context.user_id == user_id]
    if context_type:
        conditions.append(Context.context_type == context_type)
    if source_id:
        conditions.append(Context.source_id == source_id)

    stmt = select(Context.id).where(and_(*conditions))
    result = await db.execute(stmt)
    context_ids = [row[0] for row in result.all()]

    return await copy_contexts_to_workspace(
        db,
        workspace_id,
        context_ids,
        created_by=created_by,
        path_prefix=path_prefix,
        expires_at=expires_at,
        auto_commit=auto_commit,
    )


# ── private helpers ──────────────────────────────────────────────────────────


def _derive_name(ctx: Context) -> str:
    """Build a short display name from a Context row."""
    # Use path filename if available
    if ctx.path:
        parts = ctx.path.rstrip("/").rsplit("/", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
        if parts[0]:
            return parts[0]

    # Use summary truncated, or first line of content
    if ctx.summary:
        return ctx.summary[:120]

    if ctx.content:
        first_line = ctx.content.split("\n", 1)[0].strip()
        return first_line[:120] if first_line else f"context-{ctx.id}"

    return f"context-{ctx.id}"


def _guess_content_type(ctx: Context) -> str | None:
    """Best-effort MIME type from context_type."""
    mapping = {
        "conversation": "text/plain",
        "history": "text/plain",
        "knowledge": "text/plain",
        "chunk": "text/plain",
        "tool": "application/json",
    }
    return mapping.get(ctx.context_type)
