"""Pydantic schemas for App (Agent) API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aiwen.schemas.context.context_schema import ContextSchema
from aiwen.schemas.llm.chat_llm import ChatLLM


class AppCreate(BaseModel):
    """Schema for creating a new app (agent)."""

    app_code: str = Field(
        ...,
        description="Unique identifier for the app/agent",
        min_length=1,
        max_length=100,
    )
    executor_code: str | None = Field(
        "SimpleAgent", description="Code of the executor to use"
    )
    executor_id: UUID | None = Field(None, description="ID of the executor to use")
    user_id: UUID | None = Field(None, description="ID of the user creating the app")
    enabled: bool = Field(default=True, description="Whether the app is enabled")
    config: dict[str, Any] | None = Field(
        default=None, description="App configuration as JSON"
    )
    version: int = Field(default=1, ge=1, description="App version number")


class AppUpdate(BaseModel):
    """Schema for updating an existing app."""

    executor_id: UUID | None = Field(None, description="ID of the executor to use")
    enabled: bool | None = Field(None, description="Whether the app is enabled")
    config: dict[str, Any] | None = Field(None, description="App configuration as JSON")
    version: int | None = Field(None, ge=1, description="App version number")


class AppResponse(BaseModel):
    """Schema for app response."""

    id: UUID = Field(..., description="App UUID")
    app_code: str = Field(..., description="Unique app code")
    executor_id: UUID | None = Field(None, description="Executor ID")
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


class KnowledgeConfig(BaseModel):
    """Schema for knowledge configuration."""

    source: list[UUID] | None = Field(..., description="List of knowledge source IDs")


class SkillConfig(BaseModel):
    """Schema for SKILL configuration."""

    source: list[UUID] | None = Field(..., description="List of SKILL source IDs")


class ToolConfig(BaseModel):
    """Schema for tool configuration."""

    source: list[UUID] | None = Field(..., description="List of tool source IDs")


class AppConfig(BaseModel):
    """Schema for app configuration."""

    model: ChatLLM = Field(..., description="ChatLLM configuration")
    context: list[ContextSchema] | None = Field(
        None, description="List of context configurations"
    )
    approval_tools: list[str] = Field(
        default=["http_request"],
        description="Tool names that require user approval before execution (HITL)",
    )
    global_event: bool = Field(
        default=False,
        description=(
            "If True, load conversation history from all runs in the workspace "
            "instead of the current run only. Enables cross-run context awareness."
        ),
    )
