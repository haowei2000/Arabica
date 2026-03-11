# aiwen/models/agent/event.py
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

from aiwen.core.enums import EventType
from aiwen.extensions.database import get_base

if TYPE_CHECKING:
    from aiwen.models.runs.run import Run
    from aiwen.models.workspaces.workspace import Workspace

Base = get_base("aiwen")

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
        Integer, nullable=False, default=0, server_default="0", comment="本次 LLM 调用的输入 token 数"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="本次 LLM 调用的输出 token 数"
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
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
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
        for key in ("id", "workspace_id", "run_id", "app_id", "user_id", "parent_event_id"):
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
            "id", "event_type", "workspace_id", "run_id", "app_id",
            "user_id", "executor_code", "payload", "sequence",
            "parent_event_id", "created_at",
            "input_tokens", "output_tokens",
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

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, type='{self.event_type}', workspace_id='{self.workspace_id}', run_id='{self.run_id}')>"
