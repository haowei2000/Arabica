"""Initialize the workspace context with user-selected resources.

Called after workspace creation to pre-populate WorkspaceContext by copying
entries from the global Context table, which is the single source of truth for
tool/knowledge/skill/memory metadata.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.context import ContextType
from aiwen.models.context.context import Context
from aiwen.schemas.workspaces.workspace import WorkspaceContextConfig
from aiwen.services.workspace_context.workspace_context_service import (
    WorkspaceContextService,
)

logger = logging.getLogger(__name__)

# Maps context_type → workspace_context path prefix
_CONTEXT_TYPE_PATH_PREFIX: dict[str, str] = {
    ContextType.TOOL: "tools",
    ContextType.SKILL: "skills",
    ContextType.KNOWLEDGE: "knowledge",
    ContextType.SHORT_MEMORY: "short_memory",
}


def _derive_path(ctx: Context) -> str:
    """Derive workspace_context path from a Context row."""
    prefix = _CONTEXT_TYPE_PATH_PREFIX.get(ctx.context_type, ctx.context_type)
    if ctx.context_type == ContextType.TOOL:
        # Prefer tool_code stored in meta for a human-readable path segment
        tool_code = (ctx.meta or {}).get("tool_code") or str(ctx.source_id or ctx.id)
        return f"{prefix}/{tool_code}"
    return f"{prefix}/{ctx.source_id or ctx.id}"


async def init_workspace_context(
    db: AsyncSession,
    workspace_id: str | UUID,
    user_id: str | UUID,
    config: WorkspaceContextConfig,
) -> dict[str, int]:
    """Populate WorkspaceContext by copying entries from the Context table.

    For each resource type (tools, knowledge, skills, memories) the matching
    Context rows for *user_id* are located and their content is written into
    WorkspaceContext at the canonical path for that type.

    Triggers and history are not stored in the Context table and are handled
    separately with their original logic.
    """
    workspace_id = str(workspace_id)
    user_id_str = str(user_id)
    service = WorkspaceContextService(db, workspace_id)
    counts: dict[str, int] = {
        "tools": 0, "knowledge": 0, "skills": 0,
        "history": 0, "memories": 0, "triggers": 0,
    }

    # ── tools ──────────────────────────────────────────────────────────────
    if config.tool_ids:
        counts["tools"] = await _copy_context_entries(
            db, service, user_id_str,
            context_type=ContextType.TOOL,
            source_ids=[str(tid) for tid in config.tool_ids],
        )

    # ── knowledge ──────────────────────────────────────────────────────────
    if config.knowledge_ids:
        counts["knowledge"] = await _copy_context_entries(
            db, service, user_id_str,
            context_type=ContextType.KNOWLEDGE,
            source_ids=[str(kid) for kid in config.knowledge_ids],
        )

    # ── skills ─────────────────────────────────────────────────────────────
    if config.skill_ids:
        counts["skills"] = await _copy_context_entries(
            db, service, user_id_str,
            context_type=ContextType.SKILL,
            source_ids=[str(sid) for sid in config.skill_ids],
        )

    # ── memories (matched by context.id, not source_id) ───────────────────
    if config.memory_ids:
        counts["memories"] = await _copy_context_entries(
            db, service, user_id_str,
            context_type=ContextType.SHORT_MEMORY,
            source_ids=[str(mid) for mid in config.memory_ids],
            match_by_id=True,
        )

    # ── history (not in context table — keep original logic) ───────────────
    if config.source_workspace_ids:
        counts["history"] = await _populate_history(
            db, service, user_id_str, config.source_workspace_ids
        )

    # ── triggers (not in context table — keep original logic) ──────────────
    if config.trigger_ids:
        counts["triggers"] = await _populate_triggers(
            db, UUID(workspace_id), UUID(user_id_str), config.trigger_ids
        )

    await _create_root_structure(service, user_id_str, counts)

    logger.info(
        "init_workspace_context: workspace=%s tools=%d knowledge=%d skills=%d "
        "history=%d memories=%d triggers=%d",
        workspace_id, counts["tools"], counts["knowledge"], counts["skills"],
        counts["history"], counts["memories"], counts["triggers"],
    )
    return counts


async def _copy_context_entries(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    *,
    context_type: str,
    source_ids: list[str],
    match_by_id: bool = False,
) -> int:
    """Copy Context rows into WorkspaceContext.

    Args:
        match_by_id: When True match on ``Context.id`` instead of
                     ``Context.source_id`` (used for memories).
    """
    if not source_ids:
        return 0

    id_field = Context.id if match_by_id else Context.source_id
    result = await db.execute(
        select(Context).where(
            Context.context_type == context_type,
            Context.user_id == UUID(user_id),
            id_field.in_(source_ids),
        )
    )
    rows = result.scalars().all()

    count = 0
    for ctx in rows:
        path = _derive_path(ctx)
        await service.set(
            path=path,
            glance=ctx.glance or "",
            overview=ctx.summary,
            detail=ctx.content,
            tags=ctx.tags or [],
            meta=ctx.meta or {},
            created_by=user_id,
            content_type="application/json",
        )
        count += 1

    return count


async def _create_root_structure(
    service: WorkspaceContextService,
    user_id: str,
    counts: dict[str, int],
) -> None:
    """Create root and first-level directory nodes in WorkspaceContext."""
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
        meta={"directory": True, "directories": [p for p, _, _, _ in _DIRS]},
        created_by=user_id,
        content_type="application/json",
    )


async def _populate_triggers(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    trigger_ids: list[UUID],
) -> int:
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
        db.add(WorkspaceTrigger(
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
        ))
        count += 1

    if count:
        await db.flush()
    return count


async def _populate_history(
    db: AsyncSession,
    service: WorkspaceContextService,
    user_id: str,
    source_workspace_ids: list[UUID],
) -> int:
    """Copy finished runs from source workspaces into long_memory/."""
    import json

    from aiwen.core.enums.events import EventType
    from aiwen.models.events.event import Event
    from aiwen.models.runs.run import Run

    count = 0
    for src_id in source_workspace_ids:
        src_id_str = str(src_id)

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

            events_result = await db.execute(
                select(Event)
                .where(
                    Event.run_id == run_id_str,
                    Event.event_type.in_([EventType.USER_MESSAGE, EventType.AGENT_MESSAGE]),
                )
                .order_by(Event.sequence)
            )
            events = events_result.scalars().all()

            if not events:
                continue

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

            first_user = next((t["content"] for t in turns if t["role"] == "user"), "")
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
                meta={"run_id": run_id_str, "source_workspace_id": src_id_str},
                created_by=user_id,
                content_type="application/json",
            )
            count += 1

    return count
