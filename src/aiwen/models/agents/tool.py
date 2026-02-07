"""Tool registry model.

Stores definitions of available tools that agents can use.
Each tool has a name, type, input schema, and configuration
for its execution environment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class Tool(Base):
    __tablename__ = "tool"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Tool identification
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="工具名称")
    tool_code: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="工具唯一标识"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="工具描述")

    # Tool type (server/sandbox/client/async)
    tool_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="server",
        server_default="server",
        comment="工具执行类型: server, sandbox, client, async",
    )

    # Schema and configuration
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="工具输入参数的JSON Schema定义",
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="工具执行配置(沙箱镜像、客户端处理器等)",
    )

    # Ownership
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        comment="创建该工具的用户ID",
    )

    # Status
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        comment="是否启用",
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="是否公开可用",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        comment="版本号",
    )

    # Audit fields (timezone-aware)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    def __repr__(self) -> str:
        return (
            f"<Tool(id={self.id}, name='{self.name}', "
            f"tool_code='{self.tool_code}', tool_type='{self.tool_type}', "
            f"enabled={self.enabled})>"
        )
