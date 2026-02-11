# aiwen/models/event_sourcing.py
"""Event Sourcing Core Models - Workspace, Run, Event.

This module contains the three core models of the event-sourced architecture.
By placing them in the same file, we eliminate circular imports while maintaining
proper type annotations without using string references.

Architecture:
- Workspace: Top-level container for collaboration
- Run: Execution unit within a workspace
- Event: Immutable event log for all system activities
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.enums.events import EventType
from aiwen.enums.runs import RunStatus, TriggerType
from aiwen.enums.workspaces import WorkspaceStatus, WorkspaceVisibility
from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class Workspace(Base):
    """Workspace model - top-level container for runs and events.

    A workspace represents a collaborative space where users can create runs,
    share context, and collaborate on agent-assisted tasks.
    """

    __tablename__ = "workspace"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="工作空间唯一标识",
    )

    # Basic information
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="工作空间名称"
    )
    description: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="工作空间描述"
    )

    # Ownership
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="所有者用户ID"
    )

    # Default app (optional)
    app_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app.id", ondelete="SET NULL"),
        nullable=True,
        comment="默认应用ID",
    )

    # Visibility and sharing
    visibility: Mapped[WorkspaceVisibility] = mapped_column(
        String(50), default=WorkspaceVisibility.PRIVATE, comment="可见性: private/team/public"
    )
    is_shared: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已共享"
    )

    # Configuration
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="工作空间配置"
    )

    # Status
    status: Mapped[WorkspaceStatus] = mapped_column(
        String(50), default=WorkspaceStatus.ACTIVE, comment="状态: active/archived/deleted"
    )

    # Denormalized counts for performance
    run_count: Mapped[int] = mapped_column(Integer, default=0, comment="运行次数")
    member_count: Mapped[int] = mapped_column(Integer, default=1, comment="成员数量")

    # Migration support - link to legacy Conversation
    legacy_conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="关联的旧会话ID(迁移支持)"
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已删除"
    )

    # Relationships - using actual class references (no strings needed!)
    runs: Mapped[list[Run]] = relationship(
        "Run", back_populates="workspace", cascade="all, delete-orphan"
    )
    members: Mapped[list[WorkspaceMember]] = relationship(
        "WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_workspace_owner", "owner_id", "created_at"),
        Index("ix_workspace_visibility", "visibility", "status"),
        Index("ix_workspace_legacy_conversation", "legacy_conversation_id"),
        Index("ix_workspace_status", "status", "is_deleted"),
    )

    def __repr__(self) -> str:
        return f"<Workspace(id={self.id}, name='{self.name}', owner_id='{self.owner_id}')>"


class Run(Base):
    """Run model - represents a single execution unit within a workspace.

    A run is an execution of an agent/app, initiated by a user message or
    triggered by a tool callback or system event. Runs can be nested
    (parent_run_id) for complex multi-step operations.

    State Machine:
        pending -> running -> waiting | finished | failed | cancelled
        waiting -> running | cancelled
        finished, cancelled, failed -> (terminal)
    """

    __tablename__ = "run"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, comment="运行唯一标识"
    )

    # Workspace reference
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="工作空间ID",
    )

    # App reference
    app_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app.id", ondelete="CASCADE"),
        nullable=True,
        comment="应用ID",
    )

    # User who initiated the run
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="发起用户ID"
    )

    # Nested runs support
    parent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="SET NULL"),
        nullable=True,
        comment="父运行ID",
    )

    # Status (state machine)
    status: Mapped[RunStatus] = mapped_column(
        String(50),
        default=RunStatus.PENDING,
        comment="状态: pending/running/waiting/finished/cancelled/failed",
    )

    # Trigger type
    trigger_type: Mapped[TriggerType] = mapped_column(
        String(50),
        default=TriggerType.USER,
        comment="触发类型: user/tool_callback/agent/system",
    )

    # Input and output data
    input_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="初始输入数据"
    )
    output_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="最终输出数据"
    )

    # Error information
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误消息")
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="错误代码"
    )

    # Waiting state information
    waiting_for: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="等待信息(工具审批、用户输入等)"
    )

    # Event tracking
    last_event_sequence: Mapped[int] = mapped_column(
        Integer, default=0, comment="最后事件序列号"
    )

    # Migration support - link to legacy AgentTask
    legacy_task_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="关联的旧任务ID(迁移支持)"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始执行时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    # Relationships - using actual class references (no strings!)
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="runs")
    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="run", cascade="all, delete-orphan"
    )
    parent_run: Mapped[Run | None] = relationship(
        "Run",
        remote_side=[id],
        backref="child_runs",
    )

    __table_args__ = (
        Index("ix_run_workspace", "workspace_id", "created_at"),
        Index("ix_run_app", "app_id", "created_at"),
        Index("ix_run_status", "status", "created_at"),
        Index("ix_run_user", "user_id", "created_at"),
        Index("ix_run_parent", "parent_run_id"),
        Index("ix_run_legacy_task", "legacy_task_id"),
    )

    def __repr__(self) -> str:
        return f"<Run(id={self.id}, workspace_id='{self.workspace_id}', status='{self.status}')>"

    @property
    def is_terminal(self) -> bool:
        """Check if the run is in a terminal state."""
        return self.status in (
            RunStatus.FINISHED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        )

    @property
    def is_active(self) -> bool:
        """Check if the run is actively processing."""
        return self.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING)


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

    # Relationships - using actual class references!
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="events")
    run: Mapped[Run | None] = relationship("Run", back_populates="events")
    parent_event: Mapped[Event | None] = relationship(
        "Event",
        remote_side=[id],
        backref="child_events",
    )

    __table_args__ = (
        Index("ix_event_workspace_sequence", "workspace_id", "sequence"),
        Index("ix_event_run_sequence", "run_id", "sequence"),
        Index("ix_event_type_created", "event_type", "created_at"),
        Index("ix_event_parent", "parent_event_id"),
        Index("ix_event_executor_code", "executor_code"),
    )

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, type='{self.event_type}', workspace_id='{self.workspace_id}', run_id='{self.run_id}')>"


# Import WorkspaceMember for the relationship
# This is placed at the end to avoid circular imports during module loading
from aiwen.models.workspaces.workspace_member import WorkspaceMember  # noqa: E402
