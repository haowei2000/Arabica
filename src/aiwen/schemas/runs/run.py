# aiwen/schemas/runs/run.py
"""Pydantic schemas for Run API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aiwen.core.enums.runs import RunStatus, TriggerType
from aiwen.utils.schema_mixins import ResponseMixin


class RunCreate(BaseModel):
    """Schema for creating a new run."""

    workspace_id: str | UUID = Field(..., description="工作空间ID")
    app_id: str | UUID | None = Field(None, description="应用ID")
    parent_run_id: str | UUID | None = Field(None, description="父运行ID")
    trigger_type: TriggerType = Field(TriggerType.USER, description="触发类型")
    input_data: dict[str, Any] | None = Field(None, description="初始输入数据")


class RunUpdate(BaseModel):
    """Schema for updating a run."""

    status: RunStatus | None = Field(None, description="状态")
    output_data: dict[str, Any] | None = Field(None, description="输出数据")
    error: str | None = Field(None, description="错误消息")
    error_code: str | None = Field(None, description="错误代码")
    waiting_for: dict[str, Any] | None = Field(None, description="等待信息")


class RunResponse(ResponseMixin, BaseModel):
    """Schema for run response."""

    id: str
    workspace_id: str
    app_id: str | None = None
    user_id: str
    parent_run_id: str | None = None
    status: RunStatus
    trigger_type: TriggerType
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    waiting_for: dict[str, Any] | None = None
    last_event_sequence: int
    legacy_task_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin


class RunListResponse(BaseModel):
    """Schema for paginated list of runs."""

    total: int = Field(..., description="运行总数")
    items: list[RunResponse] = Field(..., description="运行列表")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")


class RunStartRequest(BaseModel):
    """Schema for starting a new run with a user message."""

    message: str = Field(..., description="用户消息")
    app_id: str | UUID | None = Field(None, description="应用ID(可选,使用工作空间默认)")
    attachments: list[dict[str, Any]] | None = Field(None, description="附件")
    metadata: dict[str, Any] | None = Field(None, description="元数据")


class RunResumeRequest(BaseModel):
    """Schema for resuming a waiting run."""

    tool_result: dict[str, Any] | None = Field(None, description="工具执行结果")
    user_input: str | None = Field(None, description="用户输入")
    approval: bool | None = Field(None, description="审批结果")


class RunFeedbackRequest(BaseModel):
    """Schema for submitting user feedback / response to an agent.query."""

    feedback: str = Field(..., description="User's response text")
