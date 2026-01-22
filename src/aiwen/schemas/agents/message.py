"""Pydantic schemas for Message API endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    """
    Schema for creating a new input.

    Note: Agent and App are merged into a single concept.
    app_id refers to the agent_id.
    """

    app_id: str | UUID | None = Field(default=None, description="Agent ID (app_id and agent_id are the same)")
    conversation_id: str | UUID = Field(..., description="Conversation ID")
    query: str = Field(default='', description="User query/input")
    message: list[dict[str, Any]] = Field(..., description="Message content as JSON")
    answer: str = Field(default="", description="Model answer/response")
    status: str = Field(default="normal", description="Message status")
    from_source: str = Field(default='system', description="Source of the input")
    from_end_user_id: str | UUID | None = Field(default=None, description="End user ID")
    from_account_id: str | UUID | None = Field(default=None, description="Account ID")


class MessageUpdate(BaseModel):
    """Schema for updating an existing input."""

    answer: str | None = Field(None, description="Model answer/response")
    status: str | None = Field(None, description="Message status")
    error: str | None = Field(None, description="Error input if any")
    answer_tokens: int | None = Field(None, description="Number of tokens in answer")
    total_price: Decimal | None = Field(None, description="Total price for the input")


class MessageContent(BaseModel):
    human: str | None
    assistant: str | None
    system: str | None


class MessageResponse(BaseModel):
    """Schema for input response."""

    id: str
    app_id: str
    conversation_id: str
    query: str
    answer: str
    message: list[dict[str, Any]]
    status: str
    message_tokens: int
    answer_tokens: int
    from_source: str
    from_end_user_id: str | None = None
    from_account_id: str | None = None
    model_provider: str | None = None
    model_id: str | None = None
    currency: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageListResponse(BaseModel):
    """Schema for paginated list of messages."""

    total: int = Field(..., description="Total number of messages")
    items: list[MessageResponse] = Field(..., description="List of messages")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
