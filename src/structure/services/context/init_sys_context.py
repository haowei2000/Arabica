import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums import ContextType
from structure.models.context import Context

logger = logging.getLogger(__name__)
# Default directory nodes that every workspace must have.
# These are skeleton/folder entries — no content, just structural anchors.
DEFAULT_CONTEXT_PATHS: list[dict] = [
    {"path": "/", "name": "Root", "glance": "Workspace root context"},
    {"path": "/tools", "name": "Tools", "glance": "Available Tools"},
    {"path": "/skills", "name": "Skills", "glance": "Available Skills"},
    {"path": "/knowledge", "name": "Knowledge", "glance": "Knowledge Base"},
    {"path": "/memory", "name": "Memory", "glance": "Agent Memory"},
]


async def ensure_default_context_paths(
        db: AsyncSession,
) -> int:
    """Ensure every default context path exists for *workspace_id*.

    Idempotent — paths that already exist are skipped.

    Args:
        db: Database session.

    Returns:
        Number of new paths created.
    """


    # Look up workspace owner to satisfy Context.user_id (required)

    # Fetch existing default paths already scoped to this workspace
    existing_result = await db.execute(
        select(Context.path).where(
            Context.path.in_([d["path"] for d in DEFAULT_CONTEXT_PATHS]),
        )
    )
    existing_paths = {row[0] for row in existing_result.all()}

    count = 0
    for entry in DEFAULT_CONTEXT_PATHS:
        if entry["path"] in existing_paths:
            continue
        ctx = Context(
            user_id="00000000-0000-0000-0000-000000000000",
            path=entry["path"],
            context_type=ContextType.WORKSPACE,
            glance=entry["glance"],
            content=entry["name"],
            tags=["default", "directory"],
            meta={"default": True},
        )
        db.add(ctx)
        count += 1

    if count:
        await db.flush()

    logger.info(
        "ensure_default_context_paths: created=%d skipped=%d",
        count,
        len(DEFAULT_CONTEXT_PATHS) - count,
    )
    return count
