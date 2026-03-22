"""Initialize workspace context by creating Context entries scoped to a workspace.

On workspace creation, call ``init_workspace_context`` to copy all global
Context entries owned by the workspace owner into new workspace-scoped
Context entries (identified by ``source_id == workspace_id``).

The global Context table is the single source of truth — it is kept up to
date automatically by ContextSyncer whenever tools, skills, knowledge bases,
triggers, or workspaces are created / updated / deleted.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.context import ContextScope, ContextType
from aiwen.models.context.context import Context

logger = logging.getLogger(__name__)


async def init_workspace_context(
    db: AsyncSession,
    workspace_id: str | UUID,
    user_id: str | UUID,
) -> int:
    """Copy all context entries for *user_id* into workspace-scoped Context entries.

    Each copied entry is linked to the workspace via ``source_id = workspace_id``
    and assigned ``scope = ContextScope.WORKSPACE``.

    Args:
        db: Database session.
        workspace_id: ID of the newly created workspace.
        user_id: Owner of the workspace (determines which context rows to copy).

    Returns:
        Number of context entries copied.
    """
    workspace_uuid = UUID(str(workspace_id))
    user_uuid = UUID(str(user_id))

    # Fetch all user-scoped context entries owned by this user
    result = await db.execute(
        select(Context)
        .where(
            Context.user_id == user_uuid,
            Context.scope == ContextScope.USER,
        )
        .order_by(Context.path)
    )
    contexts = result.scalars().all()

    count = 0
    for ctx in contexts:
        raw_path = ctx.path or str(ctx.id)
        path = "/" + raw_path.lstrip("/")

        summary_str = ctx.summary
        if isinstance(summary_str, dict):
            summary_str = json.dumps(summary_str, ensure_ascii=False)

        entry = Context(
            user_id=user_uuid,
            source_id=workspace_uuid,
            scope=ContextScope.WORKSPACE,
            path=path,
            context_type=ctx.context_type,
            glance=ctx.glance,
            summary=summary_str,
            content=ctx.content,
            tags=ctx.tags or [],
            meta={
                **(ctx.meta or {}),
                "source_context_id": str(ctx.id),
                "workspace_id": str(workspace_uuid),
            },
        )
        db.add(entry)
        count += 1

    if count:
        await db.flush()

    logger.info(
        "init_workspace_context: workspace=%s user=%s copied=%d",
        str(workspace_uuid),
        user_id,
        count,
    )
    return count


