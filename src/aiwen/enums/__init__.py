"""Enums Layer - Foundation layer for all enum types.

This layer is at the bottom of the dependency hierarchy and contains
all enum definitions used across the application. It should not import
from any other application layers (models, schemas, services, etc.).

This design prevents circular import issues.
"""

from aiwen.enums.context import ContextType
from aiwen.enums.events import EventType
from aiwen.enums.files import InputFormat, OutputFormat
from aiwen.enums.runs import RunStatus, TriggerType
from aiwen.enums.tools import AllowedToolType, ToolExecutionMode
from aiwen.enums.workspaces import (
    InvitationStatus,
    MemberRole,
    WorkspaceStatus,
    WorkspaceVisibility,
)

__all__ = [
    # Context
    "ContextType",
    # Events
    "EventType",
    # Files
    "InputFormat",
    "OutputFormat",
    # Runs
    "RunStatus",
    "TriggerType",
    # Tools
    "AllowedToolType",
    "ToolExecutionMode",
    # Workspaces
    "InvitationStatus",
    "MemberRole",
    "WorkspaceStatus",
    "WorkspaceVisibility",
]
