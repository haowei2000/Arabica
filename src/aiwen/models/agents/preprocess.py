# aiwen/models/agents/preprocess.py
"""Preprocess model for defining document processing methods."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from aiwen.models.agents.knowledge import Knowledge

from sqlalchemy import DateTime, Integer, String, Text, Boolean
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
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="预处理方法名称")
    description: Mapped[str | None] = mapped_column(Text, comment="预处理方法描述")
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="创建者用户ID"
    )

    # Chunking configuration
    chunking_strategy: Mapped[str] = mapped_column(
        String(50), default="fixed_size", comment="分块策略: fixed_size/semantic/sentence/paragraph"
    )
    chunk_size: Mapped[int] = mapped_column(Integer, default=512, comment="分块大小（字符数）")
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=50, comment="分块重叠（字符数）")
    separator: Mapped[str | None] = mapped_column(String(100), comment="分隔符")

    # Text cleaning configuration
    remove_extra_whitespace: Mapped[bool] = mapped_column(Boolean, default=True, comment="移除多余空白")
    remove_urls: Mapped[bool] = mapped_column(Boolean, default=False, comment="移除URL")
    remove_emails: Mapped[bool] = mapped_column(Boolean, default=False, comment="移除邮箱")
    lowercase: Mapped[bool] = mapped_column(Boolean, default=False, comment="转换为小写")

    # Embedding configuration
    embedding_model: Mapped[str | None] = mapped_column(String(255), comment="嵌入模型名称")
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, comment="嵌入向量维度")

    # Parser configuration
    parser_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="解析器配置（针对不同文件类型）"
    )

    # Additional metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外元数据"
    )

    # Status
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为默认配置")
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

    # Relationships
    knowledges: Mapped[list["Knowledge"]] = relationship("Knowledge", back_populates="preprocess")

    def __repr__(self) -> str:
        return f"<Preprocess(id={self.id}, name='{self.name}', chunking_strategy='{self.chunking_strategy}')>"
