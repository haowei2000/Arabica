# aiwen/services/workspaces/workspace_crud.py
"""CRUD operations for Workspace model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.workspace import Workspace
from aiwen.models.agents.workspace_member import WorkspaceMember


def normalize_uuid_to_str(val: str | UUID) -> str:
    """Normalize a UUID value to string.

    Args:
        val: UUID object or string representation

    Returns:
        String representation of the UUID
    """
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class WorkspaceCRUD:
    """CRUD operations for Workspace model."""

    def __init__(self, db_session: AsyncSession):
        """Initialize WorkspaceCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(
            self,
            owner_id: str | UUID,
            name: str,
            description: str | None = None,
            agent_template_id: str | UUID | None = None,
            visibility: str = "private",
            settings: dict[str, Any] | None = None,
            auto_commit: bool = False,
    ) -> Workspace:
        """Create a new workspace.

        Args:
            owner_id: ID of the workspace owner
            name: Workspace name
            description: Optional description
            agent_template_id: Default agent template ID (optional)
            visibility: Visibility setting (private/team/public)
            settings: Workspace configuration
            auto_commit: If True, immediately commit the transaction

        Returns:
            Created Workspace instance
        """
        workspace = Workspace(
            owner_id=normalize_uuid_to_str(owner_id),
            name=name,
            description=description,
            agent_template_id=normalize_uuid_to_str(agent_template_id) if agent_template_id else None,
            visibility=visibility,
            settings=settings,
        )
        self.db.add(workspace)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(workspace)

        # Create owner membership
        member = WorkspaceMember(
            workspace_id=str(workspace.id),
            user_id=normalize_uuid_to_str(owner_id),
            role="owner",
            invitation_status="accepted",
            joined_at=datetime.now(UTC),
        )
        self.db.add(member)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return workspace

    async def get_by_id(
            self,
            workspace_id: str | UUID,
    ) -> Workspace | None:
        """Get workspace by ID.

        Args:
            workspace_id: The workspace ID to search for

        Returns:
            Workspace instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(workspace_id)
        stmt = select(Workspace).where(
            and_(
                Workspace.id == normalized_id,
                Workspace.is_deleted == False,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_and_user(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
    ) -> Workspace | None:
        """Get workspace by ID with user access validation.

        Returns workspace if user is the owner or a member.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID

        Returns:
            Workspace instance or None if not found or no access
        """
        normalized_id = normalize_uuid_to_str(workspace_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Check if user is owner
        stmt = select(Workspace).where(
            and_(
                Workspace.id == normalized_id,
                Workspace.is_deleted == False,  # noqa: E712
                or_(
                    Workspace.owner_id == normalized_user_id,
                    # Or is a member
                    Workspace.id.in_(
                        select(WorkspaceMember.workspace_id).where(
                            and_(
                                WorkspaceMember.user_id == normalized_user_id,
                                WorkspaceMember.invitation_status == "accepted",
                            )
                        )
                    ),
                ),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(
            self,
            user_id: str | UUID,
            skip: int = 0,
            limit: int = 100,
            status: str | None = None,
    ) -> tuple[list[Workspace], int]:
        """List workspaces accessible to a user.

        Args:
            user_id: The user ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            status: Filter by status (optional)

        Returns:
            Tuple of (list of workspaces, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Base conditions: user is owner or member
        conditions = [
            Workspace.is_deleted == False,  # noqa: E712
            or_(
                Workspace.owner_id == normalized_user_id,
                Workspace.id.in_(
                    select(WorkspaceMember.workspace_id).where(
                        and_(
                            WorkspaceMember.user_id == normalized_user_id,
                            WorkspaceMember.invitation_status == "accepted",
                        )
                    )
                ),
            ),
        ]

        if status:
            conditions.append(Workspace.status == status)

        # Count query
        count_stmt = select(func.count(Workspace.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Workspace)
            .where(and_(*conditions))
            .order_by(Workspace.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            name: str | None = None,
            description: str | None = None,
            agent_template_id: str | UUID | None = None,
            visibility: str | None = None,
            is_shared: bool | None = None,
            settings: dict[str, Any] | None = None,
            status: str | None = None,
            auto_commit: bool = False,
    ) -> Workspace | None:
        """Update a workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID (must be owner or admin)
            name: New name (optional)
            description: New description (optional)
            agent_template_id: New default agent template ID (optional)
            visibility: New visibility (optional)
            is_shared: New sharing flag (optional)
            settings: New settings (optional)
            status: New status (optional)
            auto_commit: If True, immediately commit the transaction

        Returns:
            Updated Workspace instance or None if not found/unauthorized
        """
        workspace = await self.get_by_id_and_user(workspace_id, user_id)
        if not workspace:
            return None

        # Check if user has edit permission (owner or admin)
        can_edit = await self._can_edit(workspace_id, user_id)
        if not can_edit:
            return None

        if name is not None:
            workspace.name = name
        if description is not None:
            workspace.description = description
        if agent_template_id is not None:
            workspace.agent_template_id = normalize_uuid_to_str(agent_template_id) if agent_template_id else None
        if visibility is not None:
            workspace.visibility = visibility
        if is_shared is not None:
            workspace.is_shared = is_shared
        if settings is not None:
            workspace.settings = settings
        if status is not None:
            workspace.status = status

        workspace.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(workspace)
        return workspace

    async def delete(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
            auto_commit: bool = False,
    ) -> bool:
        """Soft delete a workspace (owner only).

        Args:
            workspace_id: The workspace ID
            user_id: The user ID (must be owner)
            auto_commit: If True, immediately commit the transaction

        Returns:
            True if deleted, False if not found or unauthorized
        """
        workspace = await self.get_by_id(workspace_id)
        if not workspace:
            return False

        # Only owner can delete
        normalized_user_id = normalize_uuid_to_str(user_id)
        if str(workspace.owner_id) != normalized_user_id:
            return False

        workspace.is_deleted = True
        workspace.status = "deleted"
        workspace.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    async def increment_run_count(
            self,
            workspace_id: str | UUID,
            auto_commit: bool = False,
    ) -> None:
        """Increment the run count for a workspace.

        Args:
            workspace_id: The workspace ID
            auto_commit: If True, immediately commit
        """
        workspace = await self.get_by_id(workspace_id)
        if workspace:
            workspace.run_count += 1
            workspace.updated_at = datetime.now(UTC)

            if auto_commit:
                await self.db.commit()
            else:
                await self.db.flush()

    async def _can_edit(
            self,
            workspace_id: str | UUID,
            user_id: str | UUID,
    ) -> bool:
        """Check if user can edit the workspace.

        Args:
            workspace_id: The workspace ID
            user_id: The user ID

        Returns:
            True if user can edit (owner or admin)
        """
        normalized_id = normalize_uuid_to_str(workspace_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Check if owner
        stmt = select(Workspace).where(
            and_(
                Workspace.id == normalized_id,
                Workspace.owner_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            return True

        # Check if admin member
        stmt = select(WorkspaceMember).where(
            and_(
                WorkspaceMember.workspace_id == normalized_id,
                WorkspaceMember.user_id == normalized_user_id,
                WorkspaceMember.role.in_(["owner", "admin"]),
                WorkspaceMember.invitation_status == "accepted",
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_legacy_conversation_id(
            self,
            conversation_id: str | UUID,
    ) -> Workspace | None:
        """Get workspace by legacy conversation ID (for migration).

        Args:
            conversation_id: The legacy conversation ID

        Returns:
            Workspace instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        stmt = select(Workspace).where(
            and_(
                Workspace.legacy_conversation_id == normalized_id,
                Workspace.is_deleted == False,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
