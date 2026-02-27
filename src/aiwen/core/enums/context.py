"""Context-related enum definitions."""

from enum import StrEnum


class ContextType(StrEnum):
    """Context type options."""

    CHUNK = "CHUNK"
    CONVERSATION = "conversation"
    MESSAGE = "message"
    SHORT_MEMORY = "short_memory"
    SKILL = "SKILL"
    TOOL = "tool"
    KNOWLEDGE = "knowledge"

class ContextPathSuffix:
    TOOLS = "tools"
    SKILLS = "skills"
    KNOWLEDGE = "knowledge"
    SHORT_MEMORY = "short_memory"
    LONG_MEMORY = "long_memory"

