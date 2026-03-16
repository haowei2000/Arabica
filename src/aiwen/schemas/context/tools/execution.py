"""Pydantic schemas for tool execution system."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aiwen.schemas.events.event_payloads import BaseEventSchema


class ReadContextResult(BaseModel):
    """Data payload returned by the ``read_context`` tool.

    Represents the ``data`` dict inside a TOOL_RESULT event payload when
    ``tool_name == "read_context"``.  Used by
    ``DefaultExecutor._extract_context_tool_schemas`` to parse tool schemas
    stored at ``tools/*`` workspace context paths.
    """

    path: str = Field("", description="Workspace context path, e.g. 'tools/my_tool'")
    content: str | None = Field(None, description="Raw JSON string of the stored schema")
    summary: str | None = Field(None, description="Human-readable description of the context entry")
    glance: str | None = Field(None, description="Short one-line summary")

    model_config = {"extra": "allow"}


class ExecutionContext(BaseModel):
    """ContextSchema information for tool execution."""

    run_id: UUID = Field(..., description="Run ID")
    workspace_id: UUID = Field(..., description="Workspace ID")
    user_id: UUID | None = Field(None, description="User ID")
    conversation_id: UUID | None = Field(None, description="Conversation ID")

    # Execution environment info
    correlation_id: str | None = Field(None, description="Correlation ID for tracing")
    timeout_override: int | None = Field(None, description="Override default timeout")


class SandboxConfig(BaseModel):
    """Configuration for sandbox execution."""

    image: str = Field("python:3.12-slim", description="Docker image")
    memory: str = Field("256m", description="Memory limit")
    cpu_percent: int = Field(50, description="CPU usage limit")
    network_enabled: bool = Field(False, description="Allow network access")
    timeout_seconds: int = Field(60, description="Execution timeout")
    workdir: str = Field("/workspace", description="Working directory in container")

    # Volume mounts (host_path: container_path)
    volumes: dict[str, str] = Field(default_factory=dict, description="Volume mounts")

    # Environment variables
    env: dict[str, str] = Field(
        default_factory=dict, description="Environment variables"
    )


class ToolExecutionRequest(BaseModel):
    """Request to execute a tool."""

    tool_name: str = Field(..., description="Name of the tool to execute")
    tool_id: str = Field(..., description="Unique ID for this tool invocation")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments"
    )
    context: ExecutionContext = Field(..., description="Execution context")

    # Optional overrides
    sandbox_config: SandboxConfig | None = Field(
        None, description="Sandbox config override"
    )
    timeout_seconds: int | None = Field(None, description="Timeout override")


class ToolResult(BaseModel):
    """Result from tool execution."""

    tool_name: str = Field(..., description="Tool name")
    tool_id: str = Field(..., description="Tool invocation ID")
    success: bool = Field(..., description="Whether execution succeeded")
    result: Any = Field(None, description="Execution result")
    error_message: str | None = Field(None, description="Error message if failed")
    execution_time_ms: int = Field(..., description="Execution time in milliseconds")

    # Execution metadata
    sandbox_container_id: str | None = Field(
        None, description="Container ID for sandbox"
    )
    stdout: str | None = Field(None, description="Standard output (sandbox only)")
    stderr: str | None = Field(None, description="Standard error (sandbox only)")


class ToolResultSubmission(BaseModel):
    """Client submission of tool execution result."""

    tool_id: str = Field(..., description="Tool invocation ID")
    success: bool = Field(..., description="Whether execution succeeded")
    result: Any = Field(None, description="Execution result")
    error_message: str | None = Field(None, description="Error message if failed")

    # Client metadata
    client_execution_time_ms: int | None = Field(
        None, description="Client-side execution time"
    )
    client_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class ToolClientRequestPayload(BaseEventSchema):
    """Payload for tool.client.request events.

    This event is sent to the client (browser) when a tool needs to be
    executed in the client environment (e.g., file picker, camera capture).
    """

    tool_name: str = Field(..., description="Tool name")
    tool_id: str = Field(..., description="Unique tool invocation ID")
    handler: str = Field(..., description="Frontend handler to invoke")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments"
    )
    timeout_seconds: int = Field(120, description="Time client has to respond")
    config: dict[str, Any] = Field(
        default_factory=dict, description="Handler configuration"
    )


class PendingToolExecution(BaseModel):
    """Tracks a pending client-side tool execution."""

    tool_id: str = Field(..., description="Tool invocation ID")
    tool_name: str = Field(..., description="Tool name")
    run_id: UUID = Field(..., description="Run ID")
    workspace_id: UUID = Field(..., description="Workspace ID")

    # Request details
    handler: str = Field(..., description="Frontend handler")
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(120)

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(..., description="When this request expires")

    # State
    completed: bool = Field(False)
    result: ToolResult | None = Field(None)
