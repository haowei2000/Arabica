"""Pydantic schemas for Tool registry API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ToolCreate(BaseModel):
    """Schema for creating a new tool."""

    name: str = Field(
        ..., description="Tool display name", min_length=1, max_length=100
    )
    tool_code: str = Field(
        ..., description="Unique tool identifier code", min_length=1, max_length=100
    )
    description: str | None = Field(None, description="Tool description")
    tool_type: str = Field(
        "server",
        description="Execution type: server, sandbox, client, async",
    )
    input_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for tool input parameters"
    )
    config: dict[str, Any] | None = Field(
        None, description="Tool execution configuration"
    )
    enabled: bool = Field(default=True, description="Whether the tool is enabled")
    is_public: bool = Field(
        default=False, description="Whether the tool is publicly available"
    )
    version: int = Field(default=1, ge=1, description="Tool version number")


class ToolUpdate(BaseModel):
    """Schema for updating an existing tool."""

    name: str | None = Field(None, description="Tool display name", max_length=100)
    description: str | None = Field(None, description="Tool description")
    tool_type: str | None = Field(None, description="Execution type")
    input_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for tool input parameters"
    )
    config: dict[str, Any] | None = Field(
        None, description="Tool execution configuration"
    )
    enabled: bool | None = Field(None, description="Whether the tool is enabled")
    is_public: bool | None = Field(None, description="Whether publicly available")
    version: int | None = Field(None, ge=1, description="Tool version number")


class ToolResponse(BaseModel):
    """Schema for tool response."""

    id: UUID = Field(..., description="Tool UUID")
    name: str = Field(..., description="Tool display name")
    tool_code: str = Field(..., description="Unique tool code")
    description: str | None = Field(None, description="Tool description")
    tool_type: str = Field(..., description="Execution type")
    input_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for input parameters"
    )
    config: dict[str, Any] | None = Field(
        None, description="Tool execution configuration"
    )
    user_id: UUID | None = Field(None, description="Creator user ID")
    enabled: bool = Field(..., description="Whether the tool is enabled")
    is_public: bool = Field(..., description="Whether publicly available")
    version: int = Field(..., description="Tool version")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True


class ToolListResponse(BaseModel):
    """Schema for paginated list of tools."""

    total: int = Field(..., description="Total number of tools")
    items: list[ToolResponse] = Field(..., description="List of tools")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
