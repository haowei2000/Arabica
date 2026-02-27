"""Initialize the workspace context with user-selected resources.

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
    counts = {"tools": 0, "knowledge": 0, "skills": 0, "history": 0, "memories": 0, "triggers": 0}

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

    if config.trigger_ids:
        counts["triggers"] = await _populate_triggers(
            db, UUID(workspace_id), UUID(user_id_str), config.trigger_ids
        )

    # Create root "/" and first-level directory nodes so that glance_context
    # and list_context on "/" return a meaningful directory structure.
    await _create_root_structure(service, user_id_str, counts)

    logger.info(
        "init_workspace_context: workspace=%s tools=%d knowledge=%d skills=%d history=%d memories=%d triggers=%d",
        workspace_id, counts["tools"], counts["knowledge"], counts["skills"],
        counts["history"], counts["memories"], counts["triggers"],
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
            Context.context_type == ContextType.SHORT_MEMORY,
        )
    )
    memories = result.scalars().all()

    count = 0
    for mem in memories:
        path = f"short_memory/{mem.id}"
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


async def _populate_triggers(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    trigger_ids: list[UUID],
) -> int:
    from sqlalchemy import select

    from aiwen.models.workspaces.workspace_trigger import WorkspaceTrigger

    result = await db.execute(
        select(WorkspaceTrigger).where(
            WorkspaceTrigger.id.in_(trigger_ids),
            WorkspaceTrigger.workspace_id.is_(None),
        )
    )
    templates = result.scalars().all()

    count = 0
    for tmpl in templates:
        copy = WorkspaceTrigger(
            workspace_id=workspace_id,
            user_id=user_id,
            name=tmpl.name,
            description=tmpl.description,
            event_type=tmpl.event_type,
            condition_type=tmpl.condition_type,
            condition_value=tmpl.condition_value,
            condition_field=tmpl.condition_field,
            tool_name=tmpl.tool_name,
            action_params=tmpl.action_params,
            priority=tmpl.priority,
            enabled=tmpl.enabled,
            created_by=user_id,
        )
        db.add(copy)
        count += 1

    if count:
        await db.flush()
    return count


async def _create_root_structure(
    service: WorkspaceContextService,
    user_id: str,
    counts: dict[str, int],
) -> None:
    """Create root and first-level directory nodes in WorkspaceContext.

    The leaf entries (e.g. ``tools/my_tool``) are written by the individual
    ``_populate_*`` helpers.  This function adds the parent directory nodes
    so that ``list_context`` / ``glance_context`` on ``"/"`` returns a proper
    directory tree instead of an empty result.

    Directory layout::

        /                   ← root, always created
        ├── tools/          ← only if count > 0
        ├── knowledge/
        ├── skills/
        ├── short_memory/
        ├── long_memory/
        └── triggers/
    """
    # Ordered list of (path, display_label, count_key, description)
    _DIRS = [
        ("tools",        "Tools",        "tools",     "Callable tools available in this workspace"),
        ("knowledge",    "Knowledge",    "knowledge", "Knowledge bases attached to this workspace"),
        ("skills",       "Skills",       "skills",    "Skill templates and prompt guides"),
        ("short_memory", "Short Memory", "memories",  "Persisted workspace short-term memory fragments"),
        ("long_memory",  "Long Memory",  "history",   "Imported long-term conversation history"),
        ("triggers",     "Triggers",     "triggers",  "Automated event trigger rules"),
    ]

    populated: list[tuple[str, str, int, str]] = []

    for path, label, count_key, description in _DIRS:
        count = counts.get(count_key, 0)
        glance = f"{label} ({count} item{'s' if count != 1 else ''})"
        await service.set(
            path=path,
            glance=glance,
            overview=description,
            detail=None,
            tags=[path, "directory"],
            meta={"item_count": count, "directory": True},
            created_by=user_id,
            content_type="application/json",
        )
        if count > 0:
            populated.append((path, label, count, description))

    # Root node — always written; glance summarises non-empty categories.
    if populated:
        root_glance = "Workspace context root — " + ", ".join(
            f"{label}: {count}" for _, label, count, _ in populated
        )
        root_overview = {
            path: {"label": label, "count": count, "description": desc}
            for path, label, count, desc in populated
        }
    else:
        root_glance = "Workspace context root (no resources yet)"
        root_overview = {}

    await service.set(
        path="",
        glance=root_glance,
        overview=root_overview,
        detail=None,
        tags=["root", "directory"],
        meta={
            "directory": True,
            "directories": [p for p, _, _, _ in _DIRS],
        },
        created_by=user_id,
        content_type="application/json",
    )


async def _populate_history(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    source_workspace_ids: list[UUID],
) -> int:
    """Copy finished runs from source workspaces into long_memory/.

    For each source workspace, loads all completed/finished runs and
    reconstructs a conversation summary from USER_MESSAGE and AGENT_MESSAGE
    events.  Each run becomes one entry at ``long_memory/{run_id}``.
    """
    import json

    from sqlalchemy import select

    from aiwen.core.enums.events import EventType
    from aiwen.models.events.event import Event
    from aiwen.models.runs.run import Run

    count = 0
    for src_id in source_workspace_ids:
        src_id_str = str(src_id)

        # Load finished runs for the source workspace (most recent 50).
        runs_result = await db.execute(
            select(Run)
            .where(
                Run.workspace_id == src_id_str,
                Run.status.in_(["finished", "completed", "failed"]),
            )
            .order_by(Run.created_at.desc())
            .limit(50)
        )
        runs = runs_result.scalars().all()

        for run in runs:
            run_id_str = str(run.id)

            # Fetch user + agent message events for this run, in order.
            events_result = await db.execute(
                select(Event)
                .where(
                    Event.run_id == run_id_str,
                    Event.event_type.in_([
                        EventType.USER_MESSAGE,
                        EventType.AGENT_MESSAGE,
                    ]),
                )
                .order_by(Event.sequence)
            )
            events = events_result.scalars().all()

            if not events:
                continue

            # Build a readable conversation transcript.
            turns = []
            for ev in events:
                payload = ev.payload or {}
                if ev.event_type == EventType.USER_MESSAGE:
                    msg = payload.get("message", "")
                    if isinstance(msg, list):
                        msg = " ".join(
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in msg
                        )
                    turns.append({"role": "user", "content": str(msg)})
                elif ev.event_type == EventType.AGENT_MESSAGE:
                    content = payload.get("content") or payload.get("message", "")
                    turns.append({"role": "assistant", "content": str(content)})

            if not turns:
                continue

            # Glance: first user message, truncated.
            first_user = next(
                (t["content"] for t in turns if t["role"] == "user"), ""
            )
            glance = first_user[:120] if first_user else f"Run {run_id_str[:8]}"

            await service.set(
                path=f"long_memory/{run_id_str}",
                glance=glance,
                overview={
                    "run_id": run_id_str,
                    "status": run.status,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "source_workspace_id": src_id_str,
                    "turn_count": len(turns),
                },
                detail=json.dumps(turns, ensure_ascii=False),
                tags=["long_memory", "imported_history"],
                meta={
                    "run_id": run_id_str,
                    "source_workspace_id": src_id_str,
                },
                created_by=user_id,
                content_type="application/json",
            )
            count += 1

    return count
