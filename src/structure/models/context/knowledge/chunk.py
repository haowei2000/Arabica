from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.context.knowledge.documents import Document

Base = get_base("structure")


class Chunk(Base):
    """Chunk table for storing document segments/chunks with vector embeddings."""

    __tablename__ = "chunk"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Chunk information
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档ID",
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="所属用户ID"
    )

    # Content
    position: Mapped[int] = mapped_column(Integer, comment="分段位置")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分段内容")
    content_length: Mapped[int] = mapped_column(Integer, default=0, comment="内容长度")

    # Processing and status
    status: Mapped[str] = mapped_column(
        String(20), default="completed", comment="处理状态"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")

    # Vector embeddings for similarity search
    embedding_384: Mapped[list[float] | None] = mapped_column(
        Vector(384),
        nullable=True,
        comment="384维向量嵌入 (MiniLM / E5-small / bge-small)",
    )
    embedding_768: Mapped[list[float] | None] = mapped_column(
        Vector(768),
        nullable=True,
        comment="768维向量嵌入 (BERT / bge-base / e5-base)",
    )
    embedding_1024: Mapped[list[float] | None] = mapped_column(
        Vector(1024),
        nullable=True,
        comment="1024维向量嵌入 (bge-large / e5-large)",
    )
    embedding_1536: Mapped[list[float] | None] = mapped_column(
        Vector(1536),
        nullable=True,
        comment="1536维向量嵌入 (OpenAI / DashScope)",
    )

    # Metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="分段元数据"
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

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    # Indexes for vector similarity search
    __table_args__ = (
        Index("ix_chunk_document_id", "document_id"),
        Index("ix_chunk_user_id", "user_id"),
        Index("ix_chunk_status", "status"),
        # HNSW indexes for vector similarity search
        Index(
            "ix_chunk_embedding_384_hnsw",
            "embedding_384",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_384": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunk_embedding_768_hnsw",
            "embedding_768",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_768": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunk_embedding_1024_hnsw",
            "embedding_1024",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_1024": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunk_embedding_1536_hnsw",
            "embedding_1536",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding_1536": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Chunk(id={self.id}, document_id='{self.document_id}', position={self.position})>"
