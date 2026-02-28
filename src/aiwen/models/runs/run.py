# aiwen/models/agent/run.py
"""Run model for the event-sourced architecture."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.core.enums.runs import RunStatus, TriggerType
from aiwen.extensions.database import get_base

if TYPE_CHECKING:
    from aiwen.models.events.event import Event
    from aiwen.models.runs.artifact import Artifact
    from aiwen.models.runs.task import Task
    from aiwen.models.workspaces.workspace import Workspace

Base = get_base("aiwen")

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

    # Relationships
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="runs")
    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="run", cascade="all, delete-orphan"
    )
    parent_run: Mapped[Run | None] = relationship(
        "Run",
        remote_side=[id],
        backref="child_runs",
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        "Artifact", back_populates="run", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Index for workspace runs
        Index("ix_run_workspace", "workspace_id", "created_at"),
        # Index for app runs
        Index("ix_run_app", "app_id", "created_at"),
        # Index for status queries
        Index("ix_run_status", "status", "created_at"),
        # Index for user runs
        Index("ix_run_user", "user_id", "created_at"),
        # Index for parent run lookups
        Index("ix_run_parent", "parent_run_id"),
        # Index for legacy migration
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
