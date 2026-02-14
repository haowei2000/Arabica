"""WorkspaceContext model - temporary context space scoped to a workspace."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class WorkspaceContext(Base):  # ty:ignore[unsupported-base]
    """Temporary context space scoped to a workspace.

    Each workspace can hold ephemeral context entries (files, notes, snippets,
    intermediate results, etc.) that agents and users reference during a
    session.  Entries are organized via a virtual ``path`` and the actual
    content can live in S3 (referenced by ``s3_key``).
    """

    __tablename__ = "workspace_context"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Owning workspace
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属工作空间ID",
    )

    # Who created this entry
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="创建者用户ID"
    )

    # Virtual path for folder-like organization (e.g. "/uploads/report.pdf")
    path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="虚拟文件路径，用于按层级管理上下文"
    )

    # Display name for the entry
    name: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="上下文条目名称"
    )

    # Content type hint (e.g. "text/plain", "application/pdf", "code/python")
    content_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="内容MIME类型"
    )

    # Inline content (for small text payloads)
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="上下文内容（小型文本直接存储）"
    )

    # S3 object key for large / binary content
    s3_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="S3对象键，用于获取实际内容"
    )

    # Size in bytes (for quota / UI display)
    size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="内容大小（字节）"
    )

    # Arbitrary metadata (tags, source info, processing status, etc.)
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外元数据"
    )

    # Soft delete / expiration
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已删除"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间（临时上下文自动清理）"
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

    __table_args__ = (
        Index("ix_ws_ctx_workspace", "workspace_id"),
        Index("ix_ws_ctx_workspace_path", "workspace_id", "path"),
        Index("ix_ws_ctx_created_by", "created_by"),
        Index("ix_ws_ctx_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceContext(id={self.id}, workspace_id={self.workspace_id}, "
            f"name='{self.name}', path='{self.path}')>"
        )
