# aiwen/models/context/contextschema.py
"""ContextSchema model for storing agent context with vector embeddings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.core.enums import ContextType
from aiwen.extensions.database import get_base

Base = get_base("aiwen")

class Context(Base):  # ty:ignore[unsupported-base]
    """Database ORM model for agent context with vector embeddings (PERSISTENCE LAYER).

    IMPORTANT: This is the DATABASE model for persistent context storage.
    Do not confuse with:
    - ContextStore (aiwen.frameworks.context.layer) - In-memory hierarchical storage
    - ContextEntry (aiwen.frameworks.context.layer) - Framework node class
    - ContextSchema (aiwen.schemas.context.context_schema) - Pydantic API schema
    - WorkspaceContext (aiwen.models.context.workspace_context) - Workspace-specific contexts

    This model stores:
    - Content with vector embeddings for semantic search
    - Metadata and tags
    - User and source associations
    - Virtual folder paths for organization
    """

    __tablename__ = "context"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="关联的用户ID"
    )
    source_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="关联的源ID"
    )
    # Virtual folder path for organizing contexts (e.g. "/projects/demo/docs")
    path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="虚拟文件夹路径，用于按层级管理上下文"
    )
    # S3 object key for retrieving the actual content
    s3_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="S3对象键，用于获取上下文的实际内容"
    )

    # ContextSchema type
    context_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ContextType.CONVERSATION.value,
        comment="上下文类型: history, tool, knowledge",
    )

    # Progressive disclosure layers (aligned with ContextLayer framework)
    # Layer 1: Glance - one-line summary for quick scanning
    glance: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="一句话摘要（扫描层，快速浏览用）"
    )

    # Layer 2: Overview - structured summary
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="概览摘要（概览层，结构化数据）"
    )

    # Layer 3: Detail - full content
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="完整内容（详情层）")

    # Tags for filtering and categorization
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="标签列表，用于分类和过滤"
    )

    # Keywords for search (deprecated in favor of tags, kept for backward compatibility)
    keywords: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="关键词（已弃用，请使用 tags）"
    )
    # Vector embedding_1536 for similarity search
    embedding_384: Mapped[list[float] | None] = mapped_column(
        Vector(384),  # 轻量级模型维度，如 MiniLM / E5-small / bge-small
        nullable=True,
        comment="384维向量嵌入",
    )

    embedding_768: Mapped[list[float] | None] = mapped_column(
        Vector(
            768
        ),  # 经典BERT类模型维度，如sentence-transformers系列、bge-base、e5-base
        nullable=True,
        comment="768维向量嵌入",
    )

    embedding_1024: Mapped[list[float] | None] = mapped_column(
        Vector(1024),  # 大型模型维度，如bge-large、e5-large
        nullable=True,
        comment="1024维向量嵌入",
    )

    embedding_1536: Mapped[list[float] | None] = mapped_column(
        Vector(1536),  # OpenAI text-embedding-3-small 维度
        nullable=True,
        comment="1536维向量嵌入",
    )

    # Metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外元数据"
    )
    importance: Mapped[int | None] = mapped_column(
        Integer, default=0, comment="重要性评分 0-100"
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    # Indexes for vector similarity search and path queries
    __table_args__ = (
        Index("ix_context_user_id", "user_id"),
        Index("ix_context_type", "context_type"),
        Index("ix_context_path", "path"),  # For prefix queries (e.g., path LIKE 'prefix%')
        Index("ix_context_user_path", "user_id", "path"),  # Composite for user-scoped queries
        Index("ix_context_glance", "glance"),  # For quick scanning queries
        # Vector index using HNSW for fast similarity search
        Index(
            "ix_context_embedding_384_hnsw",
            "embedding_384",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_384": "vector_cosine_ops"},
        ),
        Index(
            "ix_context_embedding_768_hnsw",
            "embedding_768",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_768": "vector_cosine_ops"},
        ),
        Index(
            "ix_context_embedding_1024_hnsw",
            "embedding_1024",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_1024": "vector_cosine_ops"},
        ),
        Index(
            "ix_context_embedding_1536_hnsw",
            "embedding_1536",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_1536": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Context(id={self.id}, type='{self.context_type}', path='{self.path}')>"

    # ──── ContextLayer Framework Methods ────

    def disclose(self, level: str = "overview") -> dict[str, Any]:
        """Progressive disclosure aligned with ContextLayer framework.

        Args:
            level: Disclosure level - "glance", "overview", or "detail"

        Returns:
            Dictionary with appropriate level of information
        """
        result: dict[str, Any] = {"path": self.path}

        # Level 1: Glance - quick scan
        if self.glance:
            result["glance"] = self.glance
        elif self.summary:
            result["glance"] = self.summary[:100] + "..." if len(self.summary) > 100 else self.summary
        else:
            result["glance"] = self.content[:50] + "..." if len(self.content) > 50 else self.content

        if level == "glance":
            return result

        # Level 2: Overview - structured summary
        if level in ("overview", "detail"):
            if self.summary:
                result["overview"] = self.summary
            if self.tags:
                result["tags"] = self.tags

        if level == "overview":
            return result

        # Level 3: Detail - full content
        if level == "detail":
            result["content"] = self.content
            result["meta"] = self.meta or {}
            result["context_type"] = self.context_type
            result["importance"] = self.importance
            if self.s3_key:
                result["s3_key"] = self.s3_key
            if self.keywords:
                result["keywords"] = self.keywords

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
