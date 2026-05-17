# structure/schemas/events/event_payloads.py
"""Event type definitions and payload schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from structure.core.enums import EventType


class BaseEventSchema(BaseModel):
    """Base class for all event payloads."""

    model_config = ConfigDict(extra="allow")

    event_type: EventType = Field(..., description="事件类型")
    app_id: UUID | None = Field(None, description="应用ID")
    workspace_id: UUID | str | None = Field(None, description="工作空间ID")
    run_id: UUID | str | None = Field(None, description="运行ID")
    executor_code: str | None = Field(None, description="执行器代码")


class ChatFileAttachment(BaseModel):
    """Structured context reference for a file attached to a chat message."""

    id: str = Field(..., description="WorkspaceContext ID")
    name: str = Field(..., description="File name")
    path: str = Field(..., description="Structured context path")
    content_type: str | None = Field(None, description="MIME type")
    size_bytes: int | None = Field(None, description="File size in bytes")
    download_url: str | None = Field(None, description="Authenticated download URL")
    parse_status: str | None = Field(None, description="parsed/skipped/failed")


class UserMessage(BaseModel):
    """Payload for user.message events."""

    message: str = Field(..., description="用户消息")
    attachments: list[ChatFileAttachment] | None = Field(None, description="附件")

    model_config = {"extra": "allow"}


class UserMessageEventSchema(BaseEventSchema):
    """Payload for user.message events."""

    payload: UserMessage = Field(..., description="用户消息")
    user_id: UUID | None = Field(None, description="用户ID")
    metadata: dict[str, Any] | None = Field(None, description="元数据")

    # Override base fields to make them required or set defaults
    event_type: EventType = EventType.USER_MESSAGE
    app_id: UUID | None = Field(
        None, description="应用ID (optional, workspace executor used if absent)"
    )
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

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    workspace_id: str
    run_id: str | None = None
    user_id: str | None = None
    executor_code: str | None = None
    payload: dict[str, Any] | None = None
    sequence: int
    parent_event_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    is_archived: bool = False
    archived_at: datetime | None = None
    archive_scope: str | None = None
    archive_reason: str | None = None
    archive_context_id: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def convert_uuids(cls, data: Any) -> Any:
        """Convert UUIDs to strings."""
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():  # noqa: SIM118
                value = getattr(data, field_name, None)
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                elif field_name == "is_archived":
                    result[field_name] = bool(value)
                else:
                    result[field_name] = value
            return result
        return data


class EventListResponse(BaseModel):
    """Schema for paginated list of events."""

    total: int = Field(..., description="事件总数")
    items: list[EventResponse] = Field(..., description="事件列表")
    last_sequence: int | None = Field(None, description="最后一个事件的序列号")


class EventArchiveRequest(BaseModel):
    """Request body for archiving active event memory."""

    keep_last: int | None = Field(
        None,
        ge=0,
        le=10000,
        description=(
            "Backward-compatible shorthand for strategy_config.keep_last_floor. "
            "If omitted, the strategy chooses the scope default."
        ),
    )
    include_pinned: bool = Field(
        False,
        description="Whether to archive pinned durable events such as user/agent messages",
    )
    event_types: list[str] | None = Field(
        None,
        description="Optional event type allow-list. If omitted, all event types are eligible",
    )
    include_run_events: bool = Field(
        True,
        description="Workspace archive only: include events that belong to runs",
    )
    dry_run: bool = Field(
        False,
        description="Preview selected events without writing Context rows or marking events",
    )
    strategy: str = Field(
        "event_count_ttl",
        description="Registered event GC strategy name",
    )
    strategy_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific configuration such as TTL overrides and decay rules",
    )
    max_events_per_archive_context: int = Field(
        500,
        ge=1,
        le=10000,
        description="Maximum number of events written to one archive Context row",
    )
    max_chars_per_archive_context: int = Field(
        200_000,
        ge=1000,
        le=5_000_000,
        description="Approximate maximum archive Context content size before chunking",
    )
    bulk_update_chunk_size: int = Field(
        1000,
        ge=1,
        le=10000,
        description="Number of Event rows updated per bulk archive statement",
    )
    reason: str = Field(
        "manual_event_gc",
        max_length=255,
        description="Archive reason stored on each archived event",
    )


class EventArchiveResponse(BaseModel):
    """Archive operation result."""

    scope: str
    scope_id: str
    dry_run: bool = False
    archived_count: int
    skipped_count: int
    active_count_before: int
    archive_context_id: str | None = None
    archive_path: str | None = None
    reason: str
    strategy: str
    archive_context_ids: list[str] = Field(default_factory=list)
    archive_paths: list[str] = Field(default_factory=list)
    archive_chunks: int = 0
