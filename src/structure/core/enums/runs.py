"""Run-related enum definitions."""

from enum import Enum, StrEnum


class TriggerType(str, Enum):
    """Run trigger type options."""

    USER = "user"
    TOOL_CALLBACK = "tool_callback"
    AGENT = "agent"
    SYSTEM = "system"


class RunStatus(StrEnum):
    """Run status values."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStatus(StrEnum):
    """Task status values in the agent loop."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactType(StrEnum):
    """Artifact type values — categories of agent-produced outputs."""

    TEXT = "text"
    CODE = "code"
    FILE = "file"
    IMAGE = "image"
    DOCUMENT = "document"
    DATA = "data"
    OTHER = "other"
