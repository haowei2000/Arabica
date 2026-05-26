# structure/models/agent/embedding_model.py
"""EmbeddingModel for managing embedding model configurations."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from structure.extensions.database import get_base

Base = get_base("structure")


class EmbeddingModel(Base):
    """EmbeddingModel table for storing embedding model configurations."""

    __tablename__ = "embedding_model"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Basic information
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="模型显示名称"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="模型描述")
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), comment="创建者用户ID（系统级模型为空）"
    )

    # Provider and model identification
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="提供商: openai/custom(OpenAI-compatible)",
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="模型标识符: text-embedding-3-small",
    )

    # API configuration
    base_url: Mapped[str | None] = mapped_column(String(500), comment="API基础URL")
    api_key_ref: Mapped[str | None] = mapped_column(
        String(255), comment="API密钥引用（存储密钥名称，非实际密钥）"
    )

    # ChatLLM specifications
    dimension: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="向量维度: 384/768/1024/1536"
    )
    max_tokens: Mapped[int | None] = mapped_column(Integer, comment="最大输入token数")
    supports_batch: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否支持批量处理"
    )
    batch_size: Mapped[int] = mapped_column(
        Integer, default=32, comment="默认批处理大小"
    )

    # Normalization and distance metric
    normalize: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否归一化向量"
    )
    distance_metric: Mapped[str] = mapped_column(
        String(20), default="cosine", comment="距离度量: cosine/euclidean/dot_product"
    )

    # Pricing (per 1K tokens)
    price: Mapped[float | None] = mapped_column(Float, comment="价格（每1K tokens）")
    currency: Mapped[str] = mapped_column(String(10), default="USD", comment="货币单位")

    # Additional configuration
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="额外配置参数"
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="元数据"
    )

    # Status
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否为系统预置模型"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否为默认模型"
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

    def __repr__(self) -> str:
        return f"<EmbeddingModel(id={self.id}, name='{self.name}', provider='{self.provider}', dimension={self.dimension})>"
