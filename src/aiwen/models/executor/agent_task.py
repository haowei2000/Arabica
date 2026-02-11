# aiwen/models/agent/agent_task.py
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


def _to_jsonable(value: Any) -> Any:
    """Recursively convert Python objects into JSON-serializable forms.

    - UUID -> str
    - datetime/date -> ISO string
    - Decimal -> float
    - BaseModel -> model_dump(mode='json')
    - dict/list/tuple -> walk recursively
    """
    if value is None:
        return None
    if isinstance(value, BaseModel):
        # Let Pydantic produce JSON-friendly data
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class PydanticJSONB(TypeDecorator):
    """自动处理 Pydantic 模型的 JSONB 类型"""

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """存入数据库前转换为可 JSON 序列化的结构"""
        return _to_jsonable(value)

    def process_result_value(self, value, dialect):
        """从数据库读取后保持原样（调用方可自行解析）"""
        return value


class AgentTask(Base):
    __tablename__ = "agent_task"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Task identification
    app_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="关联的agent标识"
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="关联的用户标识"
    )
    task_type: Mapped[str | None] = mapped_column(String, comment="任务类型")

    # Status and data
    status: Mapped[str] = mapped_column(
        String, default="pending", comment="任务状态: pending, running, success, failed"
    )

    payload: Mapped[Any | None] = mapped_column(PydanticJSONB, comment="任务输入数据")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="任务结果")
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")

    # Progress tracking
    progress: Mapped[int | None] = mapped_column(Integer, comment="任务进度百分比")

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
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="开始执行时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="完成时间"
    )

    def __repr__(self) -> str:
        return f"<AgentTask(id={self.id}, app_id='{self.app_id}', user_id='{self.user_id}', status='{self.status}')>"
