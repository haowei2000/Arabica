"""CRUD operations for Friend model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.auth.friend import Friend
from aiwen.models.auth.user import User


class FriendCRUD:
    """CRUD operations for Friend model."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def send_request(
        self,
        user_id: str | UUID,
        friend_id: str | UUID,
        auto_commit: bool = False,
    ) -> Friend | None:
        """Send a friend request from user_id to friend_id.

        Returns None if a request already exists in either direction.
        """
        user_id_str = str(user_id)
        friend_id_str = str(friend_id)

        if user_id_str == friend_id_str:
            raise ValueError("Cannot send friend request to yourself")

        # Check for existing relationship in either direction
        existing_stmt = select(Friend).where(
            or_(
                and_(
                    Friend.user_id == user_id_str,
                    Friend.friend_id == friend_id_str,
                ),
                and_(
                    Friend.user_id == friend_id_str,
                    Friend.friend_id == user_id_str,
                ),
            )
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            return None

        friend = Friend(
            user_id=user_id_str,
            friend_id=friend_id_str,
            status="pending",
        )
        self.db.add(friend)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(friend)
        else:
            await self.db.flush()
        return friend

    async def get_by_id(self, friend_record_id: str | UUID) -> Friend | None:
        """Get a friendship record by its ID."""
        stmt = select(Friend).where(Friend.id == str(friend_record_id))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_relationship(
        self, user_id: str | UUID, friend_id: str | UUID
    ) -> Friend | None:
        """Get the friendship record between two users (in either direction)."""
        user_id_str = str(user_id)
        friend_id_str = str(friend_id)
        stmt = select(Friend).where(
            or_(
                and_(
                    Friend.user_id == user_id_str,
                    Friend.friend_id == friend_id_str,
                ),
                and_(
                    Friend.user_id == friend_id_str,
                    Friend.friend_id == user_id_str,
                ),
            )
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def accept_request(
        self,
        friend_record_id: str | UUID,
        current_user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Friend | None:
        """Accept a pending friend request.

        Only the recipient (friend_id) can accept the request.
        """
        record = await self.get_by_id(friend_record_id)
        if not record:
            return None
        if str(record.friend_id) != str(current_user_id):
            raise PermissionError("Only the request recipient can accept it")
        if record.status != "pending":
            raise ValueError(f"Cannot accept a request with status '{record.status}'")

        record.status = "accepted"
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(record)
        else:
            await self.db.flush()
        return record

    async def decline_request(
        self,
        friend_record_id: str | UUID,
        current_user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Friend | None:
        """Decline a pending friend request.

        Only the recipient (friend_id) can decline.
        """
        record = await self.get_by_id(friend_record_id)
        if not record:
            return None
        if str(record.friend_id) != str(current_user_id):
            raise PermissionError("Only the request recipient can decline it")
        if record.status != "pending":
            raise ValueError(f"Cannot decline a request with status '{record.status}'")

        record.status = "declined"
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(record)
        else:
            await self.db.flush()
        return record

    async def remove_friend(
        self,
        friend_record_id: str | UUID,
        current_user_id: str | UUID,
        auto_commit: bool = False,
    ) -> bool:
        """Remove an accepted friendship. Either party can remove."""
        record = await self.get_by_id(friend_record_id)
        if not record:
            return False
        current_user_id_str = str(current_user_id)
        if str(record.user_id) != current_user_id_str and str(record.friend_id) != current_user_id_str:
            raise PermissionError("You are not part of this friendship")

        await self.db.delete(record)
        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return True

    async def list_friends(
        self,
        user_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """List accepted friends for a user with their user info.

        Returns (total, list of dicts with friend info).
        """
        user_id_str = str(user_id)

        # Build base condition: accepted friendships where current user is either party
        condition = and_(
            Friend.status == "accepted",
            or_(
                Friend.user_id == user_id_str,
                Friend.friend_id == user_id_str,
            ),
        )

        # Count total
        count_stmt = select(func.count()).select_from(Friend).where(condition)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # Fetch paginated records
        stmt = (
            select(Friend)
            .where(condition)
            .order_by(Friend.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        records = (await self.db.execute(stmt)).scalars().all()

        # For each record, resolve the other user's info
        items = []
        for record in records:
            other_user_id = (
                str(record.friend_id)
                if str(record.user_id) == user_id_str
                else str(record.user_id)
            )
            user_stmt = select(User).where(User.id == other_user_id)
            user = (await self.db.execute(user_stmt)).scalar_one_or_none()
            if user:
                items.append({
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "friendship_id": record.id,
                    "status": record.status,
                    "created_at": record.created_at,
                })

        return total, items

    async def list_pending_requests(
        self,
        user_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """List incoming pending friend requests for a user.

        Returns (total, list of dicts with sender info).
        """
        user_id_str = str(user_id)

        condition = and_(
            Friend.friend_id == user_id_str,
            Friend.status == "pending",
        )

        count_stmt = select(func.count()).select_from(Friend).where(condition)
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(Friend)
            .where(condition)
            .order_by(Friend.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        records = (await self.db.execute(stmt)).scalars().all()

        items = []
        for record in records:
            user_stmt = select(User).where(User.id == str(record.user_id))
            user = (await self.db.execute(user_stmt)).scalar_one_or_none()
            if user:
                items.append({
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "friendship_id": record.id,
                    "status": record.status,
                    "created_at": record.created_at,
                })

        return total, items

    async def list_accepted_friend_ids(self, user_id: str | UUID) -> list[str]:
        """Return list of user IDs who are accepted friends of the given user."""
        user_id_str = str(user_id)
        condition = and_(
            Friend.status == "accepted",
            or_(
                Friend.user_id == user_id_str,
                Friend.friend_id == user_id_str,
            ),
        )
        stmt = select(Friend).where(condition)
        records = (await self.db.execute(stmt)).scalars().all()
        result = []
        for r in records:
            other = str(r.friend_id) if str(r.user_id) == user_id_str else str(r.user_id)
            result.append(other)
        return result
