# aiwen/models/workspaces/workspace_trigger.py
"""WorkspaceTrigger model - per-workspace event trigger rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class WorkspaceTrigger(Base):
    """Per-workspace trigger rule that auto-executes context operations on events.

    When an event matching the condition occurs, the action is executed and
    the result is injected into the event payload under `_trigger_context`.
    """

    __tablename__ = "workspace_trigger"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="触发器唯一标识",
    )

    # Workspace association (nullable for user-level trigger templates)
    workspace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="所属工作区ID（用户级模板时为 null）",
    )

    # Owner user (for user-level trigger templates)
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="创建者用户ID（用于用户级模板）",
    )

    # Display info
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="触发器名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="触发器描述"
    )

    # Event matching
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="监听的事件类型，如 user.message"
    )

    # Condition
    condition_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="always",
        comment="条件类型: always | keyword | regex | jsonpath",
    )
    condition_value: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="条件表达式（关键字/正则/JSONPath）"
    )
    condition_field: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default="message",
        comment="检查的 payload 字段名，默认 message，支持点记法如 data.text",
    )

    # Action
    tool_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="要执行的工具名称（任意已注册工具，如 glance_context / http_request 等）",
    )
    action_params: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="传递给工具的参数（workspace_id 会自动注入）"
    )

    # Execution control
    priority: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="优先级（数值越小优先级越高）"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否启用"
    )

    # Audit
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="创建者用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    __table_args__ = (
        Index(
            "ix_workspace_trigger_ws_event",
            "workspace_id",
            "event_type",
            "enabled",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceTrigger(id={self.id}, name='{self.name}', "
            f"event_type='{self.event_type}', enabled={self.enabled})>"
        )
