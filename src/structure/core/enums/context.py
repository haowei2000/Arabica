"""Context-related enum definitions."""

from enum import StrEnum


class ContextScope(StrEnum):
    """Visibility scope of a Context entry."""

    USER = "user"  # visible only to the owning user
    WORKSPACE = "workspace"  # visible to all members of a workspace
    GLOBAL = "global"  # visible to all users (system-wide)


class ContextType(StrEnum):
    """Context type options."""

    CHUNK = "chunk"
    CONVERSATION = "conversation"
    MESSAGE = "message"
    SHORT_MEMORY = "short_memory"
    SKILL = "skill"
    TOOL = "tool"
    KNOWLEDGE = "knowledge"
    WORKSPACE = "workspace"
    TRIGGER = "trigger"
    RUN = "run"
    RUN_EVENTS = "run_events"
    EVENT_ARCHIVE = "event_archive"


class ContextPathSuffix:
    TOOLS = "tools"
    SKILLS = "skills"
    KNOWLEDGE = "knowledge"
    SHORT_MEMORY = "short_memory"
    LONG_MEMORY = "long_memory"
    TRIGGERS = "triggers"
    WORKSPACES = "workspaces"
    RUNS = "runs"
