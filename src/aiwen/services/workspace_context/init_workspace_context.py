"""Initialize workspace context by copying from the global Context table.

On workspace creation, call ``init_workspace_context`` to snapshot all
context entries owned by the workspace owner into the WorkspaceContext table.

The global Context table is the single source of truth — it is kept up to
date automatically by ContextSyncer whenever tools, skills, knowledge bases,
triggers, or workspaces are created / updated / deleted.

WorkspaceContext is a lightweight snapshot taken once at workspace creation.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.context import Context
from aiwen.models.context.workspace_context import WorkspaceContext

logger = logging.getLogger(__name__)


async def init_workspace_context(
    db: AsyncSession,
    workspace_id: str | UUID,
    user_id: str | UUID,
) -> int:
    """Copy all context entries for *user_id* into the WorkspaceContext table.

    Args:
        db: Database session.
        workspace_id: ID of the newly created workspace.
        user_id: Owner of the workspace (determines which context rows to copy).

    Returns:
        Number of context entries copied.
    """
    workspace_id_str = str(workspace_id)
    user_uuid = UUID(str(user_id))

    # Fetch all context entries owned by this user
    result = await db.execute(
        select(Context).where(Context.user_id == user_uuid).order_by(Context.path)
    )
    contexts = result.scalars().all()

    count = 0
    for ctx in contexts:
        # Normalise path — workspace_context paths don't carry the leading /
        raw_path = ctx.path or str(ctx.id)
        path = "/" + raw_path.lstrip("/")

        # Serialize overview (summary) to string if it looks like JSON
        summary_str = ctx.summary
        if isinstance(summary_str, dict):
            summary_str = json.dumps(summary_str, ensure_ascii=False)

        wc = WorkspaceContext(
            workspace_id=workspace_id_str,
            path=path,
            name=ctx.glance or raw_path.rsplit("/", 1)[-1],
            glance=ctx.glance,
            summary=summary_str,
            content=ctx.content,
            tags=ctx.tags or [],
            meta={
                **(ctx.meta or {}),
                "source_context_id": str(ctx.id),
                "context_type": ctx.context_type,
                "source_id": str(ctx.source_id) if ctx.source_id else None,
            },
            created_by=user_uuid,
            content_type="text/plain",
        )
        db.add(wc)
        count += 1

    if count:
        await db.flush()

    logger.info(
        "init_workspace_context: workspace=%s user=%s copied=%d",
        workspace_id_str,
        user_id,
        count,
    )
    return count
