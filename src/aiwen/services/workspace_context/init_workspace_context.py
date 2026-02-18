"""Initialize workspace context with user-selected resources.

Called after workspace creation to pre-populate WorkspaceContext with
tools, knowledge bases, and skills chosen by the user.
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.schemas.workspaces.workspace import WorkspaceContextConfig
from aiwen.services.workspace_context.workspace_context_service import (
    WorkspaceContextService,
)

logger = logging.getLogger(__name__)


async def init_workspace_context(
    db: AsyncSession,
    workspace_id: str | UUID,
    user_id: str | UUID,
    config: WorkspaceContextConfig,
) -> dict[str, int]:
    """Populate WorkspaceContext from user-selected resources.

    Args:
        db: Database session
        workspace_id: Target workspace
        user_id: Owner (used for created_by)
        config: Resource IDs to pre-populate

    Returns:
        dict with counts: {"tools": n, "knowledge": n, "skills": n}
    """
    workspace_id = str(workspace_id)
    user_id_str = str(user_id)
    service = WorkspaceContextService(db, workspace_id)
    counts = {"tools": 0, "knowledge": 0, "skills": 0, "history": 0, "memories": 0}

    if config.tool_ids:
        counts["tools"] = await _populate_tools(
            db, service, user_id_str, config.tool_ids
        )

    if config.knowledge_ids:
        counts["knowledge"] = await _populate_knowledge(
            db, service, user_id_str, config.knowledge_ids
        )

    if config.skill_ids:
        counts["skills"] = await _populate_skills(
            db, service, user_id_str, config.skill_ids
        )

    if config.source_workspace_ids:
        counts["history"] = await _populate_history(
            db, service, user_id_str, config.source_workspace_ids
        )

    if config.memory_ids:
        counts["memories"] = await _populate_memories(
            db, service, user_id_str, config.memory_ids
        )

    logger.info(
        "init_workspace_context: workspace=%s tools=%d knowledge=%d skills=%d history=%d memories=%d",
        workspace_id, counts["tools"], counts["knowledge"], counts["skills"],
        counts["history"], counts["memories"],
    )
    return counts


async def _populate_tools(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    tool_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.models.context.tools import Tool

    result = await db.execute(
        select(Tool).where(Tool.id.in_([str(tid) for tid in tool_ids]))
    )
    tools = result.scalars().all()

    count = 0
    for tool in tools:
        path = f"tools/{tool.tool_code or tool.id}"
        glance = f"{tool.display_name or tool.name} — {tool.description[:60] if tool.description else ''}"
        await service.set(
            path=path,
            glance=glance,
            overview={
                "name": tool.name,
                "display_name": tool.display_name,
                "description": tool.description,
                "tool_type": tool.tool_type,
                "tags": tool.tags or [],
            },
            detail=tool.input_schema,
            tags=["tools"] + (tool.tags or []),
            meta={"tool_id": str(tool.id), "tool_code": tool.tool_code},
            created_by=user_id,
            content_type="application/json",
        )
        count += 1
    return count


async def _populate_knowledge(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    knowledge_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.models.context.knowledge.knowledge import Knowledge

    result = await db.execute(
        select(Knowledge).where(Knowledge.id.in_(knowledge_ids))
    )
    knowledge_list = result.scalars().all()

    count = 0
    for kb in knowledge_list:
        path = f"knowledge/{kb.id}"
        glance = (kb.description[:80] if kb.description else None) or kb.name
        await service.set(
            path=path,
            glance=glance,
            overview=kb.description,
            detail=None,
            tags=["knowledge"],
            meta={"knowledge_id": str(kb.id)},
            created_by=user_id,
            content_type="text/plain",
        )
        count += 1
    return count


async def _populate_skills(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    skill_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.models.context.skill import Skill

    result = await db.execute(
        select(Skill).where(Skill.id.in_(skill_ids))
    )
    skills = result.scalars().all()

    count = 0
    for skill in skills:
        path = f"skills/{skill.id}"
        glance = skill.glance or (skill.description[:60] if skill.description else skill.name)
        await service.set(
            path=path,
            glance=glance,
            overview=skill.summary,
            detail=skill.content,
            tags=["skills"] + (skill.tags or []),
            meta={"skill_id": str(skill.id)},
            created_by=user_id,
            content_type="text/plain",
        )
        count += 1
    return count


async def _populate_memories(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    memory_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.core.enums import ContextType
    from aiwen.models.context.context import Context

    result = await db.execute(
        select(Context).where(
            Context.id.in_([str(mid) for mid in memory_ids]),
            Context.context_type == ContextType.USER_MEMORY,
        )
    )
    memories = result.scalars().all()

    count = 0
    for mem in memories:
        path = f"memory/{mem.id}"
        glance = mem.glance or (mem.summary[:60] if mem.summary else mem.content[:60])
        await service.set(
            path=path,
            glance=glance,
            overview=mem.summary,
            detail=mem.content,
            tags=["memory"] + (mem.tags or []),
            meta={
                "memory_id": str(mem.id),
                "source_context_id": str(mem.id),
                "importance": mem.importance,
            },
            created_by=user_id,
            content_type="text/plain",
        )
        count += 1
    return count


async def _populate_history(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    source_workspace_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.models.context.workspace_context import WorkspaceContext

    count = 0
    for src_id in source_workspace_ids:
        src_id_str = str(src_id)
        result = await db.execute(
            select(WorkspaceContext).where(
                WorkspaceContext.workspace_id == src_id,
                WorkspaceContext.is_deleted.is_(False),
                WorkspaceContext.path.like("/history/%"),
            )
        )
        entries = result.scalars().all()

        for entry in entries:
            # Strip leading "/history/" and re-root under the new workspace.
            suffix = entry.path[len("/history/"):]  # type: ignore[index]
            new_path = f"history/{suffix}"
            await service.set(
                path=new_path,
                glance=entry.glance or entry.name,
                overview=entry.summary,
                detail=entry.content,
                tags=(entry.tags or []) + ["imported_history"],
                meta={
                    **(entry.meta or {}),
                    "source_workspace_id": src_id_str,
                    "source_path": entry.path,
                },
                created_by=user_id,
                content_type=entry.content_type or "application/json",
            )
            count += 1
    return count
