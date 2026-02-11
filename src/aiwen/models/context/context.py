# aiwen/models/context/contextschema.py
"""ContextSchema model for storing agent context with vector embeddings."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class ContextType(StrEnum):
    """Context type options."""

    CHUNK = "CHUNK"
    CONVERSATION = "conversation"
    MESSAGE = "message"
    USER_MEMORY = "user_memory"
    SKILL = "SKILL"
    TOOL = "tool"
    KNOWLEDGE = "knowledge"


class Context(Base):  # ty:ignore[unsupported-base]
    """ContextSchema table for storing agent context with vector embeddings."""

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
    # ContextSchema type
    context_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ContextType.CONVERSATION.value,
        comment="上下文类型: history, tool, knowledge",
    )

    # ContextSchema content
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="上下文内容")
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="上下文摘要"
    )
    keywords: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="关键词"
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

    # Indexes for vector similarity search
    __table_args__ = (
        Index("ix_context_user_id", "user_id"),
        Index("ix_context_type", "context_type"),
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
        return f"<ContextSchema(id={self.id}, type='{self.context_type}', user_id='{self.user_id}')>"
