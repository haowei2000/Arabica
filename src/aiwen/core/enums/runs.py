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
