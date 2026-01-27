# aiwen/services/workspaces/member_crud.py
"""CRUD operations for WorkspaceMember model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.workspace import Workspace
from aiwen.models.agents.workspace_member import WorkspaceMember


def normalize_uuid_to_str(val: str | UUID) -> str:
    """Normalize a UUID value to string."""
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class WorkspaceMemberCRUD:
    """CRUD operations for WorkspaceMember model."""

    def __init__(self, db_session: AsyncSession):
        """Initialize WorkspaceMemberCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def add_member(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            role: str = "viewer",
            invited_by: str | UUID | None = None,
            invitation_status: str = "pending",
            auto_commit: bool = False,
    ) -> WorkspaceMember | None:
        """Add a member to a workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID to add
            role: Member role (owner/admin/editor/viewer)
            invited_by: ID of user who invited
            invitation_status: Initial invitation status
            auto_commit: If True, immediately commit

        Returns:
            Created WorkspaceMember instance or None if already exists
        """
        normalized_workspace_id = normalize_uuid_to_str(workspace_id)
        normalized_user_id = normalize_uuid_to_str(user_id)
        normalized_invited_by = normalize_uuid_to_str(invited_by) if invited_by else None

        # Check if member already exists
        existing = await self.get_member(workspace_id, user_id)
        if existing:
            return None

        member = WorkspaceMember(
            workspace_id=normalized_workspace_id,
            user_id=normalized_user_id,
            role=role,
            invited_by=normalized_invited_by,
            invitation_status=invitation_status,
            joined_at=datetime.now(UTC) if invitation_status == "accepted" else None,
        )
        self.db.add(member)

        # Update workspace member count
        await self._update_member_count(normalized_workspace_id)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(member)
        return member

    async def get_member(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
    ) -> WorkspaceMember | None:
        """Get a workspace member.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID

        Returns:
            WorkspaceMember instance or None if not found
        """
        normalized_workspace_id = normalize_uuid_to_str(workspace_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        stmt = select(WorkspaceMember).where(
            and_(
                WorkspaceMember.workspace_id == normalized_workspace_id,
                WorkspaceMember.user_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(
            self,
            workspace_id: str | UUID,
            skip: int = 0,
            limit: int = 100,
            status: str | None = None,
    ) -> tuple[list[WorkspaceMember], int]:
        """List members of a workspace.

        Args:
            workspace_id: The workspace ID
            skip: Number of records to skip
            limit: Maximum number of records
            status: Filter by invitation status (optional)

        Returns:
            Tuple of (list of members, total count)
        """
        normalized_workspace_id = normalize_uuid_to_str(workspace_id)

        conditions = [WorkspaceMember.workspace_id == normalized_workspace_id]
        if status:
            conditions.append(WorkspaceMember.invitation_status == status)

        # Count query
        count_stmt = select(func.count(WorkspaceMember.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(WorkspaceMember)
            .where(and_(*conditions))
            .order_by(WorkspaceMember.created_at)
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update_role(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            new_role: str,
            auto_commit: bool = False,
    ) -> WorkspaceMember | None:
        """Update a member's role.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID
            new_role: New role
            auto_commit: If True, immediately commit

        Returns:
            Updated WorkspaceMember or None if not found
        """
        member = await self.get_member(workspace_id, user_id)
        if not member:
            return None

        # Cannot change owner role
        if member.role == "owner":
            return None

        member.role = new_role
        member.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(member)
        return member

    async def accept_invitation(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            auto_commit: bool = False,
    ) -> WorkspaceMember | None:
        """Accept a workspace invitation.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID
            auto_commit: If True, immediately commit

        Returns:
            Updated WorkspaceMember or None if not found
        """
        member = await self.get_member(workspace_id, user_id)
        if not member:
            return None

        if member.invitation_status != "pending":
            return member

        member.invitation_status = "accepted"
        member.joined_at = datetime.now(UTC)
        member.updated_at = datetime.now(UTC)

        # Update workspace member count
        await self._update_member_count(str(member.workspace_id))

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(member)
        return member

    async def decline_invitation(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            auto_commit: bool = False,
    ) -> WorkspaceMember | None:
        """Decline a workspace invitation.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID
            auto_commit: If True, immediately commit

        Returns:
            Updated WorkspaceMember or None if not found
        """
        member = await self.get_member(workspace_id, user_id)
        if not member:
            return None

        member.invitation_status = "declined"
        member.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(member)
        return member

    async def remove_member(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            auto_commit: bool = False,
    ) -> bool:
        """Remove a member from a workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID to remove
            auto_commit: If True, immediately commit

        Returns:
            True if removed, False if not found or cannot remove
        """
        member = await self.get_member(workspace_id, user_id)
        if not member:
            return False

        # Cannot remove owner
        if member.role == "owner":
            return False

        await self.db.delete(member)

        # Update workspace member count
        await self._update_member_count(str(member.workspace_id))

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    async def get_user_role(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
    ) -> str | None:
        """Get a user's role in a workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID

        Returns:
            Role string or None if not a member
        """
        member = await self.get_member(workspace_id, user_id)
        if member and member.invitation_status == "accepted":
            return member.role
        return None

    async def is_member(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
    ) -> bool:
        """Check if a user is a member of a workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID

        Returns:
            True if user is an accepted member
        """
        member = await self.get_member(workspace_id, user_id)
        return member is not None and member.invitation_status == "accepted"

    async def _update_member_count(
            self,
            workspace_id: str,
    ) -> None:
        """Update the workspace's member count.

        Args:
            workspace_id: The workspace ID
        """
        # Count accepted members
        count_stmt = select(func.count(WorkspaceMember.id)).where(
            and_(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.invitation_status == "accepted",
            )
        )
        count_result = await self.db.execute(count_stmt)
        count = count_result.scalar() or 0

        # Update workspace
        stmt = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if workspace:
            workspace.member_count = count
            workspace.updated_at = datetime.now(UTC)
