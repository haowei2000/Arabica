# aiwen/schemas/events/event_payloads.py
"""Event type definitions and payload schemas."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EventType(str, Enum):
    """Enumeration of all event types in the system."""

    # User events
    USER_MESSAGE = "user.message"
    USER_FEEDBACK = "user.feedback"

    # Agent events
    AGENT_TOKEN = "agent.token"
    AGENT_MESSAGE = "agent.message"
    AGENT_PLAN_STEP = "agent.plan.step"
    AGENT_THINKING = "agent.thinking"

    # Tool events
    TOOL_CALL = "tool.call"
    TOOL_PENDING = "tool.pending"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    TOOL_CLIENT_REQUEST = "tool.client.request"  # Request client-side execution

    # Run lifecycle events
    RUN_CREATED = "run.created"
    RUN_STATE_CHANGE = "run.state.change"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # Workspace events
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_MEMBER_JOIN = "workspace.member.join"
    WORKSPACE_MEMBER_LEAVE = "workspace.member.leave"
    WORKSPACE_MEMBER_ROLE_CHANGE = "workspace.member.role.change"

    # System events
    SYSTEM_ERROR = "system.error"
    SYSTEM_NOTIFICATION = "system.notification"


class BaseEventPayload(BaseModel):
    """Base class for all event payloads."""

    class Config:
        extra = "allow"


class UserMessagePayload(BaseEventPayload):
    """Payload for user.message events."""

    content: str = Field(..., description="用户消息内容")
    attachments: list[dict[str, Any]] | None = Field(None, description="附件列表")
    metadata: dict[str, Any] | None = Field(None, description="元数据")


class AgentTokenPayload(BaseEventPayload):
    """Payload for agent.token events (streaming tokens)."""

    token: str = Field(..., description="生成的token")
    token_index: int = Field(0, description="token索引")
    is_final: bool = Field(False, description="是否是最后一个token")


class AgentPlanStepPayload(BaseEventPayload):
    """Payload for agent.plan.step events."""

    step_number: int = Field(..., description="步骤编号")
    step_description: str = Field(..., description="步骤描述")
    status: str = Field("pending", description="步骤状态: pending/in_progress/completed/failed")
    output: str | None = Field(None, description="步骤输出")


class ToolCallPayload(BaseEventPayload):
    """Payload for tool.call events."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")


class ToolPendingPayload(BaseEventPayload):
    """Payload for tool.pending events (waiting for approval or external result)."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    reason: str = Field(..., description="等待原因")
    requires_approval: bool = Field(False, description="是否需要审批")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")


class ToolResultPayload(BaseEventPayload):
    """Payload for tool.result events."""

    tool_name: str = Field(..., description="工具名称")
    tool_id: str = Field(..., description="工具调用ID")
    result: Any = Field(..., description="工具执行结果")
    success: bool = Field(True, description="是否成功")
    error_message: str | None = Field(None, description="错误消息")
    execution_time_ms: int | None = Field(None, description="执行时间(毫秒)")


class RunStateChangePayload(BaseEventPayload):
    """Payload for run.state.change events."""

    previous_state: str = Field(..., description="之前的状态")
    new_state: str = Field(..., description="新状态")
    reason: str | None = Field(None, description="状态变更原因")
    triggered_by: str | None = Field(None, description="触发者")


class WorkspaceMemberJoinPayload(BaseEventPayload):
    """Payload for workspace.member.join events."""

    member_id: str = Field(..., description="成员用户ID")
    role: str = Field(..., description="成员角色")
    invited_by: str | None = Field(None, description="邀请人ID")


class EventCreate(BaseModel):
    """Schema for creating a new event."""

    event_type: EventType = Field(..., description="事件类型")
    workspace_id: str | UUID = Field(..., description="工作空间ID")
    run_id: str | UUID | None = Field(None, description="运行ID")
    user_id: str | UUID | None = Field(None, description="用户ID")
    payload: dict[str, Any] | None = Field(None, description="事件数据")
    parent_event_id: str | UUID | None = Field(None, description="父事件ID")


class EventResponse(BaseModel):
    """Schema for event response."""

    id: str
    event_type: str
    workspace_id: str
    run_id: str | None = None
    user_id: str | None = None
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
