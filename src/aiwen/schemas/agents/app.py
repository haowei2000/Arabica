"""Pydantic schemas for App (Agent) API endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AppCreate(BaseModel):
    """Schema for creating a new app (agent)."""

    app_code: str = Field(..., description="Unique identifier for the app/agent", min_length=1, max_length=100)
    agent_template_code: str | None = Field("DEFAULT001", description="Code of the agent template to use")
    agent_template_id: UUID | None = Field(None, description="ID of the agent template to use")
    user_id: UUID | None = Field(None, description="ID of the user creating the app")
    enabled: bool = Field(default=True, description="Whether the app is enabled")
    config: dict[str, Any] | None = Field(default=None, description="App configuration as JSON")
    version: int = Field(default=1, ge=1, description="App version number")


class AppUpdate(BaseModel):
    """Schema for updating an existing app."""

    agent_template_id: UUID | None = Field(None, description="ID of the agent template to use")
    enabled: bool | None = Field(None, description="Whether the app is enabled")
    config: dict[str, Any] | None = Field(None, description="App configuration as JSON")
    version: int | None = Field(None, ge=1, description="App version number")


class AppResponse(BaseModel):
    """Schema for app response."""

    id: UUID = Field(..., description="App UUID")
    app_code: str = Field(..., description="Unique app code")
    agent_template_id: UUID | None = Field(None, description="Agent template ID")
    user_id: UUID | None = Field(None, description="User ID who created the app")
    enabled: bool = Field(..., description="Whether the app is enabled")
    config: dict[str, Any] | None = Field(None, description="App configuration")
    version: int = Field(..., description="App version")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True


class AppListResponse(BaseModel):
    """Schema for paginated list of apps."""

    total: int = Field(..., description="Total number of apps")
    items: list[AppResponse] = Field(..., description="List of apps")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
