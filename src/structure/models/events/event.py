# structure/models/agent/event.py
"""Event model for event-sourced architecture."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.core.enums import EventType
from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.runs.run import Run
    from structure.models.workspaces.workspace import Workspace

Base = get_base("structure")


class Event(Base):
    """Event model for storing all events in the system.

    Events are the source of truth in the event-sourced architecture.
    They capture user messages, agent responses, tool calls, state changes, etc.
    """

    __tablename__ = "event"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, comment="事件唯一标识"
    )

    # Event type (e.g., user.message, agent.token, tool.call)
    event_type: Mapped[EventType] = mapped_column(
        String(100), nullable=False, comment="事件类型"
    )

    # Associated workspace
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的工作空间ID",
    )

    # Associated run (nullable for workspace-level events)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联的运行ID",
    )

    # Associated app
    app_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的应用ID",
    )

    # User who triggered the event
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="触发事件的用户ID",
    )

    # Executor template code (agent template that processed this event)
    executor_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="执行器模板代码（处理此事件的 Agent 模板）",
    )

    # Event payload (flexible JSON structure)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="事件数据")

    # Token usage for LLM-generated events (e.g. agent.message)
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="本次 LLM 调用的输入 token 数",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="本次 LLM 调用的输出 token 数",
    )

    # Sequence number for ordering within run/workspace
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="事件序列号"
    )

    # Parent event for hierarchical events (e.g., tool result belongs to tool call)
    parent_event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("event.id", ondelete="SET NULL"),
        nullable=True,
        comment="父事件ID",
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )

    # Relationships
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="events")
    run: Mapped[Run | None] = relationship("Run", back_populates="events")
    parent_event: Mapped[Event | None] = relationship(
        "Event",
        remote_side=[id],
        backref="child_events",
    )

    __table_args__ = (
        # Index for workspace event sequence
        Index("ix_event_workspace_sequence", "workspace_id", "sequence"),
        # Index for run event sequence
        Index("ix_event_run_sequence", "run_id", "sequence"),
        # Index for event type and time queries
        Index("ix_event_type_created", "event_type", "created_at"),
        # Index for parent event lookups
        Index("ix_event_parent", "parent_event_id"),
        # Index for executor code queries
        Index("ix_event_executor_code", "executor_code"),
    )

    @classmethod
    def from_redis_fields(cls, data: dict) -> Event:
        """Construct a transient Event instance from raw Redis stream data.

        Reverses the encoding done by ``to_redis_fields``: bytes are decoded,
        JSON strings are parsed back to dicts, and UUID/datetime strings are
        converted to their native types.
        """
        decoded: dict[str, Any] = {
            k.decode() if isinstance(k, bytes) else k: v.decode()
            if isinstance(v, bytes)
            else v
            for k, v in data.items()
        }

        # Parse JSON fields
        for key in ("payload",):
            if decoded.get(key):
                try:
                    decoded[key] = json.loads(decoded[key])
                except (json.JSONDecodeError, TypeError):
                    decoded[key] = {}

        # Convert UUID string fields
        for key in (
            "id",
            "workspace_id",
            "run_id",
            "app_id",
            "user_id",
            "parent_event_id",
        ):
            if decoded.get(key):
                decoded[key] = UUID(decoded[key])

        # Convert sequence to int
        if "sequence" in decoded:
            decoded["sequence"] = int(decoded["sequence"])

        # Convert token fields to int
        for key in ("input_tokens", "output_tokens"):
            if key in decoded:
                decoded[key] = int(decoded[key])

        # Convert created_at to datetime
        if decoded.get("created_at"):
            decoded["created_at"] = datetime.fromisoformat(decoded["created_at"])

        return cls(**decoded)

    def to_redis_fields(self) -> dict[str, str | int | float]:
        """Convert this event to a flat dict suitable for Redis XADD.

        Redis stream fields only accept str, bytes, int, or float.
        UUIDs and datetimes are converted to strings; dicts/lists are
        JSON-serialized.
        """
        fields: dict[str, str | int | float] = {}
        for key in (
            "id",
            "event_type",
            "workspace_id",
            "run_id",
            "app_id",
            "user_id",
            "executor_code",
            "payload",
            "sequence",
            "parent_event_id",
            "created_at",
            "input_tokens",
            "output_tokens",
        ):
            value = getattr(self, key, None)
            if value is None:
                continue
            if isinstance(value, UUID):
                fields[key] = str(value)
            elif isinstance(value, datetime):
                fields[key] = value.isoformat()
            elif isinstance(value, (dict, list)):
                fields[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, (int, float)):
                fields[key] = value
            else:
                fields[key] = str(value)
        return fields

    def to_context(self) -> str | None:
        """Render this event as a human-readable text context string.

        Returns a concise, role-prefixed line suitable for inclusion in a
        conversation history or context window.  Returns ``None`` for
        high-frequency noise events (tokens, heartbeats) that carry no
        durable information.
        """
        p: dict[str, Any] = self.payload or {}

        def _trim(text: Any, limit: int = 200) -> str:
            s = str(text) if not isinstance(text, str) else text
            return s[:limit] + "…" if len(s) > limit else s

        match str(self.event_type):
            # ── User ──────────────────────────────────────────────────────
            case EventType.USER_MESSAGE:
                msg = p.get("message") or p.get("content") or ""
                return f"User: {_trim(msg)}" if msg else "User: (empty message)"

            case EventType.USER_FEEDBACK:
                feedback = (
                    p.get("feedback") or p.get("content") or p.get("rating") or ""
                )
                return f"User feedback: {_trim(feedback)}"

            # ── Agent ─────────────────────────────────────────────────────
            case EventType.AGENT_TOKEN | EventType.AGENT_HEARTBEAT:
                # Too noisy — skip
                return None

            case EventType.AGENT_MESSAGE:
                msg = p.get("message") or p.get("content") or ""
                return (
                    f"Assistant: {_trim(msg)}" if msg else "Assistant: (empty message)"
                )

            case EventType.AGENT_THINKING:
                content = p.get("content") or p.get("thinking") or ""
                return f"Thinking: {_trim(content)}" if content else None

            case EventType.AGENT_PLAN_STEP:
                step = p.get("step") or p.get("index", "")
                title = p.get("title") or p.get("name") or ""
                content = p.get("content") or p.get("description") or ""
                parts = [f"Plan step {step}" if step else "Plan step", title, content]
                return " — ".join(filter(None, parts))[:300]

            case EventType.AGENT_QUERY:
                query = p.get("query") or p.get("content") or ""
                return f"Agent query: {_trim(query)}" if query else None

            # ── Tool ──────────────────────────────────────────────────────
            case EventType.TOOL_CALL:
                name = p.get("tool_name") or p.get("name") or "unknown"
                args = p.get("arguments") or p.get("args") or {}
                args_str = (
                    json.dumps(args, ensure_ascii=False)
                    if isinstance(args, dict)
                    else str(args)
                )
                return f"Tool call: {name}({_trim(args_str, 120)})"

            case EventType.TOOL_RESULT:
                name = p.get("tool_name") or p.get("name") or "unknown"
                result = p.get("result") or p.get("output") or p.get("content") or ""
                result_str = (
                    json.dumps(result, ensure_ascii=False)
                    if isinstance(result, (dict, list))
                    else str(result)
                )
                return f"Tool result [{name}]: {_trim(result_str, 160)}"

            case EventType.TOOL_ERROR:
                name = p.get("tool_name") or p.get("name") or "unknown"
                error = p.get("error") or p.get("message") or "unknown error"
                return f"Tool error [{name}]: {_trim(error)}"

            case EventType.TOOL_PENDING:
                name = p.get("tool_name") or p.get("name") or "unknown"
                return f"Tool pending: {name}"

            case EventType.TOOL_CLIENT_REQUEST:
                name = p.get("tool_name") or p.get("name") or "unknown"
                return f"Tool client request: {name}"

            # ── Context ───────────────────────────────────────────────────
            case EventType.USING_CONTEXT:
                path = p.get("path") or p.get("context_path") or ""
                return f"Using context: {path}" if path else "Using context"

            # ── Run lifecycle ─────────────────────────────────────────────
            case EventType.RUN_CREATED:
                return "Run created"

            case EventType.RUN_STATE_CHANGE:
                prev = p.get("previous_state") or "?"
                new = p.get("new_state") or "?"
                reason = p.get("reason")
                line = f"Run state: {prev} → {new}"
                return f"{line} ({reason})" if reason else line

            case EventType.RUN_COMPLETED:
                return "Run completed"

            case EventType.RUN_FAILED:
                error = p.get("error") or p.get("message") or ""
                return f"Run failed: {_trim(error)}" if error else "Run failed"

            case EventType.RUN_CANCELLED:
                reason = p.get("reason") or ""
                return f"Run cancelled: {reason}" if reason else "Run cancelled"

            # ── Workspace ─────────────────────────────────────────────────
            case EventType.WORKSPACE_CREATED:
                name = p.get("name") or p.get("workspace_name") or ""
                return f"Workspace created: {name}" if name else "Workspace created"

            case EventType.WORKSPACE_UPDATED:
                return "Workspace updated"

            case EventType.WORKSPACE_MEMBER_JOIN:
                user = p.get("user_id") or p.get("username") or "unknown"
                return f"Member joined: {user}"

            case EventType.WORKSPACE_MEMBER_LEAVE:
                user = p.get("user_id") or p.get("username") or "unknown"
                return f"Member left: {user}"

            case EventType.WORKSPACE_MEMBER_ROLE_CHANGE:
                user = p.get("user_id") or p.get("username") or "unknown"
                role = p.get("role") or p.get("new_role") or "unknown"
                return f"Role changed: {user} → {role}"

            # ── Task ──────────────────────────────────────────────────────
            case EventType.TASK_CREATE:
                title = p.get("title") or p.get("name") or ""
                return f"Task created: {_trim(title)}" if title else "Task created"

            case EventType.TASK_UPDATE:
                title = p.get("title") or p.get("name") or ""
                return f"Task updated: {_trim(title)}" if title else "Task updated"

            case EventType.TASK_DELETE:
                return "Task deleted"

            case EventType.TASK_COMPLETE:
                title = p.get("title") or p.get("name") or ""
                return f"Task completed: {_trim(title)}" if title else "Task completed"

            case EventType.TASK_ASSIGN:
                user = p.get("assignee") or p.get("user_id") or "unknown"
                return f"Task assigned to: {user}"

            # ── Artifact ──────────────────────────────────────────────────
            case EventType.ARTIFACT_CREATE:
                name = p.get("name") or p.get("artifact_name") or ""
                return (
                    f"Artifact created: {_trim(name)}" if name else "Artifact created"
                )

            case EventType.ARTIFACT_UPDATE:
                name = p.get("name") or p.get("artifact_name") or ""
                return (
                    f"Artifact updated: {_trim(name)}" if name else "Artifact updated"
                )

            case EventType.ARTIFACT_DELETE:
                return "Artifact deleted"

            case EventType.ARTIFACT_VERSION:
                version = p.get("version") or p.get("version_id") or ""
                return (
                    f"Artifact version: {version}"
                    if version
                    else "Artifact version created"
                )

            # ── System ────────────────────────────────────────────────────
            case EventType.SYSTEM_ERROR:
                error = p.get("error") or p.get("message") or ""
                return f"System error: {_trim(error)}" if error else "System error"

            case EventType.SYSTEM_NOTIFICATION:
                msg = p.get("message") or p.get("content") or ""
                return f"System: {_trim(msg)}" if msg else "System notification"

            # ── Fallback ──────────────────────────────────────────────────
            case _:
                # Unknown event type — emit a generic line so nothing is silently dropped
                preview = _trim(json.dumps(p, ensure_ascii=False), 120) if p else ""
                label = str(self.event_type)
                return f"[{label}]{': ' + preview if preview else ''}"

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, type='{self.event_type}', workspace_id='{self.workspace_id}', run_id='{self.run_id}')>"
