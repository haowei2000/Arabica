# structure/models/agent/workspace_member.py
"""WorkspaceMember model for workspace collaboration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.workspaces.workspace import Workspace

from structure.core.enums.workspaces import InvitationStatus, MemberRole

Base = get_base("structure")


class WorkspaceMember(Base):
    """WorkspaceMember model - represents a user's membership in a workspace.

    Tracks user roles, invitation status, and membership metadata.
    """

    __tablename__ = "workspace_member"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="成员记录唯一标识",
    )

    # Workspace reference
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="工作空间ID",
    )

    # Member user
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="成员用户ID"
    )

    # Role in workspace
    role: Mapped[MemberRole] = mapped_column(
        String(50), default=MemberRole.VIEWER, comment="角色: owner/admin/editor/viewer"
    )

    # Invitation details
    invited_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="邀请人ID"
    )
    invitation_status: Mapped[InvitationStatus] = mapped_column(
        String(50), default=InvitationStatus.ACCEPTED, comment="邀请状态: pending/accepted/declined"
    )

    # Timestamps
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="加入时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="更新时间",
    )

    # Relationship
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="members")

    __table_args__ = (
        # Unique constraint: one user can only be a member once per workspace
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        # Index for user's memberships
        Index("ix_workspace_member_user", "user_id"),
        # Index for workspace members
        Index("ix_workspace_member_workspace", "workspace_id", "role"),
        # Index for invitation queries
        Index("ix_workspace_member_invitation", "invitation_status"),
    )

    def __repr__(self) -> str:
        return f"<WorkspaceMember(id={self.id}, workspace_id='{self.workspace_id}', user_id='{self.user_id}', role='{self.role}')>"
