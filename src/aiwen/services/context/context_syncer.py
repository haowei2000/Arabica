"""ContextSyncer — auto-syncs entity changes to the global Context table.

When a Tool, Skill, Knowledge, Trigger, Workspace, or Run is created /
updated / deleted, call the corresponding method on ContextSyncer so that
the global ``context`` table always reflects the latest state.

Path conventions
----------------
/tools/{tool_name}                         — one entry per external tool
/skills/{skill_name}                       — one entry per skill
/knowledge/{knowledge_name}                — one entry per knowledge base
/triggers/{trigger_name}                   — one entry per workspace trigger
/workspaces/{workspace_name}               — one entry per workspace
/workspaces/{workspace_name}/runs/{run_id[:8]}  — one entry per run (optional)

Key root paths (schema / directory nodes)
-----------------------------------------
/           root
/tools      tools directory
/skills     skills directory
/knowledge  knowledge directory
/triggers   triggers directory
/workspaces workspaces directory
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.context import ContextType
from aiwen.models.context.context import Context
from aiwen.services.context.context_crud import ContextCRUD

logger = logging.getLogger(__name__)


def _uuid(val: str | UUID | None) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


class ContextSyncer:
    """Syncs entity changes to the global Context table with structured paths.

    Usage in CRUD methods::

        syncer = ContextSyncer(self.db)
        await syncer.sync_tool(tool)      # after create / update
        await syncer.remove_tool(tool)    # before / after delete
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ──────────────────────────────────────────────────────────────
    # Tool
    # ──────────────────────────────────────────────────────────────

    async def sync_tool(self, tool: Any) -> None:
        """Upsert a context entry for an external Tool at /tools/{name}."""
        if not tool.user_id:
            return  # inner tools have no owner — skip

        path = f"/tools/{tool.name}"
        glance = tool.display_name or tool.name
        if tool.description:
            glance += f" — {tool.description[:60]}"

        parts: list[str] = [f"Tool: {tool.name}"]
        if tool.description:
            parts.append(f"Description: {tool.description}")
        if tool.input_schema:
            parts.append(f"Input Schema:\n{json.dumps(tool.input_schema, ensure_ascii=False, indent=2)}")

        await self._upsert(
            user_id=str(tool.user_id),
            path=path,
            source_id=str(tool.id),
            context_type=ContextType.TOOL,
            glance=glance,
            content="\n".join(parts),
            tags=(tool.tags or []) + ["tool"],
            meta={
                "tool_code": tool.tool_code,
                "tool_type": tool.tool_type,
                "enabled": tool.enabled,
            },
        )

    async def remove_tool(self, tool: Any) -> None:
        """Delete the context entry for a Tool."""
        if not tool.user_id:
            return
        await self._delete(str(tool.user_id), f"/tools/{tool.name}")

    # ──────────────────────────────────────────────────────────────
    # Skill
    # ──────────────────────────────────────────────────────────────

    async def sync_skill(self, skill: Any, content: str | None = None) -> None:
        """Upsert a context entry for a Skill at /skills/{name}.

        ``content`` is the Markdown body (no longer stored on the Skill row).
        If omitted, any existing content in the Context row is preserved.
        """
        path = f"/skills/{skill.name}"
        glance = (skill.description[:80] if skill.description else None) or f"Skill: {skill.name}"

        parts: list[str] = [f"Skill: {skill.name}"]
        if skill.description:
            parts.append(f"Description: {skill.description}")
        if content:
            parts.append(f"Content:\n{content}")

        await self._upsert(
            user_id=str(skill.user_id),
            path=path,
            source_id=str(skill.id),
            context_type=ContextType.SKILL,
            glance=glance,
            content="\n".join(parts),
            tags=(skill.tags or []) + ["skill"],
            meta={"skill_id": str(skill.id)},
        )

    async def remove_skill(self, skill: Any) -> None:
        """Delete the context entry for a Skill."""
        await self._delete(str(skill.user_id), f"/skills/{skill.name}")

    # ──────────────────────────────────────────────────────────────
    # Knowledge
    # ──────────────────────────────────────────────────────────────

    async def sync_knowledge(self, knowledge: Any) -> None:
        """Upsert a context entry for a Knowledge base at /knowledge/{name}."""
        path = f"/knowledge/{knowledge.name}"
        glance = f"Knowledge: {knowledge.name}"
        if knowledge.description:
            glance += f" — {knowledge.description[:60]}"

        parts: list[str] = [f"Knowledge Base: {knowledge.name}"]
        if knowledge.description:
            parts.append(f"Description: {knowledge.description}")
        await self._upsert(
            user_id=str(knowledge.user_id),
            path=path,
            source_id=str(knowledge.id),
            context_type=ContextType.KNOWLEDGE,
            glance=glance,
            content="\n".join(parts),
            tags=["knowledge"],
            meta={
                "knowledge_id": str(knowledge.id),
                "status": getattr(knowledge, "status", None),
            },
        )

    async def remove_knowledge(self, knowledge: Any) -> None:
        """Delete the context entry for a Knowledge base."""
        await self._delete(str(knowledge.user_id), f"/knowledge/{knowledge.name}")

    # ──────────────────────────────────────────────────────────────
    # Trigger
    # ──────────────────────────────────────────────────────────────

    async def sync_trigger(self, trigger: Any, user_id: str | UUID) -> None:
        """Upsert a context entry for a WorkspaceTrigger at /triggers/{name}."""
        path = f"/triggers/{trigger.name}"
        glance = f"Trigger: {trigger.name}"
        if trigger.description:
            glance += f" — {trigger.description[:50]}"

        parts: list[str] = [f"Trigger: {trigger.name}"]
        if trigger.description:
            parts.append(f"Description: {trigger.description}")
        parts.append(f"Event: {trigger.event_type}")
        parts.append(f"Condition: {trigger.condition_type}")
        parts.append(f"Action (tool): {trigger.tool_name}")

        await self._upsert(
            user_id=str(user_id),
            path=path,
            source_id=str(trigger.id),
            context_type=ContextType.TRIGGER,
            glance=glance,
            content="\n".join(parts),
            tags=["trigger", trigger.event_type],
            meta={
                "workspace_id": str(trigger.workspace_id) if trigger.workspace_id else None,
                "event_type": trigger.event_type,
                "tool_name": trigger.tool_name,
                "enabled": trigger.enabled,
            },
        )

    async def remove_trigger(self, trigger: Any, user_id: str | UUID) -> None:
        """Delete the context entry for a WorkspaceTrigger."""
        await self._delete(str(user_id), f"/triggers/{trigger.name}")

    # ──────────────────────────────────────────────────────────────
    # Workspace
    # ──────────────────────────────────────────────────────────────

    async def sync_workspace(self, workspace: Any) -> None:
        """Upsert a context entry for a Workspace at /workspaces/{name}."""
        path = f"/workspaces/{workspace.name}"
        glance = f"Workspace: {workspace.name}"
        if workspace.description:
            glance += f" — {workspace.description[:60]}"

        ws_summary = getattr(workspace, "summary", None)

        parts: list[str] = [f"Workspace: {workspace.name}"]
        if workspace.description:
            parts.append(f"Description: {workspace.description}")
        if ws_summary:
            parts.append(f"Summary: {ws_summary}")

        await self._upsert(
            user_id=str(workspace.owner_id),
            path=path,
            source_id=str(workspace.id),
            context_type=ContextType.WORKSPACE,
            glance=glance,
            content="\n".join(parts),
            tags=["workspace"],
            meta={
                "workspace_id": str(workspace.id),
                "status": getattr(workspace, "status", None),
                "visibility": getattr(workspace, "visibility", None),
            },
        )

    async def remove_workspace(self, workspace: Any) -> None:
        """Delete the context entry for a Workspace."""
        await self._delete(str(workspace.owner_id), f"/workspaces/{workspace.name}")

    # ──────────────────────────────────────────────────────────────
    # Run (lightweight — only glance / status, no heavy content)
    # ──────────────────────────────────────────────────────────────

    async def sync_run(self, run: Any) -> None:
        """Upsert a context entry for a Run at /workspaces/{workspace_name}/runs/{run_id[:8]}."""
        workspace_name = await self._workspace_name(run.workspace_id)
        short_id = str(run.id)[:8]
        path = f"/workspaces/{workspace_name}/runs/{short_id}"

        title = getattr(run, "title", None)
        run_summary = getattr(run, "summary", None)
        glance = title or f"Run {short_id} [{run.status}]"

        parts: list[str] = [
            f"Run ID: {run.id}",
            f"Status: {run.status}",
            f"Workspace: {workspace_name}",
        ]
        if title:
            parts.insert(0, f"Title: {title}")
        if run_summary:
            parts.append(f"Summary: {run_summary}")

        await self._upsert(
            user_id=str(run.user_id),
            path=path,
            source_id=str(run.id),
            context_type=ContextType.RUN,
            glance=glance,
            content="\n".join(parts),
            tags=["run", run.status],
            meta={
                "run_id": str(run.id),
                "workspace_id": str(run.workspace_id),
                "status": run.status,
            },
        )

    async def remove_run(self, run: Any) -> None:
        """Delete the context entry for a Run."""
        workspace_name = await self._workspace_name(run.workspace_id)
        short_id = str(run.id)[:8]
        await self._delete(
            str(run.user_id),
            f"/workspaces/{workspace_name}/runs/{short_id}",
        )

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    async def _workspace_name(self, workspace_id: Any) -> str:
        """Return the workspace name for use in paths, falling back to the raw ID."""
        from aiwen.models.workspaces.workspace import Workspace

        ws = await self.db.get(Workspace, _uuid(str(workspace_id)))
        return ws.name if ws else str(workspace_id)

    async def _upsert(
        self,
        user_id: str,
        path: str,
        source_id: str | None,
        context_type: str,
        glance: str,
        content: str,
        tags: list[str],
        meta: dict[str, Any],
    ) -> None:
        crud = ContextCRUD(self.db)
        await crud.upsert_by_path(
            user_id=user_id,
            path=path,
            data={
                "context_type": context_type,
                "source_id": source_id,
                "glance": glance,
                "content": content,
                "tags": tags,
                "meta": meta,
            },
        )
        logger.debug("context_syncer: upserted path=%s user=%s", path, user_id)

    async def _delete(self, user_id: str, path: str) -> None:
        user_uuid = _uuid(user_id)
        await self.db.execute(
            delete(Context).where(
                Context.user_id == user_uuid,
                Context.path == path,
            )
        )
        await self.db.flush()
        logger.debug("context_syncer: deleted path=%s user=%s", path, user_id)
