# aiwen/models/agents/docments.py
"""Document model for storing documents belonging to knowledge bases."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base

if TYPE_CHECKING:
    from aiwen.models.agents.chunk import Chunk
    from aiwen.models.agents.knowledge import Knowledge

Base = get_base("aiwen")


class Document(Base):
    """Document table for storing knowledge belonging to knowledge bases."""

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
    file_hash: Mapped[str | None] = mapped_column(String(64), comment="SHA256哈希")
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    object_key: Mapped[str] = mapped_column(String(500), nullable=False, comment="S3对象键")
    file_url: Mapped[str | None] = mapped_column(String(1000), comment="访问URL")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="文件大小（字节）")
    mime_type: Mapped[str | None] = mapped_column(String(100), comment="MIME类型")
    storage_type: Mapped[str] = mapped_column(String(20), default="s3", comment="存储类型: s3/oss/local")
    bucket_name: Mapped[str | None] = mapped_column(String(100), comment="存储桶名称")

    # Content and processing
    content: Mapped[str | None] = mapped_column(Text, comment="文档内容")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="处理状态: pending/processing/completed/failed"
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="处理错误信息")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分块数量")

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
    knowledge: Mapped["Knowledge"] = relationship("Knowledge", back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("ix_document_file_hash", "file_hash"),
        Index("ix_document_user_id", "user_id"),
        Index("ix_document_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, original_name='{self.original_name}', knowledge_id='{self.knowledge_id}')>"
