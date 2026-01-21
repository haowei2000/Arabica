"""Pydantic schemas for Conversation API endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional, Union, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

if TYPE_CHECKING:
    from aiwen.schemas.agents.message import MessageResponse


class ConversationCreate(BaseModel):
    """
    Schema for creating a new conversation.

    Note: Agent and App are merged into a single concept.
    app_id refers to the agent_id.
    """

    id: str | UUID | None = Field(None, description="Optional specific UUID for the conversation")
    app_id: str | UUID = Field(..., description="Agent ID (app_id and agent_id are the same)")
    name: str = Field(..., min_length=1, max_length=255, description="Conversation name")
    mode: str = Field(default="chat", description="Conversation mode (chat or completion)")
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
    mode: str = Field(default="chat")
    status: str
    dialogue_count: int
    summary: str | None = None
    from_source: str
    from_end_user_id: str | None = None
    from_account_id: str | None = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    @model_validator(mode='before')
    @classmethod
    def convert_uuids_and_map_fields(cls, data: Any) -> Any:
        """Convert UUIDs to strings and map field names."""
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():
                # Try to get value from object
                value = getattr(data, field_name, None)

                # Handle special mappings
                if field_name == "from_account_id" and value is None:
                    value = getattr(data, "account_id", None)

                # Handle missing mode field (backwards compatibility)
                if field_name == "mode" and value is None:
                    value = "chat"

                # Convert UUIDs to strings
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value

            return result
        return data

    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    """Schema for conversation detail with messages."""

    messages: list[dict[str, Any]] = Field(default_factory=list, description="List of messages in the conversation")

    @model_validator(mode='before')
    @classmethod
    def convert_messages(cls, data: Any) -> Any:
        """Convert input objects to dicts."""
        # First call parent validator
        result = ConversationResponse.convert_uuids_and_map_fields(data)

        # Convert messages if present
        if hasattr(data, "messages"):
            messages = []
            for msg in data.messages:
                msg_dict = {
                    "id": str(msg.id) if hasattr(msg, 'id') else None,
                    "role": "assistant" if hasattr(msg, 'answer') and msg.answer else "user",
                    "content": msg.answer if hasattr(msg, 'answer') and msg.answer else (msg.query if hasattr(msg, 'query') else ""),
                    "query": msg.query if hasattr(msg, 'query') else "",
                    "answer": msg.answer if hasattr(msg, 'answer') else "",
                    "created_at": msg.created_at.isoformat() if hasattr(msg, 'created_at') else None,
                }
                messages.append(msg_dict)
            result["messages"] = messages
        else:
            result["messages"] = []

        return result

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """Schema for paginated list of conversations."""

    total: int = Field(..., description="Total number of conversations")
    items: list[ConversationResponse] = Field(..., description="List of conversations")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
