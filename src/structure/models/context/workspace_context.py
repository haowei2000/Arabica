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

from structure.extensions.database import get_base

Base = get_base("structure")


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

    # Progressive disclosure layers (aligned with ContextLayer framework)
    # Layer 1: Glance - one-line summary for quick scanning
    glance: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="一句话摘要（扫描层）"
    )

    # Layer 2: Overview - structured summary
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="概览摘要（概览层）"
    )

    # Layer 3: Detail - full content
    # Inline content (for small text payloads)
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="完整内容（详情层，小型文本直接存储）"
    )

    # S3 object key for large / binary content
    s3_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="S3对象键，用于获取实际内容"
    )

    # Size in bytes (for quota / UI display)
    size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="内容大小（字节）"
    )

    # Tags for filtering and categorization
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="标签列表，用于分类和过滤"
    )

    # Arbitrary metadata (source info, processing status, etc.)
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
        Index("ix_ws_ctx_glance", "glance"),  # For quick scanning
        Index("ix_ws_ctx_workspace_deleted", "workspace_id", "is_deleted"),  # For active contexts
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceContext(id={self.id}, workspace_id={self.workspace_id}, "
            f"name='{self.name}', path='{self.path}')>"
        )

    # ──── ContextLayer Framework Methods ────

    def disclose(self, level: str = "overview") -> dict[str, Any]:
        """Progressive disclosure aligned with ContextLayer framework.

        Args:
            level: Disclosure level - "glance", "overview", or "detail"

        Returns:
            Dictionary with appropriate level of information
        """
        result: dict[str, Any] = {"path": self.path, "name": self.name}

        # Level 1: Glance - quick scan
        if self.glance:
            result["glance"] = self.glance
        elif self.summary:
            result["glance"] = self.summary[:100] + "..." if len(self.summary) > 100 else self.summary
        elif self.content:
            result["glance"] = self.content[:50] + "..." if len(self.content) > 50 else self.content
        else:
            result["glance"] = f"{self.name} ({self.content_type or 'unknown'})"

        if level == "glance":
            return result

        # Level 2: Overview - structured summary
        if level in ("overview", "detail"):
            if self.summary:
                result["overview"] = self.summary
            result["content_type"] = self.content_type
            if self.size_bytes is not None:
                result["size_bytes"] = self.size_bytes
            if self.tags:
                result["tags"] = self.tags

        if level == "overview":
            return result

        # Level 3: Detail - full content
        if level == "detail":
            result["content"] = self.content
            result["meta"] = self.meta or {}
            if self.s3_key:
                result["s3_key"] = self.s3_key
            result["created_by"] = str(self.created_by) if self.created_by else None
            result["created_at"] = self.created_at.isoformat() if self.created_at else None
            result["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
            if self.expires_at:
                result["expires_at"] = self.expires_at.isoformat()

        return result

    def get_path_depth(self) -> int:
        """Get the depth of this context's path.

        Returns:
            Number of path segments (0 if no path)
        """
        if not self.path:
            return 0
        return len(self.path.strip("/").split("/"))

    def get_parent_path(self) -> str | None:
        """Get the parent path of this context.

        Returns:
            Parent path or None if this is a root path
        """
        if not self.path:
            return None
        parts = self.path.strip("/").rsplit("/", 1)
        return f"/{parts[0]}" if len(parts) > 1 else None

    def matches_prefix(self, prefix: str) -> bool:
        """Check if this context's path starts with the given prefix.

        Args:
            prefix: Path prefix to match

        Returns:
            True if path matches prefix
        """
        if not self.path or not prefix:
            return False
        normalized_path = self.path.strip("/")
        normalized_prefix = prefix.strip("/")
        return normalized_path.startswith(normalized_prefix)

    def has_tag(self, tag: str) -> bool:
        """Check if this context has a specific tag.

        Args:
            tag: Tag to check

        Returns:
            True if tag exists
        """
        return self.tags is not None and tag in self.tags

    def has_any_tag(self, tags: list[str]) -> bool:
        """Check if this context has any of the given tags.

        Args:
            tags: List of tags to check

        Returns:
            True if any tag exists
        """
        if not self.tags:
            return False
        return any(tag in self.tags for tag in tags)

    def has_all_tags(self, tags: list[str]) -> bool:
        """Check if this context has all of the given tags.

        Args:
            tags: List of tags to check

        Returns:
            True if all tags exist
        """
        if not self.tags:
            return False
        return all(tag in self.tags for tag in tags)

    @property
    def has_s3_content(self) -> bool:
        """Check if this context has content stored in S3."""
        return self.s3_key is not None and len(self.s3_key) > 0

    @property
    def has_inline_content(self) -> bool:
        """Check if this context has inline content."""
        return self.content is not None and len(self.content) > 0

    @property
    def is_expired(self) -> bool:
        """Check if this context has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at
