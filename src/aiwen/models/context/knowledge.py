# aiwen/models/context/knowledge.py
"""Knowledge Management Models - Unified module for all knowledge-related models.

This module contains all models related to knowledge base management:
- Knowledge: Knowledge base container
- Document: Document storage and metadata
- Chunk: Text chunks with vector embeddings
- Preprocess: Processing configuration

By placing them in the same file, we eliminate circular imports while maintaining
proper type annotations without using string references.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class Preprocess(Base):
    """Preprocess table for defining document processing configurations."""

    __tablename__ = "preprocess"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Basic information
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="预处理方法名称"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="预处理方法描述")
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="创建者用户ID"
    )

    # Chunking configuration
    chunking_strategy: Mapped[str] = mapped_column(
        String(50),
        default="fixed_size",
        comment="分块策略: fixed_size/semantic/sentence/paragraph",
    )
    chunk_size: Mapped[int] = mapped_column(
        Integer, default=512, comment="分块大小（字符数）"
    )
    chunk_overlap: Mapped[int] = mapped_column(
        Integer, default=50, comment="分块重叠（字符数）"
    )
    separator: Mapped[str | None] = mapped_column(String(100), comment="分隔符")

    # Text cleaning configuration
    remove_extra_whitespace: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="移除多余空白"
    )
    remove_urls: Mapped[bool] = mapped_column(Boolean, default=False, comment="移除URL")
    remove_emails: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="移除邮箱"
    )
    lowercase: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="转换为小写"
    )

    # Embedding configuration
    embedding_model: Mapped[str | None] = mapped_column(
        String(255), comment="嵌入模型名称"
    )
    embedding_dimension: Mapped[int | None] = mapped_column(
        Integer, comment="嵌入向量维度"
    )

    # Parser configuration
    parser_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="解析器配置（针对不同文件类型）"
    )

    # Additional metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外元数据"
    )

    # Status
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否为默认配置"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")

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

    # Relationships - using actual class references!
    knowledges: Mapped[list[Knowledge]] = relationship(
        "Knowledge", back_populates="preprocess"
    )

    def __repr__(self) -> str:
        return f"<Preprocess(id={self.id}, name='{self.name}', chunking_strategy='{self.chunking_strategy}')>"


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
    provider: Mapped[str] = mapped_column(
        String(50), default="default", comment="知识库提供商"
    )
    indexing_technique: Mapped[str] = mapped_column(
        String(50), default="high_quality", comment="索引技术"
    )
    embedding_model: Mapped[str | None] = mapped_column(String(255), comment="嵌入模型")
    preprocess_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("preprocess.id", ondelete="SET NULL"),
        nullable=True,
        comment="预处理配置ID",
    )

    # Status and permissions
    status: Mapped[str] = mapped_column(
        String(20), default="active", comment="知识库状态"
    )
    permission: Mapped[str] = mapped_column(
        String(20), default="private", comment="权限设置"
    )

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

    # Relationships - using actual class references!
    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="knowledge", cascade="all, delete-orphan"
    )
    preprocess: Mapped[Preprocess | None] = relationship(
        "Preprocess", back_populates="knowledges"
    )

    def __repr__(self) -> str:
        return (
            f"<Knowledge(id={self.id}, name='{self.name}', user_id='{self.user_id}')>"
        )


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
        comment="所属知识库ID",
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="上传文档的用户ID"
    )

    # File information
    file_hash: Mapped[str | None] = mapped_column(String(64), comment="SHA256哈希")
    original_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="原始文件名"
    )
    object_key: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="S3对象键"
    )
    file_url: Mapped[str | None] = mapped_column(String(1000), comment="访问URL")
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="文件大小（字节）"
    )
    mime_type: Mapped[str | None] = mapped_column(String(100), comment="MIME类型")
    storage_type: Mapped[str] = mapped_column(
        String(20), default="s3", comment="存储类型: s3/oss/local"
    )
    bucket_name: Mapped[str | None] = mapped_column(String(100), comment="存储桶名称")

    # Content and processing
    content: Mapped[str | None] = mapped_column(Text, comment="文档内容")
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="处理状态: pending/processing/completed/failed",
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="处理错误信息")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分块数量")

    # Reference and status
    reference_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="引用计数，用于去重"
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已删除"
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

    # Relationships - using actual class references!
    knowledge: Mapped[Knowledge] = relationship(
        "Knowledge", back_populates="documents"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )

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

    # Relationships - using actual class reference!
    document: Mapped[Document] = relationship("Document", back_populates="chunks")

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
