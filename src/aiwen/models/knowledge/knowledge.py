# aiwen/models/agent/knowledge.py
"""Knowledge management models for storing knowledge bases, knowledge and chunks."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base

if TYPE_CHECKING:
    from aiwen.models.knowledge.documents import Document
    from aiwen.models.knowledge.preprocess import Preprocess

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

    # Relationships
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="knowledge", cascade="all, delete-orphan"
    )
    preprocess: Mapped["Preprocess | None"] = relationship(
        "Preprocess", back_populates="knowledges"
    )

    def __repr__(self) -> str:
        return (
            f"<Knowledge(id={self.id}, name='{self.name}', user_id='{self.user_id}')>"
        )
