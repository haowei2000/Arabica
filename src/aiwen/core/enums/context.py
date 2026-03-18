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
    WORKSPACE = "workspace"
    TRIGGER = "trigger"
    RUN = "run"


class ContextPathSuffix:
    TOOLS = "tools"
    SKILLS = "skills"
    KNOWLEDGE = "knowledge"
    SHORT_MEMORY = "short_memory"
    LONG_MEMORY = "long_memory"
    TRIGGERS = "triggers"
    WORKSPACES = "workspaces"
    RUNS = "runs"

