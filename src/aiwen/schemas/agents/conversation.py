"""Pydantic schemas for Conversation API endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """
    Schema for creating a new conversation.

    Note: Agent and App are merged into a single concept.
    app_id refers to the agent_id.
    """

    app_id: str | UUID = Field(..., description="Agent ID (app_id and agent_id are the same)")
    name: str = Field(..., min_length=1, max_length=255, description="Conversation name")
    status: str = Field(default="normal", description="Conversation status")
    summary: str | None = Field(None, description="Conversation summary")
    from_source: str = Field(..., description="Source of the conversation")
    from_account_id: str | UUID | None = Field(None, description="Account ID")
    from_end_user_id: str | UUID | None = Field(None, description="End user ID")


class ConversationUpdate(BaseModel):
    """Schema for updating an existing conversation."""

    name: str | None = Field(None, min_length=1, max_length=255, description="Conversation name")
    summary: str | None = Field(None, description="Conversation summary")
    status: str | None = Field(None, description="Conversation status")
    read_at: datetime | None = Field(None, description="Read timestamp")
    read_account_id: str | UUID | None = Field(None, description="Account that read the conversation")


class ConversationResponse(BaseModel):
    """Schema for conversation response."""

    id: str
    app_id: str
    name: str
    mode: str
    status: str
    dialogue_count: int
    summary: str | None = None
    from_source: str
    from_end_user_id: str | None = None
    from_account_id: str | None = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """Schema for paginated list of conversations."""

    total: int = Field(..., description="Total number of conversations")
    items: list[ConversationResponse] = Field(..., description="List of conversations")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
