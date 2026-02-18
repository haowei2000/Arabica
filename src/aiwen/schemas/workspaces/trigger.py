# aiwen/schemas/workspaces/trigger.py
"""Pydantic schemas for WorkspaceTrigger API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aiwen.utils.schema_mixins import ResponseMixin


class TriggerCreate(BaseModel):
    """Schema for creating a new workspace trigger."""

    name: str = Field(..., min_length=1, max_length=255, description="触发器名称")
    description: str | None = Field(None, description="触发器描述")
    event_type: str = Field(..., min_length=1, max_length=100, description="监听的事件类型，如 user.message")
    condition_type: str = Field(
        "always",
        description="条件类型: always | keyword | regex | jsonpath",
    )
    condition_value: str | None = Field(None, description="条件表达式（关键字/正则/JSONPath）")
    condition_field: str | None = Field(
        "message", description="检查的 payload 字段名，默认 message，支持点记法如 data.text"
    )
    action_type: str = Field(
        ...,
        description="动作类型: read_context | list_context | glance_context | glob_context | search_context",
    )
    action_params: dict[str, Any] | None = Field(None, description="动作参数，如 {'prefix': 'tools'}")
    priority: int = Field(0, description="优先级（数值越小优先级越高）")
    enabled: bool = Field(True, description="是否启用")


class TriggerUpdate(BaseModel):
    """Schema for updating an existing workspace trigger."""

    name: str | None = Field(None, min_length=1, max_length=255, description="触发器名称")
    description: str | None = Field(None, description="触发器描述")
    event_type: str | None = Field(None, min_length=1, max_length=100, description="监听的事件类型")
    condition_type: str | None = Field(None, description="条件类型")
    condition_value: str | None = Field(None, description="条件表达式")
    condition_field: str | None = Field(None, description="检查的 payload 字段名")
    action_type: str | None = Field(None, description="动作类型")
    action_params: dict[str, Any] | None = Field(None, description="动作参数")
    priority: int | None = Field(None, description="优先级")
    enabled: bool | None = Field(None, description="是否启用")


class TriggerResponse(ResponseMixin, BaseModel):
    """Schema for trigger API responses."""

    id: str
    workspace_id: str | None = None
    user_id: str | None = None
    name: str
    description: str | None = None
    event_type: str
    condition_type: str
    condition_value: str | None = None
    condition_field: str | None = None
    action_type: str
    action_params: dict[str, Any] | None = None
    priority: int
    enabled: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TriggerListResponse(BaseModel):
    """Paginated list of triggers."""

    total: int = Field(..., description="总数")
    items: list[TriggerResponse] = Field(..., description="触发器列表")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")


class TriggerTestRequest(BaseModel):
    """Schema for dry-run testing a trigger against a sample payload."""

    payload: dict[str, Any] = Field(..., description="模拟的事件 payload")


class TriggerTestResponse(BaseModel):
    """Schema for trigger test results."""

    matched: bool = Field(..., description="是否匹配条件")
    trigger_id: str = Field(..., description="触发器 ID")
    trigger_name: str = Field(..., description="触发器名称")
    action_type: str = Field(..., description="执行的动作类型")
    result: Any = Field(None, description="动作执行结果（仅 matched=True 时有值）")
    error: str | None = Field(None, description="执行错误信息")
