"""Workspace-related enum definitions."""

from enum import Enum


class WorkspaceVisibility(str, Enum):
    """Workspace visibility options."""

    PRIVATE = "private"
    TEAM = "team"
    PUBLIC = "public"


class WorkspaceStatus(str, Enum):
    """Workspace status options."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemberRole(str, Enum):
    """Workspace member role options."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class InvitationStatus(str, Enum):
    """Invitation status options."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
