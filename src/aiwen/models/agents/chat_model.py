# aiwen/models/agent/chat_model.py
"""ChatModel for managing LLM/chat model configurations."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class ChatModel(Base):
    """ChatModel table for storing LLM/chat model configurations."""

    __tablename__ = "chat_model"

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
        comment="提供商: openai/anthropic/dashscope/ollama/azure/custom",
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="模型标识符: gpt-4/claude-3-opus/qwen-turbo",
    )

    # API configuration
    base_url: Mapped[str | None] = mapped_column(String(500), comment="API基础URL")
    api_key_ref: Mapped[str | None] = mapped_column(
        String(255), comment="API密钥引用（存储密钥名称，非实际密钥）"
    )

    # Model capabilities
    max_tokens: Mapped[int | None] = mapped_column(Integer, comment="最大输出token数")
    context_window: Mapped[int | None] = mapped_column(
        Integer, comment="上下文窗口大小"
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否支持视觉输入"
    )
    supports_function_call: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否支持函数调用"
    )
    supports_streaming: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否支持流式输出"
    )

    # Default parameters
    default_temperature: Mapped[float | None] = mapped_column(
        Float, default=0.7, comment="默认温度"
    )
    default_top_p: Mapped[float | None] = mapped_column(
        Float, default=1.0, comment="默认top_p"
    )
    default_max_tokens: Mapped[int | None] = mapped_column(
        Integer, comment="默认最大输出token"
    )

    # Pricing (per 1K tokens)
    input_price: Mapped[float | None] = mapped_column(
        Float, comment="输入价格（每1K tokens）"
    )
    output_price: Mapped[float | None] = mapped_column(
        Float, comment="输出价格（每1K tokens）"
    )
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
        return f"<ChatModel(id={self.id}, name='{self.name}', provider='{self.provider}', model_id='{self.model_id}')>"
