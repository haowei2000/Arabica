# aiwen/models/agents/event.py
"""Event model for event-sourced architecture."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base

if TYPE_CHECKING:
    from aiwen.models.agents.run import Run
    from aiwen.models.agents.workspace import Workspace

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
    event_type: Mapped[str] = mapped_column(
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

    # User who triggered the event
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="触发事件的用户ID",
    )

    # Event payload (flexible JSON structure)
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="事件数据"
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
    workspace: Mapped["Workspace"] = relationship(
        "Workspace", back_populates="events"
    )
    run: Mapped["Run | None"] = relationship(
        "Run", back_populates="events"
    )
    parent_event: Mapped["Event | None"] = relationship(
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
    )

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, type='{self.event_type}', workspace_id='{self.workspace_id}', run_id='{self.run_id}')>"
