"""Enums Layer - Foundation layer for all enum types.

This layer is at the bottom of the dependency hierarchy and contains
all enum definitions used across the application. It should not import
from any other application layers (models, schemas, services, etc.).

This design prevents circular import issues.
"""

from structure.core.enums.context import ContextType
from structure.core.enums.events import (
    ContextBatchLoadState,
    ContextBatchState,
    EventType,
)
from structure.core.enums.files import InputFormat, OutputFormat
from structure.core.enums.runs import RunStatus, TriggerType
from structure.core.enums.tools import AllowedToolType
from structure.core.enums.workspaces import (
    InvitationStatus,
    MemberRole,
    WorkspaceStatus,
    WorkspaceVisibility,
)

__all__ = [
    # Tools
    "AllowedToolType",
    # Events
    "ContextBatchLoadState",
    "ContextBatchState",
    # Context
    "ContextType",
    "EventType",
    # Files
    "InputFormat",
    # Workspaces
    "InvitationStatus",
    "MemberRole",
    "OutputFormat",
    # Runs
    "RunStatus",
    "TriggerType",
    "WorkspaceStatus",
    "WorkspaceVisibility",
]
