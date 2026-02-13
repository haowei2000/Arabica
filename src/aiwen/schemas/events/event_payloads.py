# aiwen/schemas/events/event_payloads.py
"""Event type definitions and payload schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from aiwen.enums.events import EventType


class BaseEventSchema(BaseModel):
    """Base class for all event payloads."""

    event_type: EventType = Field(..., description="事件类型")
    app_id: UUID | None = Field(None, description="应用ID")
    workspace_id: UUID | str | None = Field(None, description="工作空间ID")
    run_id: UUID | str | None = Field(None, description="运行ID")
    executor_code: str | None = Field(None, description="执行器代码")

    class Config:
        extra = "allow"


class UserMessage(BaseModel):
    """Payload for user.message events."""

    message: str = Field(..., description="用户消息")


class UserMessageEventSchema(BaseEventSchema):
    """Payload for user.message events."""

    payload: UserMessage = Field(..., description="用户消息")
    user_id: UUID | None = Field(None, description="用户ID")
    metadata: dict[str, Any] | None = Field(None, description="元数据")

    # Override base fields to make them required or set defaults
    event_type: EventType = EventType.USER_MESSAGE
    app_id: UUID = Field(..., description="应用ID")
    workspace_id: UUID | str = Field(..., description="工作空间ID")


class AgentTokenEventSchema(BaseEventSchema):
    """Payload for agent.token events (streaming tokens)."""

    token: str = Field(..., description="生成的token")
    token_index: int = Field(0, description="token索引")
    is_final: bool = Field(False, description="是否是最后一个token")

    # Override base field to set default
    event_type: EventType = EventType.AGENT_TOKEN


class AgentPlanEventSchema(BaseEventSchema):
    """Payload for agent.plan.step events."""

    step_number: int = Field(..., description="步骤编号")
    step_description: str = Field(..., description="步骤描述")
    status: str = Field(
        "pending", description="步骤状态: pending/in_progress/completed/failed"
    )
    output: str | None = Field(None, description="步骤输出")

    # Override base field to set default
    event_type: EventType = EventType.AGENT_PLAN_STEP


class ToolCallEventSchema(BaseEventSchema):
    """Payload for tool.call events."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")

    # Override base field to set default
    event_type: EventType = EventType.TOOL_CALL


class ToolPendingEventSchema(BaseEventSchema):
    """Payload for tool.pending events (waiting for approval or external result)."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    reason: str = Field(..., description="等待原因")
    requires_approval: bool = Field(False, description="是否需要审批")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")

    # Override base field to set default
    event_type: EventType = EventType.TOOL_PENDING


class ToolResultEventSchema(BaseEventSchema):
    """Payload for tool.result events."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    result: Any = Field(..., description="工具执行结果")
    success: bool = Field(True, description="是否成功")
    error_message: str | None = Field(None, description="错误消息")
    execution_time_ms: int | None = Field(None, description="执行时间(毫秒)")

    # Override base field to set default
    event_type: EventType = EventType.TOOL_RESULT


class RunStateChangeEventSchema(BaseEventSchema):
    """Payload for run.state.change events."""

    previous_state: str = Field(..., description="之前的状态")
    new_state: str = Field(..., description="新状态")
    reason: str | None = Field(None, description="状态变更原因")
    triggered_by: str | None = Field(None, description="触发者")

    # Override base field to set default
    event_type: EventType = EventType.RUN_STATE_CHANGE


class WorkspaceMemberJoinEventSchema(BaseEventSchema):
    """Payload for workspace.member.join events."""

    member_id: str = Field(..., description="成员用户ID")
    role: str = Field(..., description="成员角色")
    invited_by: str | None = Field(None, description="邀请人ID")

    # Override base field to set default
    event_type: EventType = EventType.WORKSPACE_MEMBER_JOIN


class EventCreate(BaseModel):
    """Schema for creating a new event."""

    event_type: EventType = Field(..., description="事件类型")
    workspace_id: str | UUID = Field(..., description="工作空间ID")
    run_id: str | UUID | None = Field(None, description="运行ID")
    user_id: str | UUID | None = Field(None, description="用户ID")
    executor_code: str | None = Field(None, description="执行器模板代码")
    payload: dict[str, Any] | None = Field(None, description="事件数据")
    parent_event_id: str | UUID | None = Field(None, description="父事件ID")


class EventResponse(BaseModel):
    """Schema for event response."""

    id: str
    event_type: str
    workspace_id: str
    run_id: str | None = None
    user_id: str | None = None
    executor_code: str | None = None
    payload: dict[str, Any] | None = None
    sequence: int
    parent_event_id: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def convert_uuids(cls, data: Any) -> Any:
        """Convert UUIDs to strings."""
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():
                value = getattr(data, field_name, None)
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value
            return result
        return data

    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    """Schema for paginated list of events."""

    total: int = Field(..., description="事件总数")
    items: list[EventResponse] = Field(..., description="事件列表")
    last_sequence: int | None = Field(None, description="最后一个事件的序列号")
