"""Conversation domain schemas."""

from aiwen.schemas.conversations.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
)
from aiwen.schemas.conversations.message import (
    MessageContent,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    MessageUpdate,
    Role,
)

__all__ = [
    "ConversationCreate",
    "ConversationDetailResponse",
    "ConversationListResponse",
    "ConversationResponse",
    "ConversationUpdate",
    "MessageContent",
    "MessageCreate",
    "MessageListResponse",
    "MessageResponse",
    "MessageUpdate",
    "Role",
]
