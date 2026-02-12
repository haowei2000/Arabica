"""
User Tool Schemas

Pydantic models for user tool API requests and responses.

All user tools are ExternalTools — they delegate execution to a registered
InnerTool backend via the unified ``execute()`` / ``__call__()`` protocol.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class UserToolBase(BaseModel):
    """Base schema for the user tool (ExternalTool)"""

    name: str = Field(
        ..., description="Tool name (unique per user)", min_length=1, max_length=100
    )
    display_name: str = Field(
        ..., description="Display name", min_length=1, max_length=200
    )
    description: str = Field(..., description="Tool description", min_length=1)
    input_schema: dict[str, Any] = Field(
        ..., description="Input parameters schema (JSON Schema format)"
    )
    output_schema: dict[str, Any] | None = Field(
        None, description="Output schema (optional)"
    )
    category: str = Field(default="custom", description="Tool category")
    tags: list[str] | None = Field(None, description="Tool tags")
    version: int | str = Field(default=1, description="Tool version")
    timeout: int = Field(
        default=30, ge=1, le=3600, description="Execution timeout in seconds"
    )
    enabled: bool = Field(default=True, description="Whether tool is enabled")
    is_public: bool = Field(default=False, description="Whether tool is public")


class UserToolCreate(UserToolBase):
    """Schema for creating a user tool (ExternalTool)"""

    workspace_id: UUID | None = Field(None, description="Workspace ID (optional)")

    # InnerTool delegation
    inner_tool_name: str | None = Field(
        None,
        description="Name of the InnerTool to delegate to",
    )
    parameter_mapping: dict[str, str] | None = Field(
        None,
        description=(
            "Maps external param names to InnerTool param names. "
            "e.g. {'city': 'query', 'count': 'limit'}. "
            "If not set, all input fields are passed as a single 'input_data' dict"
        ),
    )

    # Tool-specific configurations
    code: str | None = Field(None, description="Python code for code execution tools")
    http_config: dict[str, Any] | None = Field(None, description="HTTP configuration")
    container_config: dict[str, Any] | None = Field(
        None, description="Container configuration"
    )
    client_config: dict[str, Any] | None = Field(
        None, description="Client configuration"
    )
    celery_config: dict[str, Any] | None = Field(
        None, description="Celery configuration"
    )


class UserToolUpdate(BaseModel):
    """Schema for updating a user tool (ExternalTool)"""

    display_name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1)
    inner_tool_name: str | None = Field(
        None, description="Name of the InnerTool to delegate to"
    )
    parameter_mapping: dict[str, str] | None = Field(
        None, description="Param name mapping"
    )
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    code: str | None = None
    http_config: dict[str, Any] | None = None
    container_config: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    celery_config: dict[str, Any] | None = None
    category: str | None = None
    tags: list[str] | None = None
    version: int | str | None = None
    timeout: int | None = Field(None, ge=1, le=3600)
    enabled: bool | None = None
    is_public: bool | None = None


class UserToolResponse(UserToolBase):
    """Schema for user tool response (both inner and external tools)"""

    id: UUID
    tool_code: str = Field(default="", description="Unique tool identifier code")
    user_id: UUID | None = Field(
        None, description="Tool owner ID (NULL for inner tools)"
    )
    workspace_id: UUID | None = None
    tool_type: str = Field(
        default="external", description="Tool type: inner or external"
    )
    inner_tool_name: str | None = Field(
        None,
        description="Name of the InnerTool this external tool delegates to",
    )
    parameter_mapping: dict[str, str] | None = Field(
        None,
        description="Maps external param names to InnerTool param names",
    )
    code: str | None = None
    http_config: dict[str, Any] | None = None
    container_config: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    celery_config: dict[str, Any] | None = None
    verified: bool = False
    usage_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class UserToolListResponse(BaseModel):
    """Schema for list of user tools"""

    tools: list[UserToolResponse]
    total: int


class UserToolExecutionRequest(BaseModel):
    """Schema for executing a user tool"""

    tool_id: UUID = Field(..., description="Tool ID to execute")
    parameters: dict[str, Any] = Field(..., description="Tool execution parameters")


class UserToolExecutionResponse(BaseModel):
    """Schema for tool execution result"""

    success: bool
    message: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None
    execution_time: float | None = None
