"""structure/models/executor/executor.py
Executor model for storing executor configurations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from structure.extensions.database import get_base

Base = get_base("structure")


class ExecutorTemplate(Base):
    """Executor configuration template stored in database.

    Note: This is the ORM model for persisting executor templates.
    For the abstract executor interface, see structure.core.interfaces.executor.Executor
    """

    __tablename__ = "executor"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Executor identification
    executor_code: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, comment="执行器标识"
    )
    executor_name: Mapped[str] = mapped_column(
        String, nullable=False, comment="执行器名称"
    )

    # Status and configuration
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否启用"
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="执行器参数配置"
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", comment="版本号"
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
        return f"<ExecutorTemplate(id={self.id}, executor_code='{self.executor_code}', executor_name='{self.executor_name}', enabled={self.enabled})>"
