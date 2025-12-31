"""aiwen/models/agents/agent.py
Edited to use timezone-aware DateTime columns and timezone-aware Python defaults.
"""
from __future__ import annotations

from datetime import UTC, datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")  # Assuming agents are stored in the default database


class App(Base):
    __tablename__ = 'app'

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Agent identification
    app_code: Mapped[str] = mapped_column(String, unique=True, nullable=False, comment='对外使用的agent标识')
    agent_template_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), comment='构建该app使用的模板ID')

    # User relationship
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), comment='创建该app的用户ID')

    # Status and configuration
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true', comment='是否启用')
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment='Agent参数配置')
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1', comment='版本号')

    # Audit fields (timezone-aware)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        comment='创建时间'
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment='更新时间'
    )

    def __repr__(self) -> str:
        return f"<App(id={self.id}, app_code='{self.app_code}', agent_template_id='{self.agent_template_id}', enabled={self.enabled})>"
