# aiwen/models/agents/context.py
"""Context model for storing agent context with vector embeddings."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class ContextType(str, Enum):
    """Context type enumeration."""
    HISTORY = "history"      # Conversation history context
    TOOL = "tool"            # Tool usage context
    KNOWLEDGE = "knowledge"  # Knowledge base context


class Context(Base):
    """Context table for storing agent context with vector embeddings."""
    __tablename__ = 'context'

    # Primary key
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    # Relationships
    app_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, comment='关联的应用ID')
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, comment='关联的用户ID')
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, comment='关联的对话ID')

    # Context type
    context_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ContextType.HISTORY.value,
        comment='上下文类型: history, tool, knowledge'
    )

    # Context content
    content: Mapped[str] = mapped_column(Text, nullable=False, comment='上下文内容')
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment='上下文摘要')

    # Vector embedding for similarity search
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536),  # OpenAI text-embedding-3-small dimension
        nullable=True,
        comment='向量嵌入'
    )

    # Metadata
    metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, comment='额外元数据')
    source: Mapped[str | None] = mapped_column(String(255), nullable=True, comment='上下文来源')
    importance: Mapped[int | None] = mapped_column(Integer, default=0, comment='重要性评分 0-100')

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment='创建时间'
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment='更新时间'
    )

    # Indexes for vector similarity search
    __table_args__ = (
        Index('ix_context_app_id', 'app_id'),
        Index('ix_context_user_id', 'user_id'),
        Index('ix_context_type', 'context_type'),
        Index('ix_context_conversation_id', 'conversation_id'),
        # Vector index using HNSW for fast similarity search
        Index(
            'ix_context_embedding_hnsw',
            'embedding',
            postgresql_using='hnsw',
            postgresql_with={'m': 16, 'ef_construction': 64},
            postgresql_ops={'embedding': 'vector_cosine_ops'}
        ),
    )

    def __repr__(self) -> str:
        return f"<Context(id={self.id}, type='{self.context_type}', user_id='{self.user_id}')>"
