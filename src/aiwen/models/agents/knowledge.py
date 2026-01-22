# aiwen/models/agents/knowledge.py
"""Knowledge management models for storing knowledge bases, documents and chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class Knowledge(Base):
    """Knowledge table for storing knowledge base information."""

    __tablename__ = "knowledge"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Knowledge base information
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="知识库名称")
    description: Mapped[str | None] = mapped_column(Text, comment="知识库描述")
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="创建知识库的用户ID"
    )
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), comment="知识库所有者ID"
    )

    # Knowledge base configuration
    provider: Mapped[str] = mapped_column(String(50), default="default", comment="知识库提供商")
    indexing_technique: Mapped[str] = mapped_column(String(50), default="high_quality", comment="索引技术")
    embedding_model: Mapped[str | None] = mapped_column(String(255), comment="嵌入模型")

    # Status and permissions
    status: Mapped[str] = mapped_column(String(20), default="active", comment="知识库状态")
    permission: Mapped[str] = mapped_column(String(20), default="private", comment="权限设置")

    # Metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外元数据"
    )
    document_count: Mapped[int] = mapped_column(Integer, default=0, comment="文档数量")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分段数量")

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
    documents: Mapped[list[Document]] = relationship("Document", back_populates="knowledge",
                                                     cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Knowledge(id={self.id}, name='{self.name}', user_id='{self.user_id}')>"


class Document(Base):
    """Document table for storing documents belonging to knowledge bases."""

    __tablename__ = "document"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Document information
    knowledge_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属知识库ID"
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="上传文档的用户ID"
    )

    # File information
    file_hash: Mapped[str | None] = mapped_column(String(64), unique=True, comment="SHA256哈希，用于去重")
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    object_key: Mapped[str] = mapped_column(String(500), nullable=False, comment="S3对象键")
    file_url: Mapped[str | None] = mapped_column(String(1000), comment="访问URL")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="文件大小（字节）")
    mime_type: Mapped[str | None] = mapped_column(String(100), comment="MIME类型")
    storage_type: Mapped[str] = mapped_column(String(20), default="s3", comment="存储类型: s3/oss/local")
    bucket_name: Mapped[str | None] = mapped_column(String(100), comment="存储桶名称")

    # Content and processing
    content: Mapped[str | None] = mapped_column(Text, comment="文档内容")

    # Reference and status
    reference_count: Mapped[int] = mapped_column(Integer, default=0, comment="引用计数，用于去重")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已删除")

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
    knowledge: Mapped[Knowledge] = relationship("Knowledge", back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("ix_document_file_hash", "file_hash"),
        Index("ix_document_user_id", "user_id"),
        Index("ix_document_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, original_name='{self.original_name}', knowledge_id='{self.knowledge_id}')>"


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
        comment="所属文档ID"
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="所属用户ID"
    )

    # Content
    position: Mapped[int] = mapped_column(Integer, comment="分段位置")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分段内容")
    content_length: Mapped[int] = mapped_column(Integer, default=0, comment="内容长度")

    # Processing and status
    status: Mapped[str] = mapped_column(String(20), default="completed", comment="处理状态")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")

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
    document: Mapped[Document] = relationship("Document", back_populates="chunks")

    def __repr__(self) -> str:
        return f"<Chunk(id={self.id}, document_id='{self.document_id}', position={self.position})>"
