"""Task model - stores tasks within an agent loop run."""

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

from structure.core.enums.runs import TaskStatus
from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.runs.run import Run
    from structure.models.workspaces.workspace import Workspace

Base = get_base("structure")


class Task(Base):
    """Task model - a unit of work tracked during an agent loop run.

    Tasks represent discrete actions or goals the agent needs to accomplish.
    They can be nested (parent_task_id) for hierarchical planning and
    transition through a simple status lifecycle.
    """

    __tablename__ = "task"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, comment="Task unique ID"
    )

    # Workspace reference
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="Workspace ID",
    )

    # Run reference (optional)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="SET NULL"),
        nullable=True,
        comment="Run ID this task belongs to",
    )

    # Subtask support
    parent_task_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task.id", ondelete="SET NULL"),
        nullable=True,
        comment="Parent task ID for nested tasks",
    )

    # Task content
    title: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="Task title"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Detailed task description"
    )
    result: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Task result or output after completion"
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        default=TaskStatus.PENDING,
        comment="Status: pending/in_progress/done/failed/cancelled",
    )

    # Priority (1=low, 2=normal, 3=high, 4=urgent, 5=critical)
    priority: Mapped[int] = mapped_column(
        Integer, default=2, comment="Priority level (1-5, higher = more urgent)"
    )

    # Assignee (e.g., agent name, tool name, user)
    assignee: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Who this task is assigned to"
    )

    # Flexible metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Additional metadata"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="Creation time",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Completion time"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Last update time",
    )

    # Relationships
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="tasks")
    run: Mapped[Run | None] = relationship("Run", back_populates="tasks")
    parent_task: Mapped[Task | None] = relationship(
        "Task", remote_side=[id], backref="subtasks"
    )

    __table_args__ = (
        Index("ix_task_workspace", "workspace_id", "created_at"),
        Index("ix_task_run", "run_id", "status"),
        Index("ix_task_status", "status", "priority"),
        Index("ix_task_parent", "parent_task_id"),
    )

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, title='{self.title}', status='{self.status}')>"

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
