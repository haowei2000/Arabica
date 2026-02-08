"""
User Tool Schemas

Pydantic models for user tool API requests and responses.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class UserToolBase(BaseModel):
    """Base schema for user tool"""

    name: str = Field(..., description="Tool name (unique per user)", min_length=1, max_length=100)
    display_name: str = Field(..., description="Display name", min_length=1, max_length=200)
    description: str = Field(..., description="Tool description", min_length=1)
    execution_mode: str = Field(
        default="server_run",
        description="Execution mode: server_run, http, client_run, container_run, celery_run",
    )
    input_schema: dict[str, Any] = Field(..., description="Input parameters schema (JSON Schema format)")
    output_schema: dict[str, Any] | None = Field(None, description="Output schema (optional)")
    category: str = Field(default="custom", description="Tool category")
    tags: list[str] | None = Field(None, description="Tool tags")
    version: str = Field(default="1.0.0", description="Tool version")
    timeout: int = Field(default=30, ge=1, le=3600, description="Execution timeout in seconds")
    enabled: bool = Field(default=True, description="Whether tool is enabled")
    is_public: bool = Field(default=False, description="Whether tool is public")


class UserToolCreate(UserToolBase):
    """Schema for creating a user tool"""

    workspace_id: UUID | None = Field(None, description="Workspace ID (optional)")

    # Execution mode specific configurations
    code: str | None = Field(None, description="Python code for server_run mode")
    http_config: dict[str, Any] | None = Field(None, description="HTTP configuration")
    container_config: dict[str, Any] | None = Field(None, description="Container configuration")
    client_config: dict[str, Any] | None = Field(None, description="Client configuration")
    celery_config: dict[str, Any] | None = Field(None, description="Celery configuration")


class UserToolUpdate(BaseModel):
    """Schema for updating a user tool"""

    display_name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1)
    execution_mode: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    code: str | None = None
    http_config: dict[str, Any] | None = None
    container_config: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    celery_config: dict[str, Any] | None = None
    category: str | None = None
    tags: list[str] | None = None
    version: str | None = None
    timeout: int | None = Field(None, ge=1, le=3600)
    enabled: bool | None = None
    is_public: bool | None = None


class UserToolResponse(UserToolBase):
    """Schema for user tool response"""

    id: UUID
    user_id: UUID
    workspace_id: UUID | None
    code: str | None
    http_config: dict[str, Any] | None
    container_config: dict[str, Any] | None
    client_config: dict[str, Any] | None
    celery_config: dict[str, Any] | None
    verified: bool
    usage_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime | None

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
