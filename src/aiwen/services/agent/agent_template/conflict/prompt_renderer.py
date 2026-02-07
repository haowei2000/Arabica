# aiwen/services/agent/agent_template/agent/prompt_renderer.py
"""System prompt renderer — loads workspace context from the database
and renders template variables into a complete system prompt.

Usage:

    async with get_session("aiwen") as db:
        renderer = PromptRenderer(db)
        system_prompt = await renderer.render(
            workspace_id=workspace_id,
            run_id=run_id,
            user_id=user_id,
            app_id=app_id,
        )
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Data containers ───────────────────────────────────────────


@dataclass
class RunInfo:
    """Lightweight snapshot of a Run for prompt rendering."""

    id: str
    status: str
    trigger_type: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


@dataclass
class KnowledgeInfo:
    """Lightweight snapshot of a Knowledge base."""

    id: str
    name: str
    description: str | None = None
    document_count: int = 0
    status: str = "active"


@dataclass
class ToolInfo:
    """Lightweight snapshot of a Tool."""

    id: str
    name: str
    tool_code: str
    description: str | None = None
    tool_type: str = "server"
    input_schema: dict[str, Any] | None = None


@dataclass
class SkillInfo:
    """Lightweight snapshot of a Skill / Context of type SKILL."""

    id: str
    content: str
    summary: str | None = None
    keywords: list[str] | None = None


@dataclass
class WorkspaceContext:
    """All context data needed to render the system prompt."""

    workspace_id: str = ""
    workspace_name: str = ""
    run_id: str = ""
    run_info: str = ""
    owner_id: str = ""
    member_ids: list[str] = field(default_factory=list)
    runs: list[RunInfo] = field(default_factory=list)
    user_history_events: list[dict[str, Any]] = field(default_factory=list)
    knowledge_list: list[KnowledgeInfo] = field(default_factory=list)
    tools: list[ToolInfo] = field(default_factory=list)
    skills: list[SkillInfo] = field(default_factory=list)


# ── Prompt Renderer ───────────────────────────────────────────


class PromptRenderer:
    """Renders the system prompt templates with actual workspace data.

    Each ``load_*`` method is independent — call only the ones you need,
    then call ``render()`` to assemble the final prompt string.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.ctx = WorkspaceContext()

    # ── Public API ────────────────────────────────────────────

    async def render(
        self,
        workspace_id: str | UUID,
        run_id: str | UUID | None = None,
        user_id: str | UUID | None = None,
        app_id: str | UUID | None = None,
        *,
        locale: str = "zh",
        include_history: bool = True,
        include_knowledge: bool = True,
        include_tools: bool = True,
        include_skills: bool = True,
        include_user_history: bool = False,
        max_runs: int = 10,
        max_user_events: int = 20,
    ) -> str:
        """Load all relevant context and render the final system prompt.

        Args:
            workspace_id: Current workspace UUID
            run_id: Current run UUID (optional)
            user_id: Current user UUID (optional)
            app_id: Current app UUID (optional)
            locale: Prompt language — "zh" (Chinese) or "en" (English)
            include_history: Whether to include workspace run history
            include_knowledge: Whether to include knowledge bases
            include_tools: Whether to include available tools
            include_skills: Whether to include available skills
            include_user_history: Whether to include user's global history
            max_runs: Maximum number of historical runs to load
            max_user_events: Maximum number of user history events to load

        Returns:
            Fully rendered system prompt string
        """
        ws_id = str(workspace_id)
        r_id = str(run_id) if run_id else None
        u_id = str(user_id) if user_id else None
        _ = app_id  # Reserved for future use

        # Load workspace + run basics (always needed)
        await self._load_workspace(ws_id)
        if r_id:
            await self._load_current_run(r_id)

        # Load optional sections in parallel-safe order
        if include_history:
            await self._load_workspace_runs(ws_id, limit=max_runs)

        if include_knowledge and u_id:
            await self._load_knowledge(u_id)

        if include_tools and u_id:
            await self._load_tools(u_id)

        if include_skills and u_id:
            await self._load_skills(u_id)

        if include_user_history and u_id:
            await self._load_user_history(u_id, ws_id, limit=max_user_events)

        return self._assemble(
            locale=locale,
            include_history=include_history,
            include_knowledge=include_knowledge,
            include_tools=include_tools,
            include_skills=include_skills,
            include_user_history=include_user_history,
        )

    # ── Data loaders ──────────────────────────────────────────

    async def _load_workspace(self, workspace_id: str) -> None:
        """Load workspace metadata and members."""
        from aiwen.models.workspaces.workspace import Workspace
        from aiwen.models.workspaces.workspace_member import WorkspaceMember

        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.is_deleted == False,  # noqa: E712
        )
        result = await self.db.execute(stmt)
        ws = result.scalar_one_or_none()

        if not ws:
            logger.warning(f"Workspace {workspace_id} not found")
            self.ctx.workspace_id = workspace_id
            return

        self.ctx.workspace_id = str(ws.id)
        self.ctx.workspace_name = ws.name or ""
        self.ctx.owner_id = str(ws.owner_id)

        # Load accepted members
        member_stmt = select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.invitation_status == "accepted",
        )
        member_result = await self.db.execute(member_stmt)
        self.ctx.member_ids = [
            str(uid)
            for uid in member_result.scalars().all()
            if str(uid) != self.ctx.owner_id
        ]

    async def _load_current_run(self, run_id: str) -> None:
        """Load the current run's basic info."""
        from aiwen.models.runs.run import Run

        stmt = select(Run).where(Run.id == run_id)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            self.ctx.run_id = run_id
            return

        self.ctx.run_id = str(run.id)
        parts = [f"status: {run.status}"]
        if run.started_at:
            parts.append(f"started: {run.started_at.isoformat()}")
        if run.trigger_type:
            parts.append(f"trigger: {run.trigger_type}")
        self.ctx.run_info = ", ".join(parts)

    async def _load_workspace_runs(
        self, workspace_id: str, limit: int = 10
    ) -> None:
        """Load recent runs for the workspace."""
        from aiwen.models.runs.run import Run

        stmt = (
            select(Run)
            .where(Run.workspace_id == workspace_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        self.ctx.runs = [
            RunInfo(
                id=str(r.id),
                status=r.status,
                trigger_type=r.trigger_type or "unknown",
                created_at=r.created_at.isoformat() if r.created_at else "",
                started_at=r.started_at.isoformat() if r.started_at else None,
                completed_at=r.completed_at.isoformat()
                if r.completed_at
                else None,
                error=r.error,
            )
            for r in runs
        ]

    async def _load_knowledge(self, user_id: str) -> None:
        """Load knowledge bases owned by the user."""
        from aiwen.models.knowledge.knowledge import Knowledge

        stmt = (
            select(Knowledge)
            .where(
                Knowledge.user_id == user_id,
                Knowledge.status == "active",
            )
            .order_by(Knowledge.created_at.desc())
        )
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        self.ctx.knowledge_list = [
            KnowledgeInfo(
                id=str(k.id),
                name=k.name,
                description=k.description,
                document_count=k.document_count or 0,
                status=k.status or "active",
            )
            for k in items
        ]

    async def _load_tools(self, user_id: str) -> None:
        """Load tools accessible to the user (own + public)."""
        from aiwen.models.agents.tool import Tool

        stmt = (
            select(Tool)
            .where(
                Tool.enabled == True,  # noqa: E712
                (Tool.user_id == user_id) | (Tool.is_public == True),  # noqa: E712
            )
            .order_by(Tool.name)
        )
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        self.ctx.tools = [
            ToolInfo(
                id=str(t.id),
                name=t.name,
                tool_code=t.tool_code,
                description=t.description,
                tool_type=t.tool_type,
                input_schema=t.input_schema,
            )
            for t in items
        ]

    async def _load_skills(self, user_id: str) -> None:
        """Load skill-type context entries for the user."""
        from aiwen.models.context.context import Context

        stmt = (
            select(Context)
            .where(
                Context.user_id == user_id,
                Context.context_type == "SKILL",
            )
            .order_by(Context.created_at.desc())
        )
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        self.ctx.skills = [
            SkillInfo(
                id=str(s.id),
                content=s.content or "",
                summary=s.summary,
                keywords=s.keywords if isinstance(s.keywords, list) else None,
            )
            for s in items
        ]

    async def _load_user_history(
        self, user_id: str, workspace_id: str, limit: int = 20
    ) -> None:
        """Load recent user-triggered events across the system.

        Args:
            user_id: The user ID to filter events
            workspace_id: Reserved for future workspace-scoped filtering
            limit: Maximum number of events to load
        """
        from aiwen.models.events.event import Event

        _ = workspace_id  # Reserved for future workspace-scoped filtering

        stmt = (
            select(Event)
            .where(
                and_(
                    Event.user_id == user_id,
                    Event.event_type.in_(
                        ["user.message", "user.feedback"]
                    ),
                )
            )
            .order_by(Event.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        events = result.scalars().all()

        self.ctx.user_history_events = [
            {
                "event_type": e.event_type,
                "workspace_id": str(e.workspace_id),
                "content": (e.payload or {}).get("content", ""),
                "created_at": e.created_at.isoformat() if e.created_at else "",
            }
            for e in reversed(events)  # chronological order
        ]

    # ── Formatting helpers ────────────────────────────────────

    def _format_runs(self) -> str:
        """Format run history as a readable list."""
        if not self.ctx.runs:
            return "(no run history)"

        lines = []
        for r in self.ctx.runs:
            parts = [f"Run {r.id} | status: {r.status}"]
            if r.created_at:
                parts.append(f"created: {r.created_at}")
            if r.started_at:
                parts.append(f"started: {r.started_at}")
            if r.completed_at:
                parts.append(f"completed: {r.completed_at}")
            if r.error:
                parts.append(f"error: {r.error[:100]}")
            lines.append("- " + " | ".join(parts))

        return "\n".join(lines)

    def _format_user_history(self) -> str:
        """Format user history events as a readable list."""
        if not self.ctx.user_history_events:
            return "(no user history)"

        lines = []
        for e in self.ctx.user_history_events:
            content = e.get("content", "")
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(
                f"- [{e.get('created_at', '')}] "
                f"{e.get('event_type', '')}: {content}"
            )

        return "\n".join(lines)

    def _format_knowledge(self) -> str:
        """Format knowledge bases as a readable list."""
        if not self.ctx.knowledge_list:
            return "(no knowledge bases)"

        lines = []
        for k in self.ctx.knowledge_list:
            desc = f" — {k.description}" if k.description else ""
            lines.append(
                f"- [{k.id}] {k.name}{desc} "
                f"({k.document_count} documents, {k.status})"
            )

        return "\n".join(lines)

    def _format_tools(self) -> str:
        """Format tools as a readable list."""
        if not self.ctx.tools:
            return "(no tools available)"

        lines = []
        for t in self.ctx.tools:
            desc = f" — {t.description}" if t.description else ""
            schema_hint = ""
            if t.input_schema and isinstance(t.input_schema, dict):
                props = t.input_schema.get("properties", {})
                if props:
                    param_names = ", ".join(list(props.keys())[:5])
                    schema_hint = f" | params: ({param_names})"
            lines.append(
                f"- [{t.id}] {t.name} (code: {t.tool_code}, "
                f"type: {t.tool_type}){desc}{schema_hint}"
            )

        return "\n".join(lines)

    def _format_skills(self) -> str:
        """Format skills as a readable list."""
        if not self.ctx.skills:
            return "(no skills available)"

        lines = []
        for s in self.ctx.skills:
            summary = s.summary or s.content[:100]
            if len(summary) > 100:
                summary = summary[:100] + "..."
            kw = f" | keywords: {', '.join(s.keywords)}" if s.keywords else ""
            lines.append(f"- [{s.id}] {summary}{kw}")

        return "\n".join(lines)

    # ── Assembly ──────────────────────────────────────────────

    def _assemble(
        self,
        *,
        locale: str = "zh",
        include_history: bool = True,
        include_knowledge: bool = True,
        include_tools: bool = True,
        include_skills: bool = True,
        include_user_history: bool = False,
    ) -> str:
        """Assemble all sections into the final system prompt."""
        # Select prompt templates based on locale
        if locale == "en":
            from aiwen.services.agent.agent_template.conflict.system_prompt_en import (
                available_context_prompt_en,
                available_knowledge_prompt_en,
                available_tools_prompt_en,
                skill_prompt_en,
                system_prompt_en,
                user_history_prompt_en,
                workspace_history_prompt_en,
            )

            tpl_system = system_prompt_en
            tpl_context = available_context_prompt_en
            tpl_history = workspace_history_prompt_en
            tpl_user = user_history_prompt_en
            tpl_knowledge = available_knowledge_prompt_en
            tpl_tools = available_tools_prompt_en
            tpl_skills = skill_prompt_en
        else:
            from aiwen.services.agent.agent_template.conflict.system_prompt_zh import (
                available_context_prompt,
                available_knowledge_prompt,
                available_tools_prompt,
                skill_prompt,
                system_prompt,
                user_history_prompt,
                workspace_history_prompt,
            )

            tpl_system = system_prompt
            tpl_context = available_context_prompt
            tpl_history = workspace_history_prompt
            tpl_user = user_history_prompt
            tpl_knowledge = available_knowledge_prompt
            tpl_tools = available_tools_prompt
            tpl_skills = skill_prompt

        # Render the main system prompt
        other_users = ", ".join(self.ctx.member_ids) if self.ctx.member_ids else "none"
        run_info_str = f"\n│   {self.ctx.run_info}" if self.ctx.run_info else ""

        rendered = tpl_system.replace(
            "{{CURRENT_WORKSPACE_ID}}", self.ctx.workspace_id
        ).replace(
            "{{CURRENT_RUN_ID}}", self.ctx.run_id
        ).replace(
            "{{CURRENT_RUN_INFO}}", run_info_str
        ).replace(
            "{{OWNER}}", self.ctx.owner_id
        ).replace(
            "{{OTHER_USER}}", other_users
        )

        # Append available context overview
        rendered += "\n" + tpl_context.strip()

        # Append optional sections
        sections: list[str] = []

        if include_history and self.ctx.runs:
            sections.append(
                tpl_history.replace("{{runs}}", self._format_runs()).strip()
            )

        if include_knowledge and self.ctx.knowledge_list:
            sections.append(
                tpl_knowledge.replace(
                    "{{knowledge_list}}", self._format_knowledge()
                ).strip()
            )

        if include_tools and self.ctx.tools:
            sections.append(
                tpl_tools.replace("{{tools}}", self._format_tools()).strip()
            )

        if include_skills and self.ctx.skills:
            sections.append(
                tpl_skills.replace(
                    "{{skills}}", self._format_skills()
                ).strip()
            )

        if include_user_history and self.ctx.user_history_events:
            sections.append(
                tpl_user.replace(
                    "{{user_history}}", self._format_user_history()
                ).strip()
            )

        if sections:
            rendered += "\n\n" + "\n\n".join(sections)

        return rendered.strip()
