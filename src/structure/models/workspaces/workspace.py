# structure/models/agent/workspace.py
"""Workspace model for the event-sourced architecture."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.core.enums.workspaces import (
    WorkspaceStatus,
    WorkspaceVisibility,
)
from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.events.event import Event
    from structure.models.runs.artifact import Artifact
    from structure.models.runs.run import Run
    from structure.models.runs.task import Task
    from structure.models.workspaces.workspace_member import WorkspaceMember

Base = get_base("structure")


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

    # LLM-generated summary (populated by summarize_workspace Celery task)
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="LLM生成的工作空间摘要"
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
        String(50),
        default=WorkspaceVisibility.PRIVATE,
        comment="可见性: private/team/public",
    )
    is_shared: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已共享"
    )

    # Executor configuration (replaces App lookup for new workspaces)
    executor_code: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="执行器代码"
    )
    executor_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="执行器配置"
    )

    # Configuration
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="工作空间配置"
    )

    # Status
    status: Mapped[WorkspaceStatus] = mapped_column(
        String(50),
        default=WorkspaceStatus.ACTIVE,
        comment="状态: active/archived/deleted",
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

    # Relationships
    runs: Mapped[list[Run]] = relationship(
        "Run", back_populates="workspace", cascade="all, delete-orphan"
    )
    members: Mapped[list[WorkspaceMember]] = relationship(
        "WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="workspace", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        "Artifact", back_populates="workspace", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Index for owner's workspaces
        Index("ix_workspace_owner", "owner_id", "created_at"),
        # Index for visibility queries
        Index("ix_workspace_visibility", "visibility", "status"),
        # Index for legacy migration
        Index("ix_workspace_legacy_conversation", "legacy_conversation_id"),
        # Index for status filtering
        Index("ix_workspace_status", "status", "is_deleted"),
    )

    def __repr__(self) -> str:
        return (
            f"<Workspace(id={self.id}, name='{self.name}', owner_id='{self.owner_id}')>"
        )
