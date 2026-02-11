"""Context-related enum definitions."""

from enum import StrEnum


class ContextType(StrEnum):
    """Context type options."""

    CHUNK = "CHUNK"
    CONVERSATION = "conversation"
    MESSAGE = "message"
    USER_MEMORY = "user_memory"
    SKILL = "SKILL"
    TOOL = "tool"
    KNOWLEDGE = "knowledge"
